# -*- coding: utf-8 -*-
"""Scrapping from the barcode dashboard: Odoo 20 scrap = stock.move with is_scrap (no stock.scrap)."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestScrapDashboard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # References and messages are compared in English, also in a database with Czech loaded
        cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))
        cls.Dashboard = cls.env["scrap.dashboard"]
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.shelf = cls.env["stock.location"].create({"name": "Regál", "location_id": cls.stock.id, "usage": "internal"})
        cls.scrap_location = cls.env.company.scrap_location_id
        cls.Quant = cls.env["stock.quant"]
        cls.jam = cls.env["product.product"].create({
            "name": "Marmeláda", "type": "consu", "is_storable": True, "barcode": "8590000000011", "list_price": 100.0,
        })
        cls.cheese = cls.env["product.product"].create({
            "name": "Sýr", "type": "consu", "is_storable": True, "barcode": "8590000000028", "list_price": 250.0,
        })
        cls.service = cls.env["product.product"].create({"name": "Degustace", "type": "service"})
        cls.expired = cls.env["stock.scrap.reason.tag"].create({"name": "Prošlá trvanlivost (test)"})

    def _set(self, product, location, qty):
        self.Quant._update_available_quantity(product, location, qty)

    def _on_hand(self, product, location=None):
        return product.with_context(location=(location or self.stock).id).qty_available

    def _scrap_moves(self, product):
        return self.env["stock.move"].search([("product_id", "=", product.id), ("is_scrap", "=", True)])

    # ------------------------------------------------------------------
    def test_scrap_creates_done_scrap_moves(self):
        self._set(self.jam, self.stock, 10)
        self._set(self.cheese, self.stock, 3)
        result = self.Dashboard.create_scrap_moves([
            {"product_id": self.jam.id, "quantity": 2, "product_uom_id": self.jam.uom_id.id},
            {"product_id": self.cheese.id, "quantity": 5, "product_uom_id": self.cheese.uom_id.id},
        ], [self.expired.id])
        self.assertTrue(result["success"])
        self.assertEqual(result["scrap_count"], 2)
        self.assertEqual(result["skipped"], [])

        jam_move = self._scrap_moves(self.jam)
        self.assertEqual(len(jam_move), 1, "jeden pohyb vyřazení na produkt")
        self.assertEqual(jam_move.state, "done")
        self.assertEqual(jam_move.quantity, 2)
        self.assertEqual(jam_move.product_uom_qty, 2)
        self.assertEqual(jam_move.location_id, self.stock)
        self.assertEqual(jam_move.location_dest_id, self.scrap_location)
        self.assertEqual(jam_move.location_dest_id.usage, "inventory")
        self.assertEqual(jam_move.scrap_reason_tag_ids, self.expired)
        self.assertTrue(jam_move.reference.startswith("SP/"), "reference z číselné řady vyřazení")
        self.assertFalse(jam_move.picking_id)

        by_product = {s["product_name"]: s for s in result["scraps"]}
        self.assertEqual(by_product[self.jam.display_name]["id"], jam_move.id)
        self.assertEqual(by_product[self.jam.display_name]["name"], jam_move.reference)
        self.assertFalse(by_product[self.jam.display_name]["capped"])
        self.assertEqual(by_product[self.cheese.display_name]["quantity"], 3, "omezeno skladem")
        self.assertTrue(by_product[self.cheese.display_name]["capped"])

        self.assertEqual(self._on_hand(self.jam), 8)
        self.assertEqual(self._on_hand(self.cheese), 0, "sklad nejde do minusu")
        self.assertEqual(result["stock_after"][self.jam.id]["qty_available"], 8)
        self.assertEqual(result["stock_after"][self.cheese.id]["value"], 0)

    def test_scrap_without_reason(self):
        self._set(self.jam, self.stock, 1)
        self.Dashboard.create_scrap_moves([{"product_id": self.jam.id, "quantity": 1}], [])
        move = self._scrap_moves(self.jam)
        self.assertEqual(move.state, "done")
        self.assertFalse(move.scrap_reason_tag_ids)

    def test_scrap_skips_products_without_stock(self):
        self._set(self.cheese, self.stock, -2)
        result = self.Dashboard.create_scrap_moves([
            {"product_id": self.jam.id, "quantity": 1},
            {"product_id": self.cheese.id, "quantity": 1},
            {"product_id": self.service.id, "quantity": 1},
        ], [])
        self.assertEqual(result["scrap_count"], 0)
        self.assertEqual(set(result["skipped"]), {self.jam.display_name, self.cheese.display_name, self.service.display_name})
        self.assertFalse(self.env["stock.move"].search_count([("is_scrap", "=", True), ("product_id", "in", (self.jam | self.cheese | self.service).ids)]))
        with self.assertRaises(UserError):
            self.Dashboard.create_scrap_moves([], [])

    def test_scrap_from_sub_location(self):
        """Stock on a sub-location of WH/Stock is scrapped from there, no negative quant on WH/Stock."""
        self._set(self.jam, self.shelf, 4)
        result = self.Dashboard.create_scrap_moves([{"product_id": self.jam.id, "quantity": 3}], [])
        self.assertEqual(result["scraps"][0]["quantity"], 3)
        self.assertEqual(self._on_hand(self.jam, self.shelf), 1)
        stock_quants = self.Quant.search([("product_id", "=", self.jam.id), ("location_id", "=", self.stock.id)])
        self.assertFalse(any(q.quantity < 0 for q in stock_quants))
        self.assertEqual(self._scrap_moves(self.jam).move_line_ids.location_id, self.shelf)

    def test_scrap_lot_tracked_product(self):
        """Odoo 20 reserves the lot itself (removal strategy): the scrap takes the stocked lot,
        no quant without a lot goes negative (19 booked the scrap without a lot)."""
        honey = self.env["product.product"].create({
            "name": "Med (šarže)", "type": "consu", "is_storable": True, "tracking": "lot",
        })
        lot = self.env["stock.lot"].create({"name": "MED-1", "product_id": honey.id})
        self.Quant._update_available_quantity(honey, self.stock, 5, lot_id=lot)
        result = self.Dashboard.create_scrap_moves([{"product_id": honey.id, "quantity": 2}], [])
        self.assertEqual(result["scrap_count"], 1)
        move = self._scrap_moves(honey)
        self.assertEqual(move.state, "done")
        self.assertEqual(move.move_line_ids.lot_id, lot)
        quants = self.Quant.search([("product_id", "=", honey.id), ("location_id", "=", self.stock.id)])
        self.assertEqual(quants.filtered(lambda q: q.lot_id == lot).quantity, 3)
        self.assertFalse(quants.filtered(lambda q: not q.lot_id and q.quantity < 0))

    def test_scrap_moves_in_scrap_menu_and_open_action(self):
        self._set(self.jam, self.stock, 5)
        self._set(self.cheese, self.stock, 5)
        result = self.Dashboard.create_scrap_moves([
            {"product_id": self.jam.id, "quantity": 1},
            {"product_id": self.cheese.id, "quantity": 1},
        ], [self.expired.id])
        move_ids = [s["id"] for s in result["scraps"]]
        menu_action = self.env.ref("stock.stock_scrapped_moves_action")
        in_menu = self.env["stock.move"].search(safe_eval(menu_action.domain))
        self.assertTrue(set(move_ids) <= set(in_menu.ids), "vyřazení jsou vidět v Sklad › Vyřazení")

        one = self.Dashboard.action_open_scraps(move_ids[:1])
        self.assertEqual(one["res_model"], "stock.move")
        self.assertEqual(one["res_id"], move_ids[0])
        self.assertEqual(one["views"], [(self.env.ref("stock.view_scrap_move_form").id, "form")])
        both = self.Dashboard.action_open_scraps(move_ids)
        self.assertEqual(both["domain"], [("id", "in", move_ids)])
        self.assertEqual(both["views"][0], (self.env.ref("stock.view_scrap_move_list").id, "list"))

    def test_scrap_as_manufacturing_user(self):
        """The dashboard is in Manufacturing; a manufacturing user (not inventory admin) can scrap."""
        user = new_test_user(self.env, login="vyroba", groups="base.group_user,mrp.group_mrp_user")
        self._set(self.jam, self.stock, 2)
        result = self.Dashboard.with_user(user).create_scrap_moves([{"product_id": self.jam.id, "quantity": 1}], [self.expired.id])
        self.assertEqual(result["scrap_count"], 1)
        self.assertEqual(self._on_hand(self.jam), 1)
        self.assertIn(self.expired.id, [tag["id"] for tag in self.Dashboard.with_user(user).get_scrap_reason_tags()])

    def test_default_reason_from_config(self):
        config = self.env.ref("mrp_barcode_scrap.default_scrap_barcode_config")
        config.default_scrap_reason_tag_id = self.expired
        self.assertEqual(self.Dashboard.get_default_config()["default_scrap_reason_tag_id"], self.expired.id)

    def test_dashboard_client_action(self):
        action = self.env.ref("mrp_barcode_scrap.action_scrap_barcode_dashboard")
        self.assertEqual(action.tag, "mrp_barcode_scrap_dashboard")
        menu = self.env.ref("mrp_barcode_scrap.menu_mrp_barcode_scrap_root")
        self.assertEqual(menu.parent_id, self.env.ref("mrp.menu_mrp_root"))
