from odoo import fields, models


class MatsafeCheckin(models.Model):
    _name = 'matsafe.checkin'
    _description = "MatSafe Door Check-in"
    _order = 'datetime desc'
    _rec_name = 'member_id'

    member_id = fields.Many2one('res.partner', string="Member", index=True)
    datetime = fields.Datetime(default=fields.Datetime.now)
    method = fields.Selection([
        ('face', 'Face'),
        ('code', 'Code'),
    ])
    result = fields.Selection([
        ('allowed', 'Allowed'),
        ('blocked', 'Blocked'),
    ])
    reason = fields.Char()
