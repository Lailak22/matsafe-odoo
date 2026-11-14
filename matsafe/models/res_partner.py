from datetime import datetime, timedelta

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
    # Both computed from allowed check-ins only and stored (searchable in crons/filters).
    # usual_sessions_week depends on "today", so _cron_absence recomputes it before use.
    last_checkin = fields.Datetime(string="Last Check-in",
                                   compute='_compute_checkin_stats', store=True)
    usual_sessions_week = fields.Float(
        string="Usual Sessions / Week", compute='_compute_checkin_stats', store=True,
        help="Allowed check-ins in the last 28 days divided by 4.",
    )
    rtp_stage = fields.Selection([
        ('no_sparring', 'No Sparring'),
        ('drilling', 'Drilling'),
        ('light_sparring', 'Light Sparring'),
        ('full', 'Full'),
    ], string="Return-to-Play Stage", help="Shown on the kiosk. Empty = no restriction.")
    # Partner ratings received (matsafe.rating): how controlled this member is when sparring
    rating_received_ids = fields.One2many('matsafe.rating', 'rated_id', string="Ratings Received")
    control_score = fields.Float(string="Control Score", compute='_compute_control', store=True,
                                 help="Average score of the last 10 ratings received (1-5).")
    rating_count = fields.Integer(string="Partner Ratings", compute='_compute_control', store=True)
    rough_flag = fields.Boolean(string="Rough Flag", compute='_compute_control', store=True,
                                help="At least 3 ratings and a control score under 2.5.")
    open_report_id = fields.Many2one('matsafe.report', string="Open Report", copy=False)

    _member_code_unique = models.Constraint(
        'UNIQUE(member_code)', "The member code must be unique.",
    )

    @api.depends('checkin_ids.datetime', 'checkin_ids.result')
    def _compute_checkin_stats(self):
        since = fields.Datetime.now() - timedelta(days=28)
        for partner in self:
            allowed = partner.checkin_ids.filtered(lambda c: c.result == 'allowed' and c.datetime)
            dates = allowed.mapped('datetime')
            partner.last_checkin = max(dates) if dates else False
            partner.usual_sessions_week = len([d for d in dates if d >= since]) / 4.0

    @api.depends('rating_received_ids.score', 'rating_received_ids.date')
    def _compute_control(self):
        for partner in self:
            ratings = partner.rating_received_ids
            # Newest 10 ratings (by session date, then creation order)
            last10 = ratings.sorted(lambda r: (r.date or datetime.min, r.id), reverse=True)[:10]
            partner.rating_count = len(ratings)
            partner.control_score = sum(last10.mapped('score')) / len(last10) if last10 else 0.0
            partner.rough_flag = partner.rating_count >= 3 and partner.control_score < 2.5

    def _notify_newly_rough(self, before):
        """Note + head coach activity for members whose rough_flag just turned on.
        `before` = {partner_id: rough_flag before the change}."""
        # sudo: ratings may come from coaches or the website, the alert is system behaviour
        head_coaches = self.env.ref('matsafe.group_head_coach').sudo().user_ids.filtered(
            lambda u: not u.share and u.id != 1)  # skip public/portal users and __system__
        for partner in self.sudo():
            if not partner.rough_flag or before.get(partner.id):
                continue
            note = ("MatSafe: flagged as rough (control score %.1f over the last %d ratings). "
                    "They won't be paired with beginners." % (partner.control_score, min(partner.rating_count, 10)))
            partner.message_post(body=note, subtype_xmlid='mail.mt_note')
            for user in head_coaches:
                partner.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary="Rough sparring flag: talk to %s" % partner.name,
                    note=note, user_id=user.id)

    def _matsafe_notify(self, template_xmlid, note, record=None):
        """Email with the template if it exists, otherwise leave a note on the member's chatter.
        `record` is what the template renders on when it is not a res.partner template."""
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        for partner in self:
            if not template:
                partner.message_post(body=note, subtype_xmlid='mail.mt_note')
            elif template.model == 'res.partner' or not record:
                template.send_mail(partner.id)
            else:
                # Rendered on another record: force this member as the only recipient
                template.send_mail(record.id, email_values={
                    'recipient_ids': [(6, 0, partner.ids)], 'email_to': False})

    # ------------------------------------------------------------------
    # Scheduled actions
    # ------------------------------------------------------------------
    def _cron_absence(self):
        """Daily: regulars (>= 2 sessions/week) with no check-in for 7 days and no open report."""
        members = self.search([('is_member', '=', True)])
        # Refresh the time-dependent stats before filtering on them
        self.env.add_to_compute(self._fields['usual_sessions_week'], members)
        members._recompute_recordset(['usual_sessions_week', 'last_checkin'])

        week_ago = fields.Datetime.now() - timedelta(days=7)
        missing = self.search([
            ('is_member', '=', True),
            ('member_state', '=', 'active'),
            ('usual_sessions_week', '>=', 2),
            ('last_checkin', '<', week_ago),
            ('open_report_id', '=', False),
        ])
        missing._matsafe_notify(
            'matsafe.mail_template_we_miss_you',
            "MatSafe: we miss you! No check-in for over a week. Hope to see you on the mats soon.")
        return missing

    def _cron_invoicing(self):
        """Monthly: one draft customer invoice per active member; blocked/frozen are skipped."""
        members = self.search([
            ('is_member', '=', True),
            ('member_state', '=', 'active'),
            # Paused only by an open skin/viral/injury/head report; 'other' keeps billing
            '|', ('open_report_id', '=', False), ('open_report_id.report_type', '=', 'other'),
            ('monthly_fee', '>', 0),
        ])
        moves = self.env['account.move']
        for member in members:
            vals = {
                'move_type': 'out_invoice',
                'partner_id': member.id,
                'invoice_line_ids': [(0, 0, {
                    'name': "Monthly membership",
                    'quantity': 1,
                    'price_unit': member.monthly_fee,
                })],
            }
            # Bill in the member's currency (AED) when it is active in this database
            if member.matsafe_currency_id.active:
                vals['currency_id'] = member.matsafe_currency_id.id
            moves |= moves.create(vals)
        return moves
