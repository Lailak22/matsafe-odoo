import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

RETURN_DAYS = {'skin': 7, 'viral': 3, 'injury': 14, 'head': 21, 'other': 3}

# Return-to-play steps for injury / head reports (empty stage = no restriction)
RTP_STEPS = ['no_sparring', 'drilling', 'light_sparring', 'full']


class MatsafeReport(models.Model):
    _name = 'matsafe.report'
    _description = "MatSafe Member Self-Report"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'
    _rec_name = 'member_id'

    member_id = fields.Many2one('res.partner', string="Member", required=True, index=True)
    report_type = fields.Selection([
        ('skin', 'Skin'),
        ('viral', 'Viral'),
        ('injury', 'Injury'),
        ('head', 'Head'),
        ('other', 'Other'),
    ], string="Type", required=True, default='other')
    description = fields.Text(groups="matsafe.group_head_coach")  # private: head coaches only
    date = fields.Datetime(default=fields.Datetime.now)
    state = fields.Selection([
        ('pending', 'Pending'),   # waiting for the member's email confirmation
        ('open', 'Open'),
        ('cleared', 'Cleared'),
    ], default='pending', required=True, tracking=True)
    confirm_token = fields.Char(copy=False, default=lambda self: secrets.token_urlsafe(16))
    confirm_date = fields.Datetime(string="Confirmed On", copy=False, readonly=True)
    min_return_date = fields.Date(string="Earliest Return",
                                  compute='_compute_min_return_date', store=True, readonly=False)
    contact_ids = fields.Many2many('res.partner', 'matsafe_report_contact_rel',
                                   'report_id', 'partner_id', string="Traced Contacts")
    cluster_id = fields.Many2one('matsafe.cluster', string="Cluster", index=True)
    cleared_by = fields.Many2one('res.users', string="Cleared By")

    @api.depends('confirm_date', 'report_type')
    def _compute_min_return_date(self):
        # Counted from confirmation; empty while the report is still pending
        for report in self:
            if report.confirm_date:
                days = RETURN_DAYS.get(report.report_type, 3)
                report.min_return_date = (report.confirm_date + timedelta(days=days)).date()
            else:
                report.min_return_date = False

    @api.model_create_multi
    def create(self, vals_list):
        # One active (pending or open) report per member, also within the same batch
        member_ids = [vals.get('member_id') for vals in vals_list if vals.get('member_id')]
        if len(member_ids) != len(set(member_ids)) or self.search_count([
            ('member_id', 'in', member_ids), ('state', 'in', ('pending', 'open')),
        ]):
            raise UserError("This member already has an open report.")
        # The chain no longer runs here: it waits for action_confirm()
        return super().create(vals_list)

    def action_confirm(self):
        """Email confirmation: pending -> open, then run the chain. A second call does nothing."""
        to_confirm = self.filtered(lambda r: r.state == 'pending')
        to_confirm.write({'state': 'open', 'confirm_date': fields.Datetime.now()})
        to_confirm.action_trace()
        return True

    # ------------------------------------------------------------------
    # The automatic chain
    # ------------------------------------------------------------------
    def action_trace(self):
        """Run the chain; what happens depends on report_type."""
        for report in self:
            member = report.member_id
            rtype = report.report_type
            ref_date = report.date or fields.Datetime.now()
            since = ref_date - timedelta(days=10)

            # min_return_date by report_type, counted from confirmation (also kept by the compute)
            start = report.confirm_date or fields.Datetime.now()
            report.min_return_date = (start + timedelta(days=RETURN_DAYS.get(rtype, 3))).date()

            # Every type keeps an open report on the member: invoicing skips them (membership paused)
            member.open_report_id = report

            if rtype in ('injury', 'head'):
                # No block, no alerts, no cleaning: start return-to-play at "no sparring"
                member.rtp_stage = 'no_sparring'
                continue
            if rtype not in ('skin', 'viral'):
                # 'other': nothing more than the open report
                continue

            # --- Contagious types: skin and viral ---
            # 1. Contacts: training partners (skin) or everyone in the same sessions (viral)
            pairings = report._recent_pairings(member, since)
            sessions = pairings.session_id | self.env['matsafe.session'].search([
                ('attendee_ids', 'in', member.ids),
                ('start', '>=', since),
            ])
            contacts = pairings.member_a_id | pairings.member_b_id
            if rtype == 'viral':
                contacts |= sessions.attendee_ids
            contacts -= member
            report.contact_ids = [(6, 0, contacts.ids)]

            # 2. Alert each contact (anonymous: never the reporter's name)
            what = "skin infection" if rtype == 'skin' else "contagious illness"
            report._notify_partners(
                'matsafe.mail_template_contact_alert', contacts,
                "MatSafe alert: someone you trained with recently reported a possible %s. "
                "Please check yourself and report any symptoms." % what,
            )

            # 3. + 5. Block at the door (open_report_id set above is the freeze flag)
            member.member_state = 'blocked'

            # 4. Cleaning task per mat: deep clean for skin, normal clean for viral
            report._create_cleaning_tasks(sessions, deep=(rtype == 'skin'))

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

    def _create_cleaning_tasks(self, sessions, deep=True):
        """One task per mat zone in the 'Mat Cleaning' project, deadline today."""
        # sudo: coaches/website users may not have Project rights
        Project = self.env['project.project'].sudo()
        project = Project.search([('name', '=', 'Mat Cleaning')], limit=1) \
            or Project.create({'name': 'Mat Cleaning'})
        zone_labels = dict(self.env['matsafe.session']._fields['mat_zone'].selection)
        for zone in set(sessions.filtered('mat_zone').mapped('mat_zone')):
            task = self.env['project.task'].sudo().create({
                'name': "%s %s - report %s" % ("Deep clean" if deep else "Clean", zone_labels[zone], self.id),
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
            member.write({'member_state': 'active', 'open_report_id': False, 'rtp_stage': False})
            member.message_post(body="MatSafe: report cleared by %s, member is active again."
                                % self.env.user.name, subtype_xmlid='mail.mt_note')
        return True

    def action_rtp_next(self):
        """Move the member one return-to-play step forward; reaching 'full' clears the report."""
        for report in self:
            member = report.member_id
            if member.rtp_stage not in RTP_STEPS:
                raise UserError("%s has no return-to-play stage." % member.name)
            next_stage = RTP_STEPS[RTP_STEPS.index(member.rtp_stage) + 1]
            if next_stage == 'full':
                # Fully back: action_clear() also empties rtp_stage
                report.action_clear()
            else:
                member.rtp_stage = next_stage
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
