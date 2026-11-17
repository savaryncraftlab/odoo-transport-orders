from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .transport_order import LOCKED_STATES


class TransportOrderStop(models.Model):
    _name = "transport.order.stop"
    _description = "Transport Order Stop"
    _order = "sequence, id"

    order_id = fields.Many2one("transport.order", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="order_id.company_id", store=True)
    sequence = fields.Integer(default=10)
    stop_type = fields.Selection(
        [("loading", "Loading"), ("unloading", "Unloading")],
        string="Type",
        required=True,
        default="loading",
    )
    date = fields.Date()
    time_window = fields.Char(string="Hours", help="Opening hours of the place, for example 08:00-14:00.")
    place_name = fields.Char(string="Company / Place")
    street = fields.Char()
    zip = fields.Char(string="ZIP")
    city = fields.Char(required=True)
    country_id = fields.Many2one("res.country", string="Country", required=True)
    note = fields.Char(help="For the driver, for example the loading reference or the gate number.")

    def _place_label(self):
        """Short label for the route, for example "PL Kępno"."""
        self.ensure_one()
        return " ".join(part for part in (self.country_id.code, self.city) if part)

    def _check_order_not_locked(self, orders):
        locked = orders.filtered(lambda o: o.state in LOCKED_STATES)
        if locked:
            raise UserError(
                _("Order %(name)s is closed: its stops can no longer be changed.", name=locked[0].name)
            )

    @api.model_create_multi
    def create(self, vals_list):
        order_ids = {vals["order_id"] for vals in vals_list if vals.get("order_id")}
        self._check_order_not_locked(self.env["transport.order"].browse(order_ids))
        return super().create(vals_list)

    def write(self, vals):
        self._check_order_not_locked(self.order_id)
        res = super().write(vals)
        if "order_id" in vals:
            self._check_order_not_locked(self.order_id)
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_locked_order(self):
        self._check_order_not_locked(self.order_id)

    @api.constrains("date", "stop_type", "order_id")
    def _check_dates_on_order(self):
        # Editing one stop directly must respect the same rule as editing the order.
        self.order_id._check_stop_dates()
