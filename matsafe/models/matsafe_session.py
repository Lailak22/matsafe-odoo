from datetime import timedelta

from markupsafe import Markup

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

    MAX_WEIGHT_GAP = 10  # kg

    def action_make_pairs(self):
        """Sort by weight, then pair each member with the nearest valid partner.
        Valid = within 10 kg and not beginner with advanced; avoid last session's partner if possible."""
        for session in self:
            # Remove previous auto pairings of this session (camera/manual ones stay)
            session.pairing_ids.filtered(lambda p: p.source == 'auto').unlink()

            sat_out = []  # "Name: reason" lines for the note
            # Skip blocked/frozen members and those limited by return-to-play
            pool = []
            for member in session.attendee_ids.sorted('weight_kg'):
                if member.member_state != 'active':
                    sat_out.append("%s: %s" % (member.name, member.member_state))
                elif member.rtp_stage in ('no_sparring', 'drilling'):
                    sat_out.append("%s: return-to-play (%s)" % (member.name, member.rtp_stage.replace('_', ' ')))
                else:
                    pool.append(member)

            eligible = list(pool)
            pairs = []
            while pool:
                first = pool.pop(0)  # lightest member left
                # Pool is sorted by weight, so valid partners come nearest first
                valid = [m for m in pool if session._can_pair(first, m)]
                if not valid:
                    # Reason looks at the whole group, not only who is left
                    others = [m for m in eligible if m != first]
                    if not any(abs(m.weight_kg - first.weight_kg) <= self.MAX_WEIGHT_GAP for m in others):
                        reason = "no partner within %s kg" % self.MAX_WEIGHT_GAP
                    elif not any(session._can_pair(first, m) for m in others):
                        reason = "no partner at a compatible level within %s kg" % self.MAX_WEIGHT_GAP
                    else:
                        reason = "valid partners were already paired"
                    sat_out.append("%s: %s" % (first.name, reason))
                    continue
                last_partner = session._last_partner(first)
                # Nearest valid partner who was not their last partner, else the nearest valid one
                mate = next((m for m in valid if m != last_partner), valid[0])
                pool.remove(mate)
                pairs.append({'session_id': session.id, 'member_a_id': first.id,
                              'member_b_id': mate.id, 'source': 'auto'})
            self.env['matsafe.pairing'].create(pairs)

            # One note listing everyone who sits out and why
            if sat_out:
                session.message_post(
                    body=Markup("Make Pairs: sitting out this round<br/>") + Markup("<br/>").join(sat_out),
                    subtype_xmlid='mail.mt_note')
        return True

    def _can_pair(self, a, b):
        """Within 10 kg and not beginner with advanced."""
        return (abs(a.weight_kg - b.weight_kg) <= self.MAX_WEIGHT_GAP
                and {a.level, b.level} != {'beginner', 'advanced'})

    def _last_partner(self, member):
        """Partner of the member in their most recent other session (empty if none)."""
        last = self.env['matsafe.pairing'].search([
            ('session_id', '!=', self.id),
            '|', ('member_a_id', '=', member.id), ('member_b_id', '=', member.id),
        ], order='date desc, id desc', limit=1)
        return (last.member_a_id | last.member_b_id) - member

    def action_end(self):
        """Close the class and create a normal (not deep) cleaning task for its mat."""
        self.write({'state': 'done'})
        project = self._get_cleaning_project()
        zones = dict(self._fields['mat_zone'].selection)
        for session in self:
            task = self.env['project.task'].sudo().create({
                'name': "Clean %s - %s" % (zones.get(session.mat_zone, 'mat'), session.name),
                'project_id': project.id,
                'date_deadline': fields.Datetime.now(),
            })
            session.cleaning_task_id = task
        return True

    def action_open_pair_cam(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url',
                'url': '/matsafe/pair-cam?session_id=%s' % self.id,
                'target': 'new'}

    def _get_cleaning_project(self):
        # sudo: coaches may not have Project rights
        Project = self.env['project.project'].sudo()
        return Project.search([('name', '=', 'Mat Cleaning')], limit=1) \
            or Project.create({'name': 'Mat Cleaning'})

    # ------------------------------------------------------------------
    # Scheduled action
    # ------------------------------------------------------------------
    def _cron_cleaning(self):
        """Hourly: warn on sessions starting within 1h whose mat still has an open cleaning task."""
        now = fields.Datetime.now()
        upcoming = self.search([
            ('state', '=', 'draft'),
            ('start', '>=', now), ('start', '<=', now + timedelta(hours=1)),
        ])
        for session in upcoming:
            # Cleaning tasks are linked on sessions of the same mat (by action_end and the report chain)
            tasks = self.search([
                ('mat_zone', '=', session.mat_zone), ('cleaning_task_id', '!=', False),
            ]).cleaning_task_id.filtered(lambda t: not t.is_closed)
            if tasks:
                session.message_post(
                    body="Warning: this mat still has open cleaning task(s): %s. "
                         "Clean before class starts." % ", ".join(tasks.mapped('name')),
                    subtype_xmlid='mail.mt_note')
