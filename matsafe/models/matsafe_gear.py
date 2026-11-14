from datetime import timedelta

from odoo import fields, models


class MatsafeGear(models.Model):
    _name = 'matsafe.gear'
    _description = "MatSafe Rental Gear"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True)
    lot_id = fields.Many2one('stock.lot', string="Lot/Serial")
    state = fields.Selection([
        ('clean', 'Clean'),
        ('in_use', 'In Use'),
        ('laundry', 'Laundry'),
        ('blocked', 'Blocked'),
    ], default='clean', required=True, tracking=True)
    member_id = fields.Many2one('res.partner', string="Held By")
    last_change = fields.Datetime(string="Last Change", default=fields.Datetime.now)

    def write(self, vals):
        # Remember when the state last changed (used by the laundry cron)
        if 'state' in vals and 'last_change' not in vals:
            vals = dict(vals, last_change=fields.Datetime.now())
        return super().write(vals)

    def _cron_gear(self):
        """Hourly: gear in laundry for more than 24h -> blocked + to-do activity for the owner."""
        stuck = self.search([
            ('state', '=', 'laundry'),
            ('last_change', '<', fields.Datetime.now() - timedelta(hours=24)),
        ])
        admin = self.env.ref('base.user_admin')
        for gear in stuck:
            gear.state = 'blocked'
            # Owner = the user of the member holding it, otherwise the admin
            user = gear.member_id.user_ids[:1] or admin
            gear.activity_schedule(
                'mail.mail_activity_data_todo',
                summary="Gear stuck in laundry > 24h",
                note="%s was in laundry for more than 24 hours and is now blocked. "
                     "Check it and set it back to clean." % gear.name,
                user_id=user.id,
            )
        return stuck
