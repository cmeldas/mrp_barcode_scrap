# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class StockQuickUpdate(models.TransientModel):
    _name = "stock.quick.update"
    _description = "Quick Stock Update"

    warehouse_id = fields.Many2one("stock.warehouse", string="Warehouse", readonly=True)
    zero = fields.Boolean(
        string="Set to Zero", readonly=True,
        help="Opened from the list action Set Stock to Zero: new stock prefilled with 0.",
    )
    line_ids = fields.One2many("stock.quick.update.line", "wizard_id", string="Products")

    @api.model
    def create_for_products(self, products, zero=False):
        """Wizard with one line per product, new quantity prefilled with the current one (or 0)."""
        warehouse = products._quick_inventory_warehouse()
        on_hand = products._quick_inventory_on_hand(warehouse)
        return self.create({
            "warehouse_id": warehouse.id,
            "zero": zero,
            "line_ids": [
                (0, 0, {
                    "product_id": product.id,
                    "qty_current": on_hand[product.id],
                    "qty_new": 0.0 if zero else on_hand[product.id],
                })
                for product in products
            ],
        })

    def action_apply(self):
        self.ensure_one()
        changed = self.line_ids.filtered(
            lambda l: l.product_id.uom_id.compare(l.qty_new, l.qty_current) != 0
        )
        if changed:
            self.env["product.product"]._quick_inventory_apply(
                {line.product_id.id: line.qty_new for line in changed},
                _("Stock set to zero") if self.zero else _("Stock updated manually"),
            )
            summary = ", ".join(
                "%s %g → %g %s" % (l.product_id.display_name, l.qty_current, l.qty_new, l.uom_id.name) for l in changed
            )
            message = _("Stock set to zero: %s", summary) if self.zero else _("Stock updated: %s", summary)
        else:
            message = _("Nothing changed.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if changed else "info",
                "message": message,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class StockQuickUpdateLine(models.TransientModel):
    _name = "stock.quick.update.line"
    _description = "Quick Stock Update Line"

    wizard_id = fields.Many2one("stock.quick.update", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Product", required=True, readonly=True)
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    qty_current = fields.Float(string="Current Stock", digits="Product Unit", readonly=True)
    qty_new = fields.Float(string="New Stock", digits="Product Unit")
    qty_difference = fields.Float(string="Difference", digits="Product Unit", compute="_compute_qty_difference")

    @api.depends("qty_new", "qty_current")
    def _compute_qty_difference(self):
        for line in self:
            line.qty_difference = line.qty_new - line.qty_current
