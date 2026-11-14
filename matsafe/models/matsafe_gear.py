from odoo import fields, models


class MatsafeGear(models.Model):
    _name = 'matsafe.gear'
    _description = "MatSafe Rental Gear"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True)
    lot_id = fields.Many2one('stock.lot', string="Lot/Serial")
    state = fields.Selection([
        ('clean', 'Clean'),
        ('in_use', 'In Use'),
        ('laundry', 'Laundry'),
        ('blocked', 'Blocked'),
    ], default='clean', required=True, tracking=True)
    member_id = fields.Many2one('res.partner', string="Held By")
    last_change = fields.Datetime(string="Last Change", default=fields.Datetime.now)
