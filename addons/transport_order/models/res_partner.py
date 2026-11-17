from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_carrier = fields.Boolean(
        string="Carrier",
        help="An external transport company that can be assigned to transport orders.",
    )
    # groups=: users without Transport access never compute this field, so the partner form
    # keeps working for them instead of raising an access error.
    transport_order_count = fields.Integer(
        compute="_compute_transport_order_count",
        groups="transport_order.group_transport_user",
    )

    def _transport_order_domain(self):
        self.ensure_one()
        return ["|", ("client_id", "=", self.id), ("carrier_id", "=", self.id)]

    def _compute_transport_order_count(self):
        Order = self.env["transport.order"]
        for partner in self:
            partner.transport_order_count = (
                Order.search_count(partner._transport_order_domain()) if partner.id else 0
            )

    def action_view_transport_orders(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("transport_order.transport_order_action")
        action["domain"] = self._transport_order_domain()
        default_field = "default_carrier_id" if self.is_carrier else "default_client_id"
        action["context"] = {default_field: self.id}
        return action
