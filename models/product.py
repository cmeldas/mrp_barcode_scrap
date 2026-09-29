# -*- coding: utf-8 -*-
"""Quick stock update: set the on-hand quantity of products in the main warehouse.

Shared by the product form buttons (Update Stock / Set Stock to Zero) and the
inventory mode of the barcode dashboard. Uses the standard Odoo inventory
adjustment (stock.quant inventory mode + _apply_inventory), so every change is
a regular inventory move from/to the inventory loss location, visible in the
moves history with its own reference.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero


class ProductProduct(models.Model):
    _inherit = "product.product"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _quick_inventory_warehouse(self):
        """Main warehouse of the current company (the one used for scrapping too)."""
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
        if not warehouse:
            raise UserError(_("No warehouse found for company %s.", self.env.company.display_name))
        return warehouse

    @api.model
    def _quick_inventory_can_edit(self):
        return self.env.user.has_group("stock.group_stock_manager")

    @api.model
    def _quick_inventory_check_access(self):
        if not self._quick_inventory_can_edit():
            raise AccessError(_("Only inventory administrators can change stock quantities."))

    def _quick_inventory_on_hand(self, warehouse=None):
        """On-hand quantity of each product in the whole warehouse: {product id: qty}.

        Computed on a clean context: a location search in the product list (search_location)
        or lot/owner/package/date keys would otherwise narrow the quantity, while the change
        is always applied to the whole warehouse.
        """
        warehouse = warehouse or self._quick_inventory_warehouse()
        products = self.with_context(
            warehouse_id=warehouse.id, search_warehouse=False, location=False, search_location=False,
            lot_id=None, owner_id=None, package_id=None, from_date=False, to_date=False,
        )
        products.invalidate_recordset(["qty_available"])
        return {product.id: product.qty_available for product in products}

    def _quick_inventory_quants(self, warehouse):
        """All quants of this product in the internal locations of the warehouse."""
        self.ensure_one()
        return self.env["stock.quant"].with_context(inventory_mode=True).search([
            ("product_id", "=", self.id),
            ("location_id", "child_of", warehouse.view_location_id.id),
            ("location_id.usage", "=", "internal"),
        ])

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------
    @api.model
    def _quick_inventory_apply(self, quantities, inventory_name):
        """Set the on-hand quantity of products in the main warehouse.

        :param quantities: {product.product id: new on-hand quantity}
        :param inventory_name: reference of the created inventory moves
        :return: list of dicts per product: product_id, product_name, uom,
            qty_before, qty_after, difference
        """
        self._quick_inventory_check_access()
        warehouse = self._quick_inventory_warehouse()
        products = self.browse(list(quantities)).exists()
        not_storable = products.filtered(lambda p: not p.is_storable)
        if not_storable:
            raise UserError(_(
                "These products do not track stock: %s",
                ", ".join(not_storable.mapped("display_name")),
            ))
        before = products._quick_inventory_on_hand(warehouse)
        Quant = self.env["stock.quant"].with_context(inventory_mode=True, inventory_name=inventory_name)
        to_apply = Quant
        for product in products:
            rounding = product.uom_id.rounding
            target = product.uom_id.round(quantities[product.id] or 0.0)
            if float_compare(target, 0.0, precision_rounding=rounding) < 0:
                raise UserError(_("The quantity of %s cannot be negative.", product.display_name))
            if product.tracking != "none" and not float_is_zero(target, precision_rounding=rounding):
                raise UserError(_(
                    "%s is tracked by lots or serial numbers. Count it in Inventory › Operations › Adjustments › Physical Inventory.",
                    product.display_name,
                ))
            quants = product._quick_inventory_quants(warehouse)
            main = quants.filtered(
                lambda q: q.location_id == warehouse.lot_stock_id and not q.lot_id and not q.package_id and not q.owner_id
            )
            # Everything outside the main quant goes to zero, the main quant gets the whole target.
            for quant in quants - main[:1]:
                if not float_is_zero(quant.quantity, precision_rounding=rounding):
                    quant.inventory_quantity = 0.0
                    to_apply |= quant
            if main:
                if float_compare(main[0].quantity, target, precision_rounding=rounding) != 0:
                    main[0].inventory_quantity = target
                    to_apply |= main[0]
            elif not float_is_zero(target, precision_rounding=rounding):
                to_apply |= Quant.create({
                    "product_id": product.id,
                    "location_id": warehouse.lot_stock_id.id,
                    "inventory_quantity": target,
                })
        if to_apply:
            to_apply._apply_inventory()
        after = products._quick_inventory_on_hand(warehouse)
        return [
            {
                "product_id": product.id,
                "product_name": product.display_name,
                "uom": product.uom_id.name,
                "unit_price": product.list_price,
                "qty_before": before[product.id],
                "qty_after": after[product.id],
                "difference": after[product.id] - before[product.id],
            }
            for product in products
        ]

    # ------------------------------------------------------------------
    # Form buttons
    # ------------------------------------------------------------------
    def _quick_inventory_products(self):
        return self.filtered(lambda p: p.is_storable)

    def action_quick_stock_update(self):
        """Update Stock: dialog with the current and the new on-hand quantity per product."""
        self._quick_inventory_check_access()
        products = self._quick_inventory_products()
        if not products:
            raise UserError(_("This product does not track stock."))
        wizard = self.env["stock.quick.update"].create_for_products(products)
        return {
            "type": "ir.actions.act_window",
            "name": _("Update Stock"),
            "res_model": "stock.quick.update",
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def action_quick_stock_zero_bulk(self):
        """List action Set Stock to Zero: dialog with the selected products that have stock, new stock 0.

        Unlike the form button (which asks a simple question), the list action shows every
        product that is going to change, so a wrong selection is visible before applying.
        """
        self._quick_inventory_check_access()
        products = self._quick_inventory_products()
        if not products:
            raise UserError(_("None of the selected products tracks stock."))
        warehouse = self._quick_inventory_warehouse()
        on_hand = products._quick_inventory_on_hand(warehouse)
        with_stock = products.filtered(lambda p: not p.uom_id.is_zero(on_hand[p.id]))
        if not with_stock:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"type": "info", "message": _("All selected products already have zero stock.")},
            }
        wizard = self.env["stock.quick.update"].create_for_products(with_stock, zero=True)
        return {
            "type": "ir.actions.act_window",
            "name": _("Set Stock to Zero"),
            "res_model": "stock.quick.update",
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def action_quick_stock_zero(self):
        """Set Stock to Zero: on-hand quantity of the product (all its variants) to 0 right away."""
        self._quick_inventory_check_access()
        products = self._quick_inventory_products()
        if not products:
            raise UserError(_("This product does not track stock."))
        results = self._quick_inventory_apply({p.id: 0.0 for p in products}, _("Stock set to zero"))
        changed = [r for r in results if r["difference"]]
        if changed:
            message = _("Stock set to zero: %s", ", ".join(
                "%s (%g → 0 %s)" % (r["product_name"], r["qty_before"], r["uom"]) for r in changed
            ))
        else:
            message = _("Stock was already zero.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": message,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _quick_inventory_variants(self):
        """Storable variants of the templates, archived ones included where they matter.

        An archived template has only archived variants (product_variant_ids would be empty),
        and those are typically what needs zeroing: products no longer sold with leftover,
        often negative, stock. On an active template an archived variant is included only
        while it still has stock.
        """
        variants = self.with_context(active_test=False).product_variant_ids.filtered(lambda p: p.is_storable)
        archived = variants.filtered(lambda p: not p.active and p.product_tmpl_id.active)
        if archived:
            on_hand = archived._quick_inventory_on_hand()
            variants -= archived.filtered(lambda p: p.uom_id.is_zero(on_hand[p.id]))
        return variants.with_env(self.env)

    def action_quick_stock_update(self):
        return self._quick_inventory_variants().action_quick_stock_update()

    def action_quick_stock_zero(self):
        return self._quick_inventory_variants().action_quick_stock_zero()

    def action_quick_stock_zero_bulk(self):
        return self._quick_inventory_variants().action_quick_stock_zero_bulk()
