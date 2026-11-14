import logging
import os

from odoo import http
from odoo.http import request
from odoo.tools.misc import file_path

_logger = logging.getLogger(__name__)

# Radio choices on the report form: (value stored on matsafe.report, label shown)
REPORT_TYPES = [('skin', "Skin issue"), ('injury', "Injury"), ('other', "Other")]


class MatsafeWebsite(http.Controller):

    def _find_member(self, field, value):
        """Member whose `field` (member_code or face_label) equals value, else an empty recordset."""
        # sudo: visitors and the door kiosk are not logged in
        Partner = request.env['res.partner'].sudo()
        value = str(value or '').strip()
        if not value:
            return Partner
        return Partner.search([('is_member', '=', True), (field, '=', value)], limit=1)

    # ------------------------------------------------------------------
    # /matsafe/report : public self-report form
    # ------------------------------------------------------------------
    @http.route('/matsafe/report', type='http', auth='public', website=True,
                methods=['GET', 'POST'], sitemap=False)
    def report(self, **post):
        if request.httprequest.method == 'GET':
            # After a successful POST we redirect here with ?submitted=1
            if post.get('submitted'):
                return request.render('matsafe.report_thanks')
            return self._render_report_form()

        member_code = (post.get('member_code') or '').strip()
        report_type = post.get('report_type')
        description = (post.get('description') or '').strip()
        values = {'member_code': member_code, 'report_type': report_type, 'description': description}

        # Check the form; any problem is shown on the same page
        if not member_code:
            return self._render_report_form(values, "Please enter your member code.")
        if report_type not in dict(REPORT_TYPES):
            return self._render_report_form(values, "Please choose what you are reporting.")
        if not post.get('consent'):
            return self._render_report_form(values, "Please tick the consent box to send your report.")
        member = self._find_member('member_code', member_code)
        if not member:
            return self._render_report_form(
                values, "We could not find that member code. Please check it and try again.")

        # create() on matsafe.report starts the automatic chain (action_trace) by itself,
        # so we never call action_trace here: it must not run twice.
        try:
            # savepoint: if the chain fails, undo it and show a message instead of an error page
            with request.env.cr.savepoint():
                request.env['matsafe.report'].sudo().create({
                    'member_id': member.id,
                    'report_type': report_type,
                    'description': description,
                })
        except Exception:
            _logger.exception("MatSafe: could not create the report for member id %s", member.id)
            return self._render_report_form(
                values, "Sorry, your report could not be saved. Please tell a coach in person.")

        # Redirect so a page refresh does not send the report twice
        return request.redirect('/matsafe/report?submitted=1')

    def _render_report_form(self, values=None, error=None):
        return request.render('matsafe.report_form', {
            'report_types': REPORT_TYPES,
            'values': values or {},
            'error': error,
        })

    # ------------------------------------------------------------------
    # /matsafe/kiosk : full-screen door page (webcam + Teachable Machine)
    # ------------------------------------------------------------------
    @http.route('/matsafe/kiosk', type='http', auth='public', methods=['GET'])
    def kiosk(self, **kw):
        return request.render('matsafe.kiosk', {'asset_version': self._asset_version()})

    def _asset_version(self):
        """Last change time of our own JS / CSS files, added to their URLs as ?v=...
        Browsers keep Odoo static files for a week; this makes them reload after an edit."""
        names = ('kiosk.js', 'kiosk.css', 'pair_cam.js')
        paths = [file_path('matsafe/static/src/' + name) for name in names]
        return int(max(os.path.getmtime(path) for path in paths))

    # ------------------------------------------------------------------
    # /matsafe/checkin : door check-in, called by the kiosk (JSON-RPC)
    # ------------------------------------------------------------------
    @http.route('/matsafe/checkin', type='jsonrpc', auth='public', methods=['POST'])
    def checkin(self, face_label=None, member_code=None, **kw):
        # A face label wins if both are sent
        if face_label:
            method = 'face'
            member = self._find_member('face_label', face_label)
        else:
            method = 'code'
            member = self._find_member('member_code', member_code)

        # Door rules
        if not member:
            result, reason = 'blocked', "Unknown member"
        elif member.member_state == 'blocked':
            result, reason = 'blocked', "Cleared by coach required"
        elif member.member_state == 'frozen':
            result, reason = 'blocked', "Membership paused"
        else:
            result, reason = 'allowed', False

        # Always keep a line in the door log, also for unknown people
        request.env['matsafe.checkin'].sudo().create({
            'member_id': member.id,
            'method': method,
            'result': result,
            'reason': reason,
        })
        return {'result': result, 'member_name': member.name or '', 'reason': reason or ''}

    # ------------------------------------------------------------------
    # /matsafe/pair-cam?session_id=ID : pair two members with the camera
    # ------------------------------------------------------------------
    def _find_session(self, session_id):
        """matsafe.session with this id (from the URL or the JSON body), else an empty recordset."""
        Session = request.env['matsafe.session']
        try:
            return Session.browse(int(session_id)).exists()
        except (TypeError, ValueError):
            return Session

    # auth='user': opened by a coach from the session form, so a login is required
    @http.route('/matsafe/pair-cam', type='http', auth='user', methods=['GET'])
    def pair_cam(self, session_id=None, **kw):
        return request.render('matsafe.pair_cam', {
            'pair_session': self._find_session(session_id),
            'asset_version': self._asset_version(),
        })

    # ------------------------------------------------------------------
    # /matsafe/pair : save one camera pairing, called by the pairing page (JSON-RPC)
    # ------------------------------------------------------------------
    @http.route('/matsafe/pair', type='jsonrpc', auth='user', methods=['POST'])
    def pair(self, face_label_a=None, face_label_b=None, session_id=None, **kw):
        session = self._find_session(session_id)
        member_a = self._find_member('face_label', face_label_a)
        member_b = self._find_member('face_label', face_label_b)

        if not session:
            reason = "Session not found"
        elif not member_a or not member_b:
            reason = "Unknown member: %s" % (face_label_b if member_a else face_label_a)
        elif member_a == member_b:
            reason = "Two different partners are needed"
        else:
            # No sudo: the logged-in user needs the coach right to create pairings
            request.env['matsafe.pairing'].create({
                'session_id': session.id,
                'member_a_id': member_a.id,
                'member_b_id': member_b.id,
                'source': 'camera',
            })
            return {'result': 'saved', 'member_a_name': member_a.name,
                    'member_b_name': member_b.name, 'reason': ''}
        return {'result': 'error', 'member_a_name': member_a.name or '',
                'member_b_name': member_b.name or '', 'reason': reason}
