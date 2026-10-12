# -*- coding: utf-8 -*-
import logging
from odoo import _, Command, models, api
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

    # ------------------------------------------------------------------
    # Scrapping
    # ------------------------------------------------------------------
    @api.model
    def create_scrap_moves(self, scrap_lines, scrap_reason_tag_ids):
        """Scrap the scanned products: one done scrap move per product line.

        Odoo 20 has no stock.scrap any more: a scrap is a stock.move with is_scrap,
        from the stock location of the main warehouse to the scrap location of the
        company, reference from the stock.scrap sequence (SP/xxxxx) and the reason
        tags in scrap_reason_tag_ids (the same as Inventory › Operations › Adjustments › Scrap creates).
        Quantities are capped at the stock of the warehouse stock location (with its
        sub-locations) so the stock never goes negative; products without stock are
        skipped.

        :param scrap_lines: list of dicts with keys:
            - product_id (int)
            - quantity (float)
            - product_uom_id (int, optional; the product unit otherwise)
        :param scrap_reason_tag_ids: list of int IDs of stock.scrap.reason.tag
        :return: dict with the scrap moves and the stock after the scrap
        """
        if not scrap_lines:
            raise UserError(_("No products to scrap."))

        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        if not warehouse:
            raise UserError(_("No warehouse found for company %s.", self.env.company.display_name))
        location = warehouse.lot_stock_id
        scrap_location = self.env.company.scrap_location_id
        tags = self.env['stock.scrap.reason.tag'].browse(scrap_reason_tag_ids or []).exists()

        created_scraps = []
        product_ids = []
        skipped = []
        for line in scrap_lines:
            product = self.env['product.product'].browse(line['product_id'])
            if not product.exists():
                _logger.warning("Product ID %s not found, skipping.", line['product_id'])
                continue
            uom = self.env['uom.uom'].browse(line.get('product_uom_id') or product.uom_id.id).exists() or product.uom_id

            # Cap quantity at the stock of the location (sub-locations included) — never go negative.
            # Products that do not track stock have none, so they are skipped as in Odoo 19.
            requested = uom._compute_quantity(float(line.get('quantity') or 0.0), product.uom_id)
            qty_available = product.with_context(location=location.id).qty_available
            scrap_qty = product.uom_id.round(min(requested, qty_available))
            if product.uom_id.compare(scrap_qty, 0.0) <= 0:
                skipped.append(product.display_name)
                _logger.info(
                    "Product %s has no stock (%.3f), skipping scrap.",
                    product.display_name, qty_available,
                )
                continue

            move = self.env['stock.move'].create({
                'is_scrap': True,
                'product_id': product.id,
                'uom_id': product.uom_id.id,
                'product_uom_qty': scrap_qty,
                'quantity': scrap_qty,
                'location_id': location.id,
                'location_dest_id': scrap_location.id,
                'company_id': self.env.company.id,
                'scrap_reason_tag_ids': [Command.set(tags.ids)],
            })
            # The availability is checked above, so no "Insufficient Quantity" wizard here
            # (action_scrap would return it instead of scrapping).
            move._action_scrap()

            product_ids.append(product.id)
            created_scraps.append({
                'id': move.id,
                'name': move.reference,
                'product_name': product.display_name,
                'quantity': move.quantity,
                'uom': product.uom_id.name,
                'capped': product.uom_id.compare(scrap_qty, requested) < 0,
            })

        unique_product_ids = list(dict.fromkeys(product_ids))
        stock_after = self.get_product_stock(unique_product_ids)

        return {
            'success': True,
            'scraps': created_scraps,
            'stock_after': stock_after,
            'scrap_count': len(created_scraps),
            'skipped': skipped,
        }

    @api.model
    def action_open_scraps(self, move_ids):
        """Scrap form (one move) or list (several) of the given scrap moves, as Inventory › Scrap shows them."""
        moves = self.env['stock.move'].browse(move_ids).exists().filtered('is_scrap')
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Scraps"),
            'res_model': 'stock.move',
            'context': {'default_is_scrap': True, 'create': False},
        }
        form_view = self.env.ref('stock.view_scrap_move_form')
        if len(moves) == 1:
            action.update({
                'res_id': moves.id,
                'views': [(form_view.id, 'form')],
                'target': 'new',
            })
        else:
            action.update({
                'domain': [('id', 'in', moves.ids)],
                'views': [(self.env.ref('stock.view_scrap_move_list').id, 'list'), (form_view.id, 'form')],
                'target': 'current',
            })
        return action

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
