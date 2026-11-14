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

    MAX_WEIGHT_GAP = 10       # kg
    PAIR_SEARCH_LIMIT = 300000  # safety stop for the search (normal classes need far fewer steps)

    def action_make_pairs(self):
        """Find the most valid pairs (see _can_pair). Among those: fewest repeats of last
        session's partner, then the smallest total weight difference."""
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

            pairs, complete = session._best_pairs(pool)
            self.env['matsafe.pairing'].create([
                {'session_id': session.id, 'member_a_id': a.id, 'member_b_id': b.id, 'source': 'auto'}
                for a, b in pairs
            ])

            # Why each unmatched member sits out (looking at the whole group)
            paired = {m for pair in pairs for m in pair}
            for member in pool:
                if member in paired:
                    continue
                others = [m for m in pool if m != member]
                if not any(abs(m.weight_kg - member.weight_kg) <= self.MAX_WEIGHT_GAP for m in others):
                    reason = "no partner within %s kg" % self.MAX_WEIGHT_GAP
                elif not any(session._can_pair(member, m) for m in others):
                    reason = "no partner at a compatible level within %s kg" % self.MAX_WEIGHT_GAP
                else:
                    reason = "odd one out (all valid partners are in pairs)"
                sat_out.append("%s: %s" % (member.name, reason))

            # One note listing everyone who sits out and why
            if sat_out or not complete:
                lines = list(sat_out)
                if not complete:
                    lines.append("(search stopped early: these pairs are valid but may not be the best)")
                session.message_post(
                    body=Markup("Make Pairs: sitting out this round<br/>") + Markup("<br/>").join(lines),
                    subtype_xmlid='mail.mt_note')
        return True

    def _best_pairs(self, members):
        """Maximum matching by backtracking (branch and bound).
        Returns ([(member_a, member_b), ...], complete). complete=False if the safety limit was hit."""
        n = len(members)
        last = [self._last_partner(m) for m in members]
        # cost of pairing i and j: (1 if they were last session's partners, weight difference)
        cost = {}
        for i in range(n):
            for j in range(i + 1, n):
                if self._can_pair(members[i], members[j]):
                    repeat = int(members[j] == last[i] or members[i] == last[j])
                    cost[i, j] = cost[j, i] = (repeat, abs(members[i].weight_kg - members[j].weight_kg))
        # Compatible partners of each member, cheapest first (so good solutions are found early)
        nbrs = {i: sorted((j for j in range(n) if (i, j) in cost), key=lambda j: cost[i, j]) for i in range(n)}

        def rank(n_pairs, repeats, gap):
            return (n_pairs, -repeats, -gap)  # higher is better

        best = {'rank': rank(-1, 0, 0), 'pairs': []}
        steps = [0]

        def bound(left):
            """Optimistic estimate for the members still left: (max extra pairs, min extra gap)."""
            # Each connected group of compatible members gives at most size // 2 pairs
            seen, max_pairs = set(), 0
            for start in left:
                if start in seen:
                    continue
                size, stack = 0, [start]
                seen.add(start)
                while stack:
                    i = stack.pop()
                    size += 1
                    for j in nbrs[i]:
                        if j in left and j not in seen:
                            seen.add(j)
                            stack.append(j)
                max_pairs += size // 2
            # Each paired member costs at least half of its smallest gap; take the 2*max_pairs smallest
            halves = sorted(min(cost[i, j][1] for j in nbrs[i] if j in left)
                            for i in left if any(j in left for j in nbrs[i]))
            return max_pairs, sum(halves[:2 * max_pairs]) / 2.0

        def search(left, chosen, repeats, gap):
            steps[0] += 1
            max_pairs, min_gap = bound(left)
            # Stop if even the optimistic estimate cannot beat the best solution found
            if rank(len(chosen) + max_pairs, repeats, gap + min_gap) <= best['rank']:
                return
            if max_pairs == 0 or steps[0] > self.PAIR_SEARCH_LIMIT:
                if rank(len(chosen), repeats, gap) > best['rank']:
                    best['rank'], best['pairs'] = rank(len(chosen), repeats, gap), list(chosen)
                return
            i = min(left)  # lightest member left: pair with each compatible partner, or sit out
            rest = left - {i}
            for j in nbrs[i]:
                if j in rest:
                    chosen.append((i, j))
                    search(rest - {j}, chosen, repeats + cost[i, j][0], gap + cost[i, j][1])
                    chosen.pop()
            search(rest, chosen, repeats, gap)

        search(frozenset(range(n)), [], 0, 0.0)
        pairs = [tuple(sorted((members[i], members[j]), key=lambda m: m.weight_kg)) for i, j in best['pairs']]
        return pairs, steps[0] <= self.PAIR_SEARCH_LIMIT

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
