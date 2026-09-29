# -*- coding: utf-8 -*-
import logging
from odoo import _, models, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ScrapDashboard(models.Model):
    _name = 'scrap.dashboard'
    _description = 'Scrap Dashboard'

    @api.model
    def get_default_config(self):
        """Get the default scrap configuration plus what the inventory mode needs."""
        config = self.env['scrap.barcode.config'].get_default_config()
        product_obj = self.env['product.product']
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        config.update({
            'can_inventory': product_obj._quick_inventory_can_edit(),
            'warehouse_name': warehouse.name or '',
            'uid': self.env.uid,
        })
        return config

    @api.model
    def _product_payload(self, product, quantity, barcode_type):
        """Product info for one scanned barcode (same shape for pieces and weight)."""
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        qty_warehouse = (
            product.with_context(warehouse_id=warehouse.id).qty_available if warehouse else product.qty_available
        )
        return {
            'success': True,
            'product_id': product.id,
            'product_name': product.display_name,
            'product_uom': product.uom_id.name,
            'product_uom_id': product.uom_id.id,
            'quantity': quantity,
            'barcode_type': barcode_type,
            'unit_price': product.list_price,
            'tracking': product.tracking,
            'is_storable': product.is_storable,
            'qty_available': product.qty_available,
            'qty_warehouse': qty_warehouse,
            'image_url': '/web/image/product.product/%d/image_128' % product.id,
        }

    @api.model
    def get_scrap_reason_tags(self):
        """Return all available scrap reason tags."""
        tags = self.env['stock.scrap.reason.tag'].search([])
        return [{'id': tag.id, 'name': tag.name} for tag in tags]

    @api.model
    def parse_barcode(self, barcode):
        """
        Parse a barcode using Odoo's barcode nomenclature.
        Returns product info with quantity (weight or 1 for pieces).
        """
        product_obj = self.env['product.product']

        # Try to use barcode nomenclature for weight-embedded barcodes
        nomenclature = self.env['barcode.nomenclature'].search([], limit=1)
        parsed = None
        if nomenclature:
            try:
                parsed = nomenclature.parse_barcode(barcode)
            except Exception:
                parsed = None

        if parsed and parsed.get('type') == 'weight':
            # Weight-embedded barcode: extract base code and weight
            base_code = parsed.get('base_code', barcode)
            weight = parsed.get('value', 0.0)

            product = product_obj.search([
                '|',
                ('barcode', '=', base_code),
                ('barcode', '=', barcode),
            ], limit=1)

            if not product:
                # Try matching by removing weight digits (common EAN-13 patterns)
                # The base_code from nomenclature should work, but try fuzzy match
                products = product_obj.search([])
                for p in products:
                    if p.barcode and nomenclature:
                        try:
                            p_parsed = nomenclature.parse_barcode(p.barcode)
                            if p_parsed.get('base_code') == base_code:
                                product = p
                                break
                        except Exception:
                            continue

            if product:
                return self._product_payload(product, weight if weight > 0 else 1.0, 'weight')

        # Standard barcode - search by exact match (pieces)
        product = product_obj.search([('barcode', '=', barcode)], limit=1)
        if product:
            return self._product_payload(product, 1.0, 'unit')

        return {
            'success': False,
            'error': _('Product not found for barcode: %s', barcode),
        }

    @api.model
    def get_product_stock(self, product_ids):
        """Get current stock quantities for given product IDs."""
        products = self.env['product.product'].browse(product_ids)
        result = {}
        for product in products:
            result[product.id] = {
                'product_id': product.id,
                'product_name': product.display_name,
                'qty_available': product.qty_available,
                'uom': product.uom_id.name,
                'unit_price': product.list_price,
                'value': product.qty_available * product.list_price,
            }
        return result

    @api.model
    def create_scrap_orders(self, scrap_lines, scrap_reason_tag_ids):
        """
        Create and confirm scrap orders for each product line.
        Quantities are capped at current stock to prevent negative inventory.

        :param scrap_lines: list of dicts with keys:
            - product_id (int)
            - quantity (float)
            - product_uom_id (int)
        :param scrap_reason_tag_ids: list of int IDs of stock.scrap.reason.tag
        :return: dict with created scrap order info and post-scrap stock
        """
        if not scrap_lines:
            raise UserError(_("No products to scrap."))

        scrap_obj = self.env['stock.scrap']
        created_scraps = []
        product_ids = []
        skipped = []

        # Get default scrap location
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1
        )
        location_id = warehouse.lot_stock_id if warehouse else False

        for line in scrap_lines:
            product = self.env['product.product'].browse(line['product_id'])
            if not product.exists():
                _logger.warning("Product ID %s not found, skipping.", line['product_id'])
                continue

            # Cap quantity at available stock — never go negative
            qty_available = product.qty_available
            scrap_qty = min(line['quantity'], qty_available)
            if scrap_qty <= 0:
                skipped.append(product.display_name)
                _logger.info(
                    "Product %s has no stock (%.3f), skipping scrap.",
                    product.display_name, qty_available
                )
                continue

            vals = {
                'product_id': product.id,
                'product_uom_id': line.get('product_uom_id', product.uom_id.id),
            }

            # Set scrap reason tags
            if scrap_reason_tag_ids:
                vals['scrap_reason_tag_ids'] = [(6, 0, scrap_reason_tag_ids)]

            if location_id:
                vals['location_id'] = location_id.id

            scrap_order = scrap_obj.create(vals)
            # Use write() to force DB flush of scrap_qty — direct assignment
            # may not persist before action_validate reads the stock move qty.
            # Also invalidate cache to ensure action_validate sees fresh value.
            scrap_order.write({'scrap_qty': scrap_qty})
            self.env.invalidate_all()

            # Validate immediately so the stock move uses the correct qty
            # before any subsequent create can trigger field recomputes
            scrap_order.action_validate()

            product_ids.append(product.id)
            created_scraps.append({
                'id': scrap_order.id,
                'name': scrap_order.name,
                'product_name': product.display_name,
                'quantity': scrap_qty,
                'uom': product.uom_id.name,
            })

        # Get updated stock levels
        unique_product_ids = list(set(product_ids))
        stock_after = self.get_product_stock(unique_product_ids)

        return {
            'success': True,
            'scraps': created_scraps,
            'stock_after': stock_after,
            'scrap_count': len(created_scraps),
            'skipped': skipped,
        }

    # ------------------------------------------------------------------
    # Inventory mode
    # ------------------------------------------------------------------
    @api.model
    def get_inventory_stock(self, product_ids):
        """Current on-hand quantity in the main warehouse: {product id: qty}."""
        products = self.env['product.product'].browse(product_ids).exists()
        return products._quick_inventory_on_hand()

    @api.model
    def apply_inventory(self, inventory_lines):
        """Inventory count from the barcode dashboard: the counted quantity becomes the new stock.

        :param inventory_lines: list of dicts with product_id (int) and quantity (float);
            several lines of the same product are added up. Products that were not
            scanned are not changed.
        :return: dict with per-product results (qty_before, qty_after, difference)
        """
        if not inventory_lines:
            raise UserError(_("No products to count."))
        counted = {}
        for line in inventory_lines:
            product_id = int(line['product_id'])
            counted[product_id] = counted.get(product_id, 0.0) + float(line.get('quantity') or 0.0)
        products = self.env['product.product'].browse(list(counted)).exists()
        skipped = products.filtered(lambda p: not p.is_storable)
        counted = {p.id: counted[p.id] for p in products - skipped}
        results = self.env['product.product']._quick_inventory_apply(counted, _("Inventory count (barcode)")) if counted else []
        warehouse = self.env['product.product']._quick_inventory_warehouse()
        return {
            'success': True,
            'lines': results,
            'changed_count': len([r for r in results if r['difference']]),
            'skipped': skipped.mapped('display_name'),
            'warehouse_name': warehouse.name,
        }
