# -*- coding: utf-8 -*-
"""Quick stock update: Update Stock / Set Stock to Zero buttons and the dashboard inventory count."""
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestQuickInventory(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("stock.group_stock_manager")
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.shelf = cls.env["stock.location"].create({"name": "Regál", "location_id": cls.stock.id, "usage": "internal"})
        cls.Product = cls.env["product.product"]
        cls.Quant = cls.env["stock.quant"]
        cls.jam = cls.Product.create({
            "name": "Marmeláda", "type": "consu", "is_storable": True, "barcode": "8590000000011", "list_price": 100.0,
        })
        cls.cheese = cls.Product.create({
            "name": "Sýr", "type": "consu", "is_storable": True, "barcode": "8590000000028", "list_price": 250.0,
        })
        cls.service = cls.Product.create({"name": "Degustace", "type": "service", "barcode": "8590000000035"})
        color = cls.env["product.attribute"].create({
            "name": "Příchuť",
            "value_ids": [Command.create({"name": "Jahoda"}), Command.create({"name": "Malina"})],
        })
        cls.template = cls.env["product.template"].create({
            "name": "Zmrzlina v kelímku", "type": "consu", "is_storable": True,
            "attribute_line_ids": [Command.create({"attribute_id": color.id, "value_ids": [Command.set(color.value_ids.ids)]})],
        })
        cls.variant_a, cls.variant_b = cls.template.product_variant_ids

    def _on_hand(self, product):
        return product._quick_inventory_on_hand(self.warehouse)[product.id]

    def _set(self, product, location, qty):
        self.Quant._update_available_quantity(product, location, qty)

    # ------------------------------------------------------------------
    def test_set_quantity_from_negative_stock(self):
        self._set(self.jam, self.stock, -28)
        self.assertEqual(self._on_hand(self.jam), -28)
        result = self.Product._quick_inventory_apply({self.jam.id: 3.0}, "Test inventura")
        self.assertEqual(self._on_hand(self.jam), 3)
        self.assertEqual(result[0]["qty_before"], -28)
        self.assertEqual(result[0]["qty_after"], 3)
        self.assertEqual(result[0]["difference"], 31)
        move = self.env["stock.move"].search([("product_id", "=", self.jam.id), ("is_inventory", "=", True)], order="id desc", limit=1)
        self.assertEqual(move.state, "done")
        self.assertEqual(move.reference, "Test inventura", "pohyb má čitelnou referenci")
        self.assertEqual(move.location_dest_id, self.stock)

    def test_other_internal_locations_are_counted_too(self):
        """Stock on a sub-location counts into the product's stock, so the new total is exactly the count."""
        self._set(self.jam, self.stock, 5)
        self._set(self.jam, self.shelf, 7)
        self.assertEqual(self._on_hand(self.jam), 12)
        self.Product._quick_inventory_apply({self.jam.id: 4.0}, "Test")
        self.assertEqual(self._on_hand(self.jam), 4)
        shelf_qty = sum(self.Quant.search([("product_id", "=", self.jam.id), ("location_id", "=", self.shelf.id)]).mapped("quantity"))
        self.assertEqual(shelf_qty, 0)

    def test_zero_without_stock_creates_no_move(self):
        moves_before = self.env["stock.move"].search_count([("product_id", "=", self.cheese.id)])
        result = self.Product._quick_inventory_apply({self.cheese.id: 0.0}, "Test")
        self.assertEqual(result[0]["difference"], 0)
        self.assertEqual(self.env["stock.move"].search_count([("product_id", "=", self.cheese.id)]), moves_before)

    def test_same_quantity_is_no_change(self):
        self._set(self.cheese, self.stock, 2)
        moves_before = self.env["stock.move"].search_count([("product_id", "=", self.cheese.id)])
        self.Product._quick_inventory_apply({self.cheese.id: 2.0}, "Test")
        self.assertEqual(self.env["stock.move"].search_count([("product_id", "=", self.cheese.id)]), moves_before)

    def test_negative_target_and_not_storable_are_refused(self):
        with self.assertRaises(UserError):
            self.Product._quick_inventory_apply({self.jam.id: -1.0}, "Test")
        with self.assertRaises(UserError):
            self.Product._quick_inventory_apply({self.service.id: 1.0}, "Test")

    def test_tracked_product_only_zero(self):
        self.jam.product_tmpl_id.tracking = "lot"
        with self.assertRaises(UserError):
            self.Product._quick_inventory_apply({self.jam.id: 2.0}, "Test")
        lot = self.env["stock.lot"].create({"name": "L1", "product_id": self.jam.id})
        self.Quant._update_available_quantity(self.jam, self.stock, 3, lot_id=lot)
        self.Product._quick_inventory_apply({self.jam.id: 0.0}, "Test")
        self.assertEqual(self._on_hand(self.jam), 0, "vynulovat jde i produkt se šaržemi")

    def test_only_inventory_admin(self):
        user = new_test_user(self.env, login="skladnik", groups="base.group_user,stock.group_stock_user,mrp.group_mrp_user")
        with self.assertRaises(AccessError):
            self.Product.with_user(user)._quick_inventory_apply({self.jam.id: 1.0}, "Test")
        with self.assertRaises(AccessError):
            self.jam.with_user(user).action_quick_stock_zero()
        self.assertFalse(self.env["scrap.dashboard"].with_user(user).get_default_config()["can_inventory"])
        self.assertTrue(self.env["scrap.dashboard"].get_default_config()["can_inventory"])

    # ------------------------------------------------------------------
    def test_zero_button_template_all_variants(self):
        self._set(self.variant_a, self.stock, 4)
        self._set(self.variant_b, self.stock, -2)
        action = self.template.action_quick_stock_zero()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["next"]["tag"], "soft_reload")
        self.assertIn("→ 0", action["params"]["message"])
        self.assertIn(self.variant_a.display_name, action["params"]["message"])
        self.assertEqual(self._on_hand(self.variant_a), 0)
        self.assertEqual(self._on_hand(self.variant_b), 0)
        again = self.variant_a.action_quick_stock_zero()
        self.assertNotIn("→", again["params"]["message"], "podruhé už není co nulovat")

    def test_zero_button_variant_only_that_variant(self):
        self._set(self.variant_a, self.stock, 4)
        self._set(self.variant_b, self.stock, 6)
        self.variant_a.action_quick_stock_zero()
        self.assertEqual(self._on_hand(self.variant_a), 0)
        self.assertEqual(self._on_hand(self.variant_b), 6)

    def test_update_wizard_template_and_variant(self):
        self._set(self.variant_a, self.stock, 4)
        action = self.template.action_quick_stock_update()
        wizard = self.env["stock.quick.update"].browse(action["res_id"])
        self.assertEqual(action["target"], "new")
        self.assertEqual(wizard.line_ids.product_id, self.variant_a | self.variant_b)
        line_a = wizard.line_ids.filtered(lambda l: l.product_id == self.variant_a)
        self.assertEqual((line_a.qty_current, line_a.qty_new), (4, 4))
        line_a.qty_new = 10
        self.assertEqual(line_a.qty_difference, 6)
        result = wizard.action_apply()
        self.assertEqual(result["params"]["next"]["type"], "ir.actions.act_window_close")
        self.assertEqual(self._on_hand(self.variant_a), 10)
        self.assertEqual(self._on_hand(self.variant_b), 0)

        action = self.jam.action_quick_stock_update()
        wizard = self.env["stock.quick.update"].browse(action["res_id"])
        self.assertEqual(wizard.line_ids.product_id, self.jam)
        self.assertEqual(wizard.action_apply()["params"]["type"], "info", "beze změny se nic neúčtuje")

    def test_bulk_zero_list_action(self):
        """List action: dialog with the selected products that have stock, prefilled 0, then zero them all."""
        self._set(self.jam, self.stock, 5)
        self._set(self.cheese, self.stock, -3)
        self._set(self.variant_a, self.stock, 2)
        templates = self.jam.product_tmpl_id | self.cheese.product_tmpl_id | self.template | self.service.product_tmpl_id
        action = templates.action_quick_stock_zero_bulk()
        self.assertEqual(action["res_model"], "stock.quick.update")
        wizard = self.env["stock.quick.update"].browse(action["res_id"])
        self.assertTrue(wizard.zero)
        self.assertEqual(
            wizard.line_ids.product_id, self.jam | self.cheese | self.variant_a,
            "jen vybrané produkty se skladem; varianta bez skladu a služba chybí",
        )
        self.assertEqual(set(wizard.line_ids.mapped("qty_new")), {0.0})
        # obsluha jeden řádek z dialogu odebere, ten se nevynuluje
        wizard.line_ids.filtered(lambda l: l.product_id == self.cheese).unlink()
        wizard.action_apply()
        self.assertEqual(self._on_hand(self.jam), 0)
        self.assertEqual(self._on_hand(self.variant_a), 0)
        self.assertEqual(self._on_hand(self.cheese), -3)
        move = self.env["stock.move"].search([("product_id", "=", self.jam.id), ("is_inventory", "=", True)], order="id desc", limit=1)
        self.assertEqual(move.reference, "Stock set to zero")

        again = (self.jam | self.variant_a).action_quick_stock_zero_bulk()
        self.assertEqual(again["tag"], "display_notification", "když není co nulovat, jen hláška")
        with self.assertRaises(UserError):
            self.service.action_quick_stock_zero_bulk()
        for xmlid in ("action_product_template_quick_stock_zero", "action_product_product_quick_stock_zero"):
            server_action = self.env.ref(f"mrp_barcode_scrap.{xmlid}")
            self.assertIn("list", server_action.binding_view_types)
            self.assertEqual(server_action.group_ids, self.env.ref("stock.group_stock_manager"))

    def test_archived_template_can_be_zeroed(self):
        """Products no longer sold get archived with leftover (often negative) stock; exactly those need zeroing."""
        self._set(self.jam, self.stock, -7)
        self.jam.product_tmpl_id.action_archive()
        self.assertFalse(self.jam.active)
        action = self.jam.product_tmpl_id.action_quick_stock_zero_bulk()
        wizard = self.env["stock.quick.update"].browse(action["res_id"])
        self.assertEqual(wizard.line_ids.product_id, self.jam)
        wizard.action_apply()
        self.assertEqual(self._on_hand(self.jam), 0)
        self._set(self.jam, self.stock, 2)
        self.jam.product_tmpl_id.action_quick_stock_zero()
        self.assertEqual(self._on_hand(self.jam), 0)
        action = self.jam.product_tmpl_id.action_quick_stock_update()
        self.assertEqual(self.env["stock.quick.update"].browse(action["res_id"]).line_ids.product_id, self.jam)

    def test_archived_variant_of_active_template_only_with_stock(self):
        self._set(self.variant_b, self.stock, 3)
        self.variant_b.action_archive()
        wizard = self.env["stock.quick.update"].browse(self.template.action_quick_stock_update()["res_id"])
        self.assertEqual(wizard.line_ids.product_id, self.variant_a | self.variant_b, "archivovaná varianta se skladem se ukáže")
        self.template.action_quick_stock_zero()
        self.assertEqual(self._on_hand(self.variant_b), 0)
        wizard = self.env["stock.quick.update"].browse(self.template.action_quick_stock_update()["res_id"])
        self.assertEqual(wizard.line_ids.product_id, self.variant_a, "bez skladu už archivovaná varianta nepřekáží")

    def test_location_search_context_does_not_change_quantities(self):
        """A Location search in the product list puts search_location into the context; the whole warehouse still counts."""
        self._set(self.jam, self.stock, -5)
        self._set(self.jam, self.shelf, 2)
        self._set(self.cheese, self.stock, 4)
        products = (self.jam | self.cheese).with_context(search_location=self.shelf.id)
        wizard = self.env["stock.quick.update"].browse(products.action_quick_stock_zero_bulk()["res_id"])
        self.assertEqual(wizard.line_ids.product_id, self.jam | self.cheese)
        self.assertEqual(wizard.line_ids.filtered(lambda l: l.product_id == self.jam).qty_current, -3)
        message = self.jam.with_context(search_location=self.shelf.id).action_quick_stock_zero()["params"]["message"]
        self.assertIn("-3 → 0", message)
        self.assertEqual(self._on_hand(self.jam), 0)

    def test_bulk_zero_dialog_new_stock_readonly(self):
        arch = self.env["stock.quick.update"].get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('readonly="parent.zero"', arch)

    def test_variant_form_zero_question_says_variant(self):
        arch = self.env["product.product"].get_views([(self.env.ref("product.product_normal_form_view").id, "form")])["views"]["form"]["arch"]
        self.assertIn("this variant to 0", arch)
        self.assertNotIn("all its variants", arch)

    def test_unknown_barcode_message(self):
        result = self.env["scrap.dashboard"].parse_barcode("0000000000000")
        self.assertFalse(result["success"])
        self.assertIn("0000000000000", result["error"])

    def test_buttons_refuse_not_storable(self):
        with self.assertRaises(UserError):
            self.service.action_quick_stock_update()
        with self.assertRaises(UserError):
            self.service.action_quick_stock_zero()

    # ------------------------------------------------------------------
    def test_dashboard_inventory_count(self):
        self._set(self.jam, self.stock, 10)
        self._set(self.cheese, self.stock, -3)
        Dashboard = self.env["scrap.dashboard"]
        scan = Dashboard.parse_barcode(self.jam.barcode)
        self.assertTrue(scan["is_storable"])
        self.assertEqual(scan["qty_warehouse"], 10)
        self.assertFalse(Dashboard.parse_barcode(self.service.barcode)["is_storable"])
        result = Dashboard.apply_inventory([
            {"product_id": self.jam.id, "quantity": 2},
            {"product_id": self.jam.id, "quantity": 1.5},  # stejný produkt podruhé se přičte
            {"product_id": self.cheese.id, "quantity": 0},
            {"product_id": self.service.id, "quantity": 1},
        ])
        self.assertEqual(self._on_hand(self.jam), 3.5)
        self.assertEqual(self._on_hand(self.cheese), 0)
        self.assertEqual(result["changed_count"], 2)
        self.assertEqual(result["skipped"], [self.service.display_name])
        by_product = {r["product_id"]: r for r in result["lines"]}
        self.assertEqual(by_product[self.jam.id]["difference"], -6.5)
        self.assertEqual(by_product[self.cheese.id]["difference"], 3)
        self.assertEqual(Dashboard.get_inventory_stock([self.jam.id])[self.jam.id], 3.5)
        with self.assertRaises(UserError):
            Dashboard.apply_inventory([])

    def test_unscanned_products_unchanged(self):
        self._set(self.jam, self.stock, 10)
        self._set(self.cheese, self.stock, 5)
        self.env["scrap.dashboard"].apply_inventory([{"product_id": self.jam.id, "quantity": 1}])
        self.assertEqual(self._on_hand(self.cheese), 5)
