from odoo import api, fields, models


class MatsafeRating(models.Model):
    _name = 'matsafe.rating'
    _description = "MatSafe Partner Rating (how controlled was my partner)"
    _order = 'date desc, id desc'

    session_id = fields.Many2one('matsafe.session', string="Session", required=True,
                                 ondelete='cascade', index=True)
    rater_id = fields.Many2one('res.partner', string="Rated By", required=True, index=True)
    rated_id = fields.Many2one('res.partner', string="Rated Member", required=True, index=True)
    score = fields.Integer(required=True, help="1 = out of control, 5 = very controlled")
    comment = fields.Char()
    date = fields.Datetime(related='session_id.start', store=True)

    _one_rating_per_session = models.Constraint(
        'UNIQUE(rater_id, rated_id, session_id)',
        "You already rated this partner for this session.",
    )
    _score_range = models.Constraint(
        'CHECK(score BETWEEN 1 AND 5)', "The score must be between 1 and 5.",
    )
    _not_self = models.Constraint(
        'CHECK(rater_id != rated_id)', "A member can't rate themselves.",
    )

    # Every change can flip the rated member's rough_flag: notify when it turns on
    @api.model_create_multi
    def create(self, vals_list):
        rated = self.env['res.partner'].browse({v.get('rated_id') for v in vals_list if v.get('rated_id')})
        before = {p.id: p.rough_flag for p in rated}
        ratings = super().create(vals_list)
        ratings.rated_id._notify_newly_rough(before)
        return ratings

    def write(self, vals):
        rated = self.rated_id | self.env['res.partner'].browse(vals.get('rated_id') or [])
        before = {p.id: p.rough_flag for p in rated}
        res = super().write(vals)
        rated._notify_newly_rough(before)
        return res

    def unlink(self):
        rated = self.rated_id
        before = {p.id: p.rough_flag for p in rated}
        res = super().unlink()
        rated._notify_newly_rough(before)
        return res
