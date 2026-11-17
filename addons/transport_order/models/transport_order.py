from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

# Context key that marks a status change made by _set_state(); any other write to "state" is refused.
STATE_CHANGE_CTX = "transport_order_state_change"

# Statuses in which the commercial terms are frozen.
LOCKED_STATES = ("delivered", "cancelled")

# Fields that can no longer change once an order is delivered or cancelled.
LOCKED_FIELDS = {
    "client_id",
    "client_ref",
    "carrier_id",
    "currency_id",
    "client_price",
    "carrier_price",
    "stop_ids",
    "cargo_description",
    "weight_kg",
    "is_adr",
    "vehicle_type",
    "company_id",
}


class TransportOrder(models.Model):
    _name = "transport.order"
    _description = "Transport Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "loading_date desc, id desc"
    _check_company_auto = True

    # Allowed status changes: current status -> statuses it may go to.
    # Every button goes through _set_state(), so this dictionary is the single source of truth.
    _TRANSITIONS = {
        "draft": {"confirmed", "cancelled"},
        "confirmed": {"in_transit", "cancelled"},
        "in_transit": {"delivered", "cancelled"},
        "delivered": set(),
        "cancelled": {"draft"},
    }

    name = fields.Char(
        string="Order No.", required=True, readonly=True, copy=False, default="/", index="trigram"
    )
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        tracking=True,
        default=lambda self: self.env.company.currency_id,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("in_transit", "In Transit"),
            ("delivered", "Delivered"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        required=True,
        readonly=True,
        copy=False,
        default="draft",
        index=True,
        tracking=True,
        group_expand=True,
    )
    user_id = fields.Many2one(
        "res.users", string="Dispatcher", tracking=True, default=lambda self: self.env.user
    )

    # Client side
    client_id = fields.Many2one(
        "res.partner", string="Client", required=True, index=True, tracking=True, check_company=True
    )
    client_ref = fields.Char(
        string="Client Reference",
        tracking=True,
        help="The client's own order or loading number. It is printed on the carrier order for the driver.",
    )
    client_price = fields.Monetary(string="Client Price", tracking=True)

    # Carrier side
    carrier_id = fields.Many2one(
        "res.partner",
        string="Carrier",
        index=True,
        tracking=True,
        check_company=True,
        domain="[('is_carrier', '=', True)]",
    )
    carrier_price = fields.Monetary(string="Carrier Price", tracking=True)

    # Result
    margin = fields.Monetary(compute="_compute_margin", store=True)
    margin_ratio = fields.Float(
        string="Margin %", compute="_compute_margin_ratio", help="Margin divided by the client price."
    )

    # Route
    stop_ids = fields.One2many("transport.order.stop", "order_id", string="Stops", copy=True)
    loading_date = fields.Date(compute="_compute_route", store=True, index=True)
    unloading_date = fields.Date(compute="_compute_route", store=True)
    route = fields.Char(compute="_compute_route", store=True)

    # Cargo
    vehicle_type = fields.Selection(
        [
            ("curtainsider", "Curtainsider"),
            ("mega", "Mega Trailer"),
            ("reefer", "Reefer"),
            ("rigid", "Rigid Truck"),
            ("van", "Van"),
        ],
        default="curtainsider",
    )
    cargo_description = fields.Char(string="Cargo")
    weight_kg = fields.Float(string="Weight (kg)", digits=(12, 0))
    is_adr = fields.Boolean(string="ADR", help="Dangerous goods under the ADR agreement.")
    notes = fields.Text(string="Internal Notes", help="For the office only, never printed.")

    _prices_not_negative = models.Constraint(
        "CHECK(client_price >= 0 AND carrier_price >= 0)",
        "Prices cannot be negative.",
    )
    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)",
        "The order number must be unique per company.",
    )

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    @api.depends("client_price", "carrier_price", "carrier_id")
    def _compute_margin(self):
        # Without a carrier there is no cost yet, so there is no margin either (not the whole client price).
        for order in self:
            order.margin = order.client_price - order.carrier_price if order.carrier_id else 0.0

    @api.depends("margin", "client_price", "carrier_id")
    def _compute_margin_ratio(self):
        for order in self:
            order.margin_ratio = order.margin / order.client_price if order.client_price and order.carrier_id else 0.0

    @api.depends(
        "stop_ids.sequence",
        "stop_ids.stop_type",
        "stop_ids.date",
        "stop_ids.city",
        "stop_ids.country_id",
    )
    def _compute_route(self):
        for order in self:
            stops = order.stop_ids.sorted("sequence")
            loadings = stops.filtered(lambda s: s.stop_type == "loading")
            unloadings = stops.filtered(lambda s: s.stop_type == "unloading")
            loading_dates = [d for d in loadings.mapped("date") if d]
            unloading_dates = [d for d in unloadings.mapped("date") if d]
            order.loading_date = min(loading_dates) if loading_dates else False
            order.unloading_date = max(unloading_dates) if unloading_dates else False
            ends = [stop._place_label() for stop in (loadings[:1], unloadings[-1:]) if stop]
            order.route = " → ".join(ends) if ends else False

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains("stop_ids")
    def _check_stop_dates(self):
        for order in self:
            loading_dates = [s.date for s in order.stop_ids if s.stop_type == "loading" and s.date]
            unloading_dates = [s.date for s in order.stop_ids if s.stop_type == "unloading" and s.date]
            if loading_dates and unloading_dates and min(unloading_dates) < min(loading_dates):
                raise ValidationError(
                    _("Order %(name)s: an unloading cannot happen before the first loading.", name=order.name)
                )

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(STATE_CHANGE_CTX) and any(
            vals.get("state", "draft") != "draft" for vals in vals_list
        ):
            raise UserError(_("New orders always start as Draft."))
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                company = self.env["res.company"].browse(vals.get("company_id")) or self.env.company
                vals["name"] = (
                    self.env["ir.sequence"].with_company(company).next_by_code("transport.order") or "/"
                )
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals and not self.env.context.get(STATE_CHANGE_CTX):
            raise UserError(_("Use the buttons on the order to change its status."))
        if LOCKED_FIELDS & set(vals):
            locked = self.filtered(lambda o: o.state in LOCKED_STATES)
            if locked:
                raise UserError(
                    _(
                        "Order %(name)s is %(state)s: its terms can no longer be changed.",
                        name=locked[0].name,
                        state=locked[0]._state_label(),
                    )
                )
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft_or_cancelled(self):
        active = self.filtered(lambda o: o.state not in ("draft", "cancelled"))
        if active:
            raise UserError(
                _("Order %(name)s is in progress. Cancel it before deleting.", name=active[0].name)
            )

    # ------------------------------------------------------------------
    # Status flow
    # ------------------------------------------------------------------

    def _state_label(self, state=None):
        self.ensure_one()
        labels = dict(self._fields["state"]._description_selection(self.env))
        return labels[state or self.state]

    def _set_state(self, new_state):
        """The only place that writes the status. Checks the transition for every order first."""
        for order in self:
            if new_state not in self._TRANSITIONS[order.state]:
                raise UserError(
                    _(
                        "Order %(name)s: cannot go from %(old)s to %(new)s.",
                        name=order.name,
                        old=order._state_label(),
                        new=order._state_label(new_state),
                    )
                )
        return self.with_context(**{STATE_CHANGE_CTX: True}).write({"state": new_state})

    def _missing_for_confirmation(self):
        """What still has to be filled in before the order can be confirmed (list of messages)."""
        self.ensure_one()
        missing = []
        if not self.carrier_id:
            missing.append(_("a carrier"))
        if self.client_price <= 0:
            missing.append(_("the client price"))
        if self.carrier_price <= 0:
            missing.append(_("the carrier price"))
        stop_types = set(self.stop_ids.filtered("date").mapped("stop_type"))
        if "loading" not in stop_types:
            missing.append(_("a loading stop with a date"))
        if "unloading" not in stop_types:
            missing.append(_("an unloading stop with a date"))
        return missing

    def action_confirm(self):
        for order in self:
            missing = order._missing_for_confirmation()
            if missing:
                raise UserError(
                    _(
                        "Order %(name)s cannot be confirmed yet. Missing: %(missing)s.",
                        name=order.name,
                        missing=", ".join(missing),
                    )
                )
        return self._set_state("confirmed")

    def action_start_transit(self):
        return self._set_state("in_transit")

    def action_deliver(self):
        return self._set_state("delivered")

    def action_cancel(self):
        return self._set_state("cancelled")

    def action_reset_to_draft(self):
        if not self.env.user.has_group("transport_order.group_transport_manager"):
            raise AccessError(_("Only a Transport Manager can reopen a cancelled order."))
        return self._set_state("draft")
