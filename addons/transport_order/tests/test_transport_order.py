from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestTransportOrder(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.client = Partner.create({"name": "Test Client GmbH", "is_company": True})
        cls.carrier = Partner.create({"name": "Test Carrier Sp. z o.o.", "is_company": True, "is_carrier": True})
        cls.pl = cls.env.ref("base.pl")
        cls.de = cls.env.ref("base.de")
        cls.dispatcher = new_test_user(
            cls.env, login="test_dispatcher", groups="transport_order.group_transport_user"
        )
        cls.manager = new_test_user(
            cls.env, login="test_transport_manager", groups="transport_order.group_transport_manager"
        )

    def _stop(self, stop_type, date, city, country, sequence):
        return Command.create(
            {"stop_type": stop_type, "date": date, "city": city, "country_id": country.id, "sequence": sequence}
        )

    def _order(self, **vals):
        values = {
            "client_id": self.client.id,
            "carrier_id": self.carrier.id,
            "client_price": 1537.0,
            "carrier_price": 1213.0,
            "stop_ids": [
                self._stop("loading", "2026-11-20", "Kępno", self.pl, 10),
                self._stop("unloading", "2026-11-21", "Berlin", self.de, 20),
            ],
        }
        values.update(vals)
        return self.env["transport.order"].create(values)

    # --- numbering and computed fields -------------------------------------------------

    def test_new_orders_get_unique_numbers(self):
        first, second = self._order(), self._order()
        self.assertTrue(first.name.startswith("TO/"))
        self.assertNotEqual(first.name, second.name)

    def test_margin_is_client_price_minus_carrier_price(self):
        order = self._order()
        self.assertEqual(order.margin, 324.0)
        self.assertAlmostEqual(order.margin_ratio, 324.0 / 1537.0)
        order.carrier_price = 1600.0
        self.assertEqual(order.margin, -63.0)

    def test_no_margin_before_a_carrier_is_chosen(self):
        order = self._order(carrier_id=False, carrier_price=0.0)
        self.assertEqual(order.margin, 0.0)
        self.assertEqual(order.margin_ratio, 0.0)
        order.write({"carrier_id": self.carrier.id, "carrier_price": 1213.0})
        self.assertEqual(order.margin, 324.0)

    def test_route_and_dates_come_from_stops(self):
        order = self._order()
        self.assertEqual(order.route, "PL Kępno → DE Berlin")
        self.assertEqual(str(order.loading_date), "2026-11-20")
        self.assertEqual(str(order.unloading_date), "2026-11-21")
        order.stop_ids = [self._stop("unloading", "2026-11-22", "Hamburg", self.de, 30)]
        self.assertEqual(order.route, "PL Kępno → DE Hamburg")
        self.assertEqual(str(order.unloading_date), "2026-11-22")

    # --- data rules --------------------------------------------------------------------

    def test_unloading_before_loading_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._order(
                stop_ids=[
                    self._stop("loading", "2026-11-20", "Kępno", self.pl, 10),
                    self._stop("unloading", "2026-11-19", "Berlin", self.de, 20),
                ]
            )

    def test_editing_one_stop_checks_dates_too(self):
        order = self._order()
        unloading = order.stop_ids.filtered(lambda s: s.stop_type == "unloading")
        with self.assertRaises(ValidationError):
            unloading.date = "2026-11-01"

    @mute_logger("odoo.sql_db")
    def test_negative_price_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            self._order(carrier_price=-1.0)

    # --- status flow -------------------------------------------------------------------

    def test_full_status_flow(self):
        order = self._order()
        self.assertEqual(order.state, "draft")
        order.action_confirm()
        order.action_start_transit()
        order.action_deliver()
        self.assertEqual(order.state, "delivered")

    def test_confirm_requires_carrier_and_prices(self):
        order = self._order(carrier_id=False, carrier_price=0.0)
        with self.assertRaisesRegex(UserError, "a carrier"):
            order.action_confirm()
        self.assertEqual(order.state, "draft")

    def test_skipping_a_status_is_refused(self):
        order = self._order()
        with self.assertRaises(UserError):
            order.action_deliver()

    def test_status_cannot_be_written_directly(self):
        order = self._order()
        with self.assertRaises(UserError):
            order.write({"state": "delivered"})
        with self.assertRaises(UserError):
            self.env["transport.order"].create({"client_id": self.client.id, "state": "delivered"})

    def test_delivered_order_is_locked(self):
        order = self._order()
        order.action_confirm()
        order.action_start_transit()
        order.action_deliver()
        with self.assertRaises(UserError):
            order.client_price = 2000.0
        with self.assertRaises(UserError):
            order.stop_ids[0].city = "Poznań"
        # Fields outside the commercial terms stay editable, for example the internal notes.
        order.notes = "Client paid late."

    # --- deleting and access rights ----------------------------------------------------

    def test_only_draft_or_cancelled_orders_can_be_deleted(self):
        order = self._order()
        order.action_confirm()
        with self.assertRaises(UserError):
            order.unlink()
        order.action_cancel()
        order.unlink()
        self.assertFalse(order.exists())

    def test_dispatcher_cannot_delete_orders(self):
        order = self._order()
        with self.assertRaises(AccessError):
            order.with_user(self.dispatcher).unlink()

    def test_only_manager_can_reopen_cancelled_order(self):
        order = self._order()
        order.action_cancel()
        with self.assertRaises(AccessError):
            order.with_user(self.dispatcher).action_reset_to_draft()
        order.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(order.state, "draft")

    def test_dispatcher_can_run_the_whole_flow(self):
        order = self._order().with_user(self.dispatcher)
        order.action_confirm()
        order.action_start_transit()
        order.action_deliver()
        self.assertEqual(order.state, "delivered")

    # --- copy, partner button, report --------------------------------------------------

    def test_copy_starts_as_new_draft_with_same_route(self):
        order = self._order()
        order.action_confirm()
        copy = order.copy()
        self.assertEqual(copy.state, "draft")
        self.assertNotEqual(copy.name, order.name)
        self.assertEqual(copy.route, order.route)
        self.assertEqual(len(copy.stop_ids), 2)

    def test_partner_counts_orders_as_client_and_as_carrier(self):
        self._order()
        self.assertEqual(self.client.transport_order_count, 1)
        self.assertEqual(self.carrier.transport_order_count, 1)

    def test_carrier_order_never_shows_client_price_or_margin(self):
        order = self._order(client_ref="REF-777", notes="Secret office note")
        order.action_confirm()
        html, _report_type = self.env["ir.actions.report"]._render_qweb_html(
            "transport_order.action_report_carrier_order", order.ids
        )
        html = html.decode()
        self.assertIn(order.name, html)
        self.assertIn("1,213.00", html)  # carrier price
        self.assertIn("REF-777", html)  # loading reference for the driver
        self.assertNotIn("1,537.00", html)  # client price
        self.assertNotIn("324.00", html)  # margin
        self.assertNotIn("Test Client GmbH", html)
        self.assertNotIn("Secret office note", html)
