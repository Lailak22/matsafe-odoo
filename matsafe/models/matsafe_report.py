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
        # Every new report starts the automatic chain
        reports.action_trace()
        return reports

    # ------------------------------------------------------------------
    # The automatic chain
    # ------------------------------------------------------------------
    def action_trace(self):
        for report in self:
            member = report.member_id
            ref_date = report.date or fields.Datetime.now()

            # min_return_date by report_type (also kept up to date by the compute)
            report.min_return_date = (ref_date + timedelta(days=RETURN_DAYS.get(report.report_type, 3))).date()

            # 1. Pairings with the member in the last 10 days -> contacts
            pairings = report._recent_pairings(member, ref_date - timedelta(days=10))
            contacts = (pairings.member_a_id | pairings.member_b_id) - member
            report.contact_ids = [(6, 0, contacts.ids)]

            # 2. Alert each contact (anonymous: never the reporter's name)
            report._notify_partners(
                'matsafe.mail_template_contact_alert', contacts,
                "MatSafe alert: a training partner from a recent session reported a possible "
                "skin infection or injury. Please check yourself and report any symptoms.",
            )

            # 3. Block the member at the door and link the open report
            # 5. Freeze: blocked + open_report_id is the flag that the door and invoicing check
            member.write({'member_state': 'blocked', 'open_report_id': report.id})

            # 4. Cleaning task for each mat zone of the member's recent sessions
            sessions = pairings.session_id | self.env['matsafe.session'].search([
                ('attendee_ids', 'in', member.ids),
                ('start', '>=', ref_date - timedelta(days=10)),
            ])
            report._create_cleaning_tasks(sessions)

            # 6. Cluster check
            report._check_cluster(ref_date)
        return True

    def _recent_pairings(self, member, since):
        """Pairings where the member is on either side, since the given date."""
        return self.env['matsafe.pairing'].search([
            ('date', '>=', since),
            '|', ('member_a_id', '=', member.id), ('member_b_id', '=', member.id),
        ])

    def _notify_partners(self, template_xmlid, partners, note):
        """Email partners with the template if it exists, and always leave a note on their chatter."""
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        for partner in partners:
            if template:
                if template.model == 'res.partner':
                    # Template rendered on the contact itself
                    template.send_mail(partner.id)
                else:
                    # Template rendered on the report: force the recipient to this contact only
                    template.send_mail(self.id, email_values={
                        'recipient_ids': [(6, 0, partner.ids)], 'email_to': False,
                    })
            # Note on the contact's chatter so the demo shows it
            partner.message_post(body=note, subtype_xmlid='mail.mt_note')

    def _create_cleaning_tasks(self, sessions):
        """One task per mat zone in the 'Mat Cleaning' project, deadline today."""
        # sudo: coaches/website users may not have Project rights
        Project = self.env['project.project'].sudo()
        project = Project.search([('name', '=', 'Mat Cleaning')], limit=1) \
            or Project.create({'name': 'Mat Cleaning'})
        zone_labels = dict(self.env['matsafe.session']._fields['mat_zone'].selection)
        for zone in set(sessions.filtered('mat_zone').mapped('mat_zone')):
            task = self.env['project.task'].sudo().create({
                'name': "Deep clean %s - report %s" % (zone_labels[zone], self.id),
                'project_id': project.id,
                'date_deadline': fields.Datetime.now(),
            })
            # Link the task on the sessions of that mat (used by the cleaning cron)
            sessions.filtered(lambda s: s.mat_zone == zone).cleaning_task_id = task

    def _check_cluster(self, ref_date):
        """If another open report (last 7 days) belongs to a contact -> outbreak cluster."""
        others = self.search([
            ('id', '!=', self.id),
            ('state', '=', 'open'),
            ('member_id', 'in', self.contact_ids.ids),
            ('date', '>=', ref_date - timedelta(days=7)),
        ])
        if not others:
            return

        # Reuse an active cluster of any involved report, else create one
        cluster = (self.cluster_id | others.cluster_id).filtered(lambda c: c.state == 'active')[:1]
        if not cluster:
            cluster = self.env['matsafe.cluster'].create({
                'name': "Outbreak %s" % fields.Date.to_string(ref_date.date()),
            })
        (self | others).cluster_id = cluster

        # Sessions the reporters shared (paired together or both attended)
        shared = self.env['matsafe.session']
        for other in others:
            pairs = self._recent_pairings(self.member_id, ref_date - timedelta(days=10)).filtered(
                lambda p: other.member_id in (p.member_a_id | p.member_b_id))
            shared |= pairs.session_id
            shared |= self.env['matsafe.session'].search([
                ('attendee_ids', 'in', self.member_id.ids),
                ('attendee_ids', 'in', other.member_id.ids),
                ('start', '>=', ref_date - timedelta(days=10)),
            ])
        cluster.session_ids = [(4, s.id) for s in shared]

        # Email ALL attendees of the shared sessions
        attendees = shared.attendee_ids | shared.pairing_ids.member_a_id | shared.pairing_ids.member_b_id
        self._notify_partners(
            'matsafe.mail_template_outbreak', attendees,
            "MatSafe outbreak notice: several members from a session you attended reported "
            "symptoms. Please check yourself and report any symptoms before training.",
        )
        cluster.message_post(body="Cluster updated: %s reports, %s shared sessions."
                             % (len(cluster.report_ids), len(cluster.session_ids)))

    # ------------------------------------------------------------------
    def action_clear(self):
        for report in self:
            report.write({'state': 'cleared', 'cleared_by': self.env.user.id})
            member = report.member_id
            member.write({'member_state': 'active', 'open_report_id': False})
            member.message_post(body="MatSafe: report cleared by %s, member is active again."
                                % self.env.user.name, subtype_xmlid='mail.mt_note')
        return True

    # ------------------------------------------------------------------
    # Scheduled action
    # ------------------------------------------------------------------
    def _cron_return(self):
        """Daily: open reports past min_return_date -> ask the member to get cleared."""
        overdue = self.search([
            ('state', '=', 'open'),
            ('min_return_date', '<', fields.Date.context_today(self)),
        ])
        for report in overdue:
            report.member_id._matsafe_notify(
                'matsafe.mail_template_return',
                "MatSafe: your minimum time off is over. Please ask a head coach "
                "to clear you before coming back to the mats.",
                record=report,
            )
        return overdue
