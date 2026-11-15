from odoo import http
from odoo.http import request

from odoo.addons.website.controllers.main import Website


class MatsafeHome(Website):
    """Opening the server address (http://localhost:8069/) goes straight to the MatSafe app
    instead of Odoo's default website homepage. Not logged in: Odoo shows its login page first
    and then lands on MatSafe. The member pages (/matsafe/report, /matsafe/rate, /matsafe/kiosk)
    are not affected."""

    @http.route()
    def index(self, **kw):
        return request.redirect('/odoo/action-matsafe.action_matsafe_dashboard')
