import io
import logging
import os
from datetime import timedelta

from markupsafe import Markup
from PIL import Image

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools.misc import file_path

from .report_kinds import REPORT_KINDS, REPORT_KINDS_BY_KEY

_logger = logging.getLogger(__name__)

# ---- Report form settings ----------------------------------------------
MAX_LINE = 120                       # longest one-line answer
MAX_TEXT = 1000                      # longest free text answer
MAX_PHOTO_BYTES = 5 * 1024 * 1024    # facility photo: 5 MB
MAX_UPLOAD_BYTES = 8 * 1024 * 1024   # bigger requests are refused before they are read
# Image formats we accept (as detected from the file itself) -> (mimetype, file extension)
PHOTO_FORMATS = {
    'JPEG': ('image/jpeg', 'jpg'),
    'PNG': ('image/png', 'png'),
    'GIF': ('image/gif', 'gif'),
    'WEBP': ('image/webp', 'webp'),
}
SESSION_DAYS = 14                    # "which class": classes of the last 14 days
CHECKIN_MATCH_HOURS = 2              # a door check-in this close to a class start counts as attending
NOT_LISTED = 'other'                 # "which class": the "Class not listed" choice
DONE_KEY = 'matsafe_report_done'     # what the thank-you page shows, kept in the visitor's session
# One message for every identity problem, so nobody can test which part was wrong
IDENTITY_ERROR = "We could not match those details. Please check them and try again."
SAVE_ERROR = "Sorry, your report could not be saved. Please tell a coach in person."
# Does not confirm that this member code has a report: anyone can type someone else's code
ALREADY_ERROR = ("Your report could not be saved. If you already sent a report, a coach has it. "
                 "Otherwise please tell a coach in person.")


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
    # /matsafe/report : public "Report something" page (6 cards, see report_kinds.py)
    # ------------------------------------------------------------------
    @http.route('/matsafe/report', type='http', auth='public', website=True,
                methods=['GET', 'POST'], sitemap=False, max_content_length=MAX_UPLOAD_BYTES)
    def report(self, **post):
        if request.httprequest.method == 'GET':
            # After a successful POST we redirect here with ?submitted=1
            if post.get('submitted'):
                done = request.session.get(DONE_KEY) or {}
                if done.get('kind') not in REPORT_KINDS_BY_KEY:
                    return request.redirect('/matsafe/report')
                return request.render('matsafe.report_thanks', {
                    'done': done,
                    'kind': REPORT_KINDS_BY_KEY[done['kind']],
                    'asset_version': self._asset_version(),
                })
            # ?type=skin opens that card directly
            return self._render_report_form({'kind': post.get('type') or post.get('report_type')})

        # request.httprequest.form: checkbox groups send several values under one name
        form = request.httprequest.form
        kind = REPORT_KINDS_BY_KEY.get(form.get('kind'))
        member_code = (form.get('member_code') or '').strip()[:MAX_LINE]
        details = (form.get('details') or '').strip()[:MAX_TEXT]
        # `values` is what the visitor typed: the form is shown again with it after an error
        values = {'kind': kind and kind['key'], 'member_code': member_code, 'details': details,
                  'consent': bool(form.get('consent'))}
        if not kind:
            return self._render_report_form(values, ["Please choose what you are reporting."])

        # 1. Check the answers (the browser checks too, but it is never trusted)
        answers, errors = self._read_answers(kind, values)
        if kind['needs_code'] and not member_code:
            errors.insert(0, "Please enter your member code.")
        if kind['needs_consent'] and not values['consent']:
            errors.append("Please tick the consent box to send your report.")
        photo, photo_error = self._read_photo(kind)
        if photo_error:
            errors.append(photo_error)
        if errors:
            return self._render_report_form(values, errors)

        # 2. Identity check
        member = self._find_member('member_code', member_code)
        if kind['needs_code'] and not member:
            return self._render_report_form(values, [IDENTITY_ERROR])
        if kind['save_as'] == 'coach_inbox':
            answers['session'] = self._chosen_session(member, answers['session'])
            if answers['session'] is None:
                return self._render_report_form(values, [IDENTITY_ERROR])

        # 3. Save
        try:
            # savepoint: if anything fails, undo it all and show a message instead of an error page
            with request.env.cr.savepoint():
                if kind['save_as'] == 'report':
                    done = self._save_report(kind, member, answers, details)
                elif kind['save_as'] == 'coach_inbox':
                    done = self._save_coach_inbox(member, answers, details)
                else:
                    done = self._save_facility(member, answers, details, photo)
        except Exception as error:
            if isinstance(error, UserError) and kind['save_as'] == 'report':
                # matsafe.report allows only one waiting or open report per member
                return self._render_report_form(values, [ALREADY_ERROR])
            _logger.exception("MatSafe: could not save a '%s' report", kind['key'])
            return self._render_report_form(values, [SAVE_ERROR])

        # Redirect so a page refresh does not send the report twice.
        # The thank-you facts travel in the session, never in the URL.
        request.session[DONE_KEY] = dict(done, kind=kind['key'])
        return request.redirect('/matsafe/report?submitted=1')

    def _render_report_form(self, values=None, errors=None):
        values = values or {}
        if values.get('kind') not in REPORT_KINDS_BY_KEY:
            values['kind'] = None
        return request.render('matsafe.report_form', {
            'kinds': REPORT_KINDS,
            'values': values,
            'errors': errors or [],
            'asset_version': self._asset_version(),
        })

    # ---- Reading the form -------------------------------------------------
    def _read_answers(self, kind, values):
        """Read the answers of one card and check them against report_kinds.py.
        Returns (answers, errors). The answers are also copied into `values`."""
        form = request.httprequest.form
        answers, errors = {}, []
        for field in kind['fields']:
            widget = field['widget']
            if widget == 'photo':
                continue  # files are read by _read_photo
            input_name = '%s__%s' % (kind['key'], field['name'])
            if widget == 'checks':
                # Only choices that exist on the card are kept
                value = [option for option in field['options'] if option in form.getlist(input_name)]
            elif widget in ('choice', 'scale'):
                value = form.get(input_name) if form.get(input_name) in field['options'] else ''
            elif widget == 'date':
                value = self._clean_date(form.get(input_name))
            else:  # line, text, session
                value = (form.get(input_name) or '').strip()[:MAX_TEXT if widget == 'text' else MAX_LINE]
            answers[field['name']] = values[input_name] = value
            if field['required'] and not value:
                errors.append("Please answer: %s" % field['label'])
        return answers, errors

    def _clean_date(self, text):
        """'2026-11-14' when text is a real date that is not in the future, else ''."""
        try:
            day = fields.Date.to_date(text)
        except (TypeError, ValueError):
            return ''
        # + 1 day: the visitor's "today" can be one day ahead of the server's (time zones)
        if not day or day > fields.Date.today() + timedelta(days=1):
            return ''
        return fields.Date.to_string(day)

    def _read_photo(self, kind):
        """The optional photo of a facility report.
        Returns ((bytes, mimetype, file name) or None, error message or None)."""
        upload = request.httprequest.files.get('facility__photo')
        if kind['save_as'] != 'facility' or not upload or not upload.filename:
            return None, None
        data = upload.read(MAX_PHOTO_BYTES + 1)
        if not data:
            return None, None
        if len(data) > MAX_PHOTO_BYTES:
            return None, "The photo is larger than 5 MB. Please send a smaller one."
        # Look inside the file: the name and the type sent by the browser prove nothing
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                image_format = image.format
        except Exception:
            image_format = None
        if image_format not in PHOTO_FORMATS:
            return None, "The photo must be a JPG, PNG, GIF or WebP image."
        mimetype, extension = PHOTO_FORMATS[image_format]
        # Our own file name: the visitor's file name is never used
        return (data, mimetype, 'facility-photo.%s' % extension), None

    # ---- "Which class?" on the unsafe partner card ---------------------------
    def _attended_sessions(self, member):
        """Classes of the last 14 days the member was in: on the attendee list, in a pairing,
        or checked in at the door around the class start. Newest first."""
        Session = request.env['matsafe.session'].sudo()
        if not member:
            return Session
        now = fields.Datetime.now()
        since = now - timedelta(days=SESSION_DAYS)
        window = timedelta(hours=CHECKIN_MATCH_HOURS)
        checkins = request.env['matsafe.checkin'].sudo().search([
            ('member_id', '=', member.id), ('result', '=', 'allowed'), ('datetime', '>=', since - window),
        ])

        def attended(session):
            pairings = session.pairing_ids
            return (member in session.attendee_ids
                    or member in (pairings.member_a_id | pairings.member_b_id)
                    or any(abs(checkin.datetime - session.start) <= window for checkin in checkins))

        return Session.search([('start', '>=', since), ('start', '<=', now)]).filtered(attended)

    def _session_label(self, session):
        return "%s - %s" % (session.name, session.start.strftime('%a %d %b'))

    def _chosen_session(self, member, choice):
        """The class picked on the form: a matsafe.session (empty for "Class not listed"),
        or None when it is not one of the member's own classes."""
        if choice == NOT_LISTED:
            return request.env['matsafe.session'].sudo()
        return self._attended_sessions(member).filtered(lambda session: str(session.id) == choice) or None

    @http.route('/matsafe/report/sessions', type='jsonrpc', auth='public', methods=['POST'])
    def report_sessions(self, member_code=None, **kw):
        """Fills the "Which class?" list. An unknown member code gives an empty list, not an error."""
        member = self._find_member('member_code', member_code)
        return [{'id': session.id, 'label': self._session_label(session)}
                for session in self._attended_sessions(member)]

    # ---- Saving: cards 1 to 4 -> matsafe.report ------------------------------
    def _save_report(self, kind, member, answers, details):
        Report = request.env['matsafe.report'].sudo()
        report_type = kind['report_type']
        if report_type not in dict(Report._fields['report_type'].selection):
            report_type = kind.get('fallback_type', 'other')
        # The new report waits in state 'pending' for the member's confirmation;
        # matsafe.report runs its chain itself (action_confirm), we never call it here.
        report = Report.create({
            'member_id': member.id,
            'report_type': report_type,
            'description': self._report_description(kind, answers, details),
        })
        # Read the member again: the thank-you page only says what really happened
        member.invalidate_recordset(['member_state'])
        return {
            'pending': report.state == 'pending',
            'door_paused': member.member_state in ('blocked', 'frozen'),
            'contacts': len(report.contact_ids),
            # Without this email template the chain only leaves a note on each contact
            'emailed': bool(Report.env.ref('matsafe.mail_template_contact_alert', raise_if_not_found=False)),
        }

    def _report_description(self, kind, answers, details):
        """Short text for the coach, for example:
            Type: Injury | Body part: Knee | How: Sparring | Pain: 4/5
            Details: what the member typed"""
        parts, lines = [], []
        for field in kind['fields']:
            value = answers.get(field['name'])
            if field['widget'] in ('line', 'text'):
                # Free text gets its own line
                if value:
                    lines.append("%s: %s" % (field['short'], value))
                continue
            if field['widget'] == 'checks':
                value = ", ".join(value) or "None ticked"
            elif field['widget'] == 'scale':
                value = "%s/5" % value
            parts.append("%s: %s" % (field['short'], value))
        if kind.get('prefix'):
            first_line = "%s %s" % (kind['prefix'], " | ".join(parts))
        else:
            first_line = " | ".join(["Type: %s" % kind['title']] + parts)
        if details:
            lines.append("Details: %s" % details)
        return "\n".join([first_line] + lines)

    # ---- Saving: cards 5 and 6 -> project.task (never a matsafe.report) ------
    def _task_project(self, name, head_coach_only=False):
        """The project with this name; it is created the first time it is needed."""
        # sudo: website visitors have no Project rights
        Project = request.env['project.project'].sudo().with_context(mail_create_nosubscribe=True)
        project = Project.search([('name', '=', name)], limit=1)
        if not project:
            project = Project.create({'name': name})
            if head_coach_only:
                # Private project: only its followers can open it, and the followers are the head coaches
                project.privacy_visibility = 'followers'
                head_coaches = request.env.ref('matsafe.group_head_coach').sudo().user_ids
                project.message_subscribe(partner_ids=head_coaches.partner_id.ids)
        return project

    def _create_task(self, project, name, lines):
        """A task whose description is a list of (label, value) lines.
        The values come from visitors: Markup % escapes them, so they are never read as HTML."""
        description = Markup("<br/>").join(Markup("<b>%s:</b> %s") % line for line in lines if line[1])
        return request.env['project.task'].sudo().with_context(mail_create_nosubscribe=True).create({
            'name': name,
            'project_id': project.id,
            'description': description,
        })

    def _save_coach_inbox(self, member, answers, details):
        session = answers['session']
        self._create_task(
            self._task_project("Coach Inbox", head_coach_only=True),
            "Unsafe partner report - %s" % (session.name or "class not listed"),
            [
                ("Class", self._session_label(session) if session else "Not listed"),
                ("What happened", answers['what']),
                ("Partner named", answers['partner']),
                ("Details", details),
                # Only the code: coaches can look the member up, the name is not copied around
                ("Reporter member code", member.member_code),
            ],
        )
        return {}

    def _save_facility(self, member, answers, details, photo):
        task = self._create_task(
            self._task_project("Facility Issues"),
            # "<area>: <first 40 characters of the text>", on one line
            "%s: %s" % (answers['area'], " ".join(answers['what'].split())[:40]),
            [
                ("Area", answers['area']),
                ("What's wrong", answers['what']),
                ("Details", details),
                ("Member code", member.member_code),  # optional on this card
            ],
        )
        if photo:
            data, mimetype, filename = photo
            request.env['ir.attachment'].sudo().create({
                'name': filename,
                'raw': data,
                'mimetype': mimetype,
                'res_model': 'project.task',
                'res_id': task.id,
            })
        return {'task_ref': task.id, 'photo': bool(photo)}

    # ------------------------------------------------------------------
    # /matsafe/kiosk : full-screen door page (webcam + Teachable Machine)
    # ------------------------------------------------------------------
    @http.route('/matsafe/kiosk', type='http', auth='public', methods=['GET'])
    def kiosk(self, **kw):
        return request.render('matsafe.kiosk', {'asset_version': self._asset_version()})

    def _asset_version(self):
        """Last change time of our own JS / CSS files, added to their URLs as ?v=...
        Browsers keep Odoo static files for a week; this makes them reload after an edit."""
        names = ('kiosk.js', 'kiosk.css', 'pair_cam.js', 'report.js', 'report.css')
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
