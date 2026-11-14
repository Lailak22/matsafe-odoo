from odoo import fields, models


class MatsafeCluster(models.Model):
    _name = 'matsafe.cluster'
    _description = "MatSafe Outbreak Cluster"
    _inherit = ['mail.thread']

    name = fields.Char(required=True)
    report_ids = fields.One2many('matsafe.report', 'cluster_id', string="Reports")
    session_ids = fields.Many2many('matsafe.session', string="Sessions")
    state = fields.Selection([
        ('active', 'Active'),
        ('closed', 'Closed'),
    ], default='active', required=True)
