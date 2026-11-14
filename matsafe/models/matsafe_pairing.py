from odoo import fields, models


class MatsafePairing(models.Model):
    _name = 'matsafe.pairing'
    _description = "MatSafe Pairing (who trained with who)"
    _order = 'date desc'

    session_id = fields.Many2one('matsafe.session', required=True, ondelete='cascade', index=True)
    member_a_id = fields.Many2one('res.partner', string="Member A", required=True, index=True)
    member_b_id = fields.Many2one('res.partner', string="Member B", required=True, index=True)
    date = fields.Datetime(related='session_id.start', store=True)
    source = fields.Selection([
        ('auto', 'Auto'),
        ('camera', 'Camera'),
        ('manual', 'Manual'),
    ], default='manual')
