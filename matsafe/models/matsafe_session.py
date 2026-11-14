from datetime import timedelta

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
        """Pair active attendees by weight; avoid repeating each member's last partner."""
        for session in self:
            # Remove previous auto pairings of this session (camera/manual ones stay)
            session.pairing_ids.filtered(lambda p: p.source == 'auto').unlink()

            # Only active members, lightest first
            pool = session.attendee_ids.filtered(lambda m: m.member_state == 'active').sorted('weight_kg')
            pool = list(pool)
            pairs = []
            while len(pool) >= 2:
                first = pool.pop(0)
                last_partner = session._last_partner(first)
                # Nearest neighbour by weight who was not their last partner, else simply the nearest
                mate = next((m for m in pool if m != last_partner), pool[0])
                pool.remove(mate)
                pairs.append({'session_id': session.id, 'member_a_id': first.id,
                              'member_b_id': mate.id, 'source': 'auto'})
            self.env['matsafe.pairing'].create(pairs)

            # Odd number: one person sits out this round
            if pool:
                session.message_post(body="Make Pairs: %s has no partner this round." % pool[0].name)
        return True

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
