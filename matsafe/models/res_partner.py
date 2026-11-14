from datetime import timedelta

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_member = fields.Boolean(string="Is Member")
    member_code = fields.Char(string="Member Code", copy=False, index=True,
                              help="Used for QR/manual check-in and the self-report form.")
    face_label = fields.Char(string="Face Label",
                             help="Must equal the class name in the Teachable Machine model.")
    weight_kg = fields.Float(string="Weight (kg)")
    level = fields.Selection([
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    ], string="Level")
    member_state = fields.Selection([
        ('active', 'Active'),
        ('blocked', 'Blocked'),
        ('frozen', 'Frozen'),
    ], string="Member State", default='active')
    matsafe_currency_id = fields.Many2one(
        'res.currency', string="Membership Currency",
        default=lambda self: self.env.ref('base.AED', raise_if_not_found=False),
    )
    monthly_fee = fields.Monetary(string="Monthly Fee", currency_field='matsafe_currency_id')
    checkin_ids = fields.One2many('matsafe.checkin', 'member_id', string="Check-ins")
    last_checkin = fields.Datetime(string="Last Check-in",
                                   compute='_compute_last_checkin', store=True)
    usual_sessions_week = fields.Float(
        string="Usual Sessions / Week",
        compute='_compute_usual_sessions_week', search='_search_usual_sessions_week',
        help="Average check-ins per week over the last 4 weeks.",
    )
    open_report_id = fields.Many2one('matsafe.report', string="Open Report", copy=False)

    _member_code_unique = models.Constraint(
        'UNIQUE(member_code)', "The member code must be unique.",
    )

    @api.depends('checkin_ids.datetime')
    def _compute_last_checkin(self):
        for partner in self:
            dates = partner.checkin_ids.mapped('datetime')
            partner.last_checkin = max(dates) if dates else False

    @api.depends('checkin_ids.datetime')
    def _compute_usual_sessions_week(self):
        since = fields.Datetime.now() - timedelta(weeks=4)
        for partner in self:
            count = len(partner.checkin_ids.filtered(
                lambda c: c.datetime and c.datetime >= since and c.result == 'allowed'))
            partner.usual_sessions_week = count / 4.0

    def _search_usual_sessions_week(self, operator, value):
        # Not stored (time-dependent), so evaluate on members in Python.
        members = self.search([('is_member', '=', True)])
        ops = {
            '=': lambda a, b: a == b, '!=': lambda a, b: a != b,
            '>': lambda a, b: a > b, '>=': lambda a, b: a >= b,
            '<': lambda a, b: a < b, '<=': lambda a, b: a <= b,
        }
        if operator not in ops:
            return NotImplemented
        ids = members.filtered(lambda p: ops[operator](p.usual_sessions_week, value)).ids
        return [('id', 'in', ids)]
