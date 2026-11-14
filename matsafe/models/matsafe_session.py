from odoo import fields, models


class MatsafeSession(models.Model):
    _name = 'matsafe.session'
    _description = "MatSafe Class Session"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start desc'

    name = fields.Char(required=True)
    start = fields.Datetime()
    coach_id = fields.Many2one('res.users', string="Coach")
    discipline = fields.Selection([
        ('bjj_gi', 'BJJ Gi'),
        ('bjj_nogi', 'BJJ No-Gi'),
        ('muay_thai', 'Muay Thai'),
        ('mma', 'MMA'),
        ('wrestling', 'Wrestling'),
    ])
    mat_zone = fields.Selection([
        ('mat_a', 'Mat A'),
        ('mat_b', 'Mat B'),
    ], string="Mat Zone")
    attendee_ids = fields.Many2many('res.partner', string="Attendees")
    pairing_ids = fields.One2many('matsafe.pairing', 'session_id', string="Pairings")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
    ], default='draft', required=True, tracking=True)
    cleaning_task_id = fields.Many2one('project.task', string="Cleaning Task")

    def action_start(self):
        self.write({'state': 'running'})

    def action_make_pairs(self):
        # TODO: auto-pair attendees (weight/level) -> matsafe.pairing with source 'auto'
        return True

    def action_end(self):
        self.write({'state': 'done'})
