from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MyModel(models.Model):
    _name = "my.model"                   # TODO: rename (dots, lowercase)
    _description = "My Model"
    # _inherit = ['mail.thread', 'mail.activity.mixin']   # uncomment for chatter (add 'mail' to depends)

    # ---------- Basic fields ----------
    name = fields.Char(required=True)
    description = fields.Text()
    date = fields.Date(default=fields.Date.today)
    amount = fields.Float()
    active = fields.Boolean(default=True)

    # ---------- Relational fields (examples) ----------
    user_id = fields.Many2one('res.users', string="Responsible", default=lambda self: self.env.user)
    partner_id = fields.Many2one('res.partner', string="Customer")
    # line_ids = fields.One2many('my.model.line', 'parent_id')

    # ---------- Status workflow ----------
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('confirmed', 'Confirmed'),
            ('done', 'Done'),
        ],
        default='draft',
        required=True,
    )

    # ---------- Computed field ----------
    amount_doubled = fields.Float(compute='_compute_amount_doubled', store=True)

    # ---------- SQL constraint ----------
    _unique_name = models.Constraint("UNIQUE(name)", "The name must be unique")

    @api.depends('amount')
    def _compute_amount_doubled(self):
        for record in self:
            record.amount_doubled = record.amount * 2

    # ---------- Python constraint ----------
    @api.constrains('amount')
    def _check_amount(self):
        for record in self:
            if record.amount < 0:
                raise ValidationError("Amount cannot be negative")

    # ---------- Button actions ----------
    def action_confirm(self):
        for record in self:
            if record.state != 'draft':
                raise UserError("Only draft records can be confirmed")
            record.state = 'confirmed'

    def action_done(self):
        for record in self:
            record.state = 'done'

    def action_reset(self):
        for record in self:
            record.state = 'draft'
