from datetime import timedelta

from odoo import api, fields, models

RETURN_DAYS = {'skin': 7, 'injury': 14, 'other': 3}


class MatsafeReport(models.Model):
    _name = 'matsafe.report'
    _description = "MatSafe Member Self-Report"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'
    _rec_name = 'member_id'

    member_id = fields.Many2one('res.partner', string="Member", required=True, index=True)
    report_type = fields.Selection([
        ('skin', 'Skin'),
        ('injury', 'Injury'),
        ('other', 'Other'),
    ], string="Type", required=True, default='other')
    description = fields.Text()  # TODO (security pass): restrict to matsafe.group_head_coach
    date = fields.Datetime(default=fields.Datetime.now)
    state = fields.Selection([
        ('open', 'Open'),
        ('cleared', 'Cleared'),
    ], default='open', required=True, tracking=True)
    min_return_date = fields.Date(string="Earliest Return",
                                  compute='_compute_min_return_date', store=True, readonly=False)
    contact_ids = fields.Many2many('res.partner', 'matsafe_report_contact_rel',
                                   'report_id', 'partner_id', string="Traced Contacts")
    cluster_id = fields.Many2one('matsafe.cluster', string="Cluster", index=True)
    cleared_by = fields.Many2one('res.users', string="Cleared By")

    @api.depends('date', 'report_type')
    def _compute_min_return_date(self):
        for report in self:
            if report.date:
                days = RETURN_DAYS.get(report.report_type, 3)
                report.min_return_date = (report.date + timedelta(days=days)).date()
            else:
                report.min_return_date = False

    @api.model_create_multi
    def create(self, vals_list):
        reports = super().create(vals_list)
        reports.action_trace()
        return reports

    def action_trace(self):
        # TODO: automatic chain from the spec (contacts, emails, block, cleaning task, cluster)
        return True

    def action_clear(self):
        self.write({'state': 'cleared', 'cleared_by': self.env.user.id})
