"""The 6 cards of /matsafe/report: their texts, their questions and where each one is saved.

The page is drawn from this list and the controller checks the answers against it,
so every question and every choice is written in one place only.
The form never diagnoses anything: it only asks what the member notices.
"""


def _field(name, label, widget, options=None, required=True, short=None, hint=None):
    """One question on a card.
    widget: checks (tick several), choice (pick one), scale (1 to 5), date,
            line (one line of text), text (a few lines), session (class list), photo.
    short:  the label used in the description a coach reads."""
    return {'name': name, 'label': label, 'widget': widget, 'options': options or [],
            'required': required, 'short': short or label, 'hint': hint}


YES_NO = ["Yes", "No"]

REPORT_KINDS = [
    {
        'key': 'skin',
        'title': "Skin issue",
        'arabic': "مشكلة جلدية",
        'examples': "Rash, ringworm, cold sore, infected cut",
        'save_as': 'report',          # a matsafe.report
        'report_type': 'skin',
        'needs_code': True,
        'needs_consent': True,
        'fields': [
            _field('symptoms', "What do you notice?", 'checks', short="Symptoms",
                   options=["Rash", "Itching", "Blister", "Open wound", "Cold sore"]),
            _field('area', "Where on your body?", 'checks', short="Area",
                   options=["Arms", "Legs", "Torso", "Face / head", "Hands / feet"]),
            _field('started', "When did it start?", 'date', short="Started"),
            _field('doctor', "Have you seen a doctor?", 'choice', options=YES_NO, short="Seen a doctor"),
        ],
    },
    {
        'key': 'sick',
        'title': "Feeling sick",
        'arabic': "أشعر بالمرض",
        'examples': "Flu, fever, cold, stomach bug",
        'save_as': 'report',
        'report_type': 'viral',
        'fallback_type': 'other',     # used if matsafe.report has no 'viral' type
        'needs_code': True,
        'needs_consent': True,
        'fields': [
            _field('symptoms', "What do you notice?", 'checks', short="Symptoms",
                   options=["Fever", "Cough", "Sore throat", "Runny nose", "Stomach / vomiting", "Body aches"]),
            _field('started', "When did it start?", 'date', short="Started"),
            _field('doctor', "Have you seen a doctor?", 'choice', options=YES_NO, short="Seen a doctor"),
        ],
    },
    {
        'key': 'injury',
        'title': "Injury",
        'arabic': "إصابة",
        'examples': "Sprain, pulled muscle, cut, joint pain",
        'save_as': 'report',
        'report_type': 'injury',
        'needs_code': True,
        'needs_consent': True,
        'fields': [
            _field('part', "Which body part?", 'choice', short="Body part",
                   options=["Neck", "Shoulder", "Elbow", "Wrist / hand", "Back", "Ribs", "Hip", "Knee",
                            "Ankle / foot", "Other"]),
            _field('how', "How did it happen?", 'choice', short="How",
                   options=["Sparring", "Drilling", "Warm-up", "Outside the gym"]),
            _field('pain', "Pain level", 'scale', short="Pain", options=["1", "2", "3", "4", "5"],
                   hint="1 = mild, 5 = very strong"),
        ],
    },
    {
        'key': 'head',
        'title': "Head knock",
        'arabic': "ضربة على الرأس",
        'examples': "Hit to the head, dizziness, headache after training",
        'save_as': 'report',
        'report_type': 'head',
        'fallback_type': 'injury',    # used if matsafe.report has no 'head' type
        'prefix': "HEAD KNOCK:",      # the description starts with this, so coaches see it at once
        'needs_code': True,
        'needs_consent': True,
        'note': "If you have severe headache, vomiting or confusion, seek medical help now.",
        'fields': [
            _field('what', "What happened?", 'line', short="What happened"),
            _field('symptoms', "What do you notice?", 'checks', short="Symptoms", required=False,
                   options=["Headache", "Dizziness", "Nausea", "Blurry vision", "Confusion"],
                   hint="Leave empty if you notice nothing."),
            _field('when', "When did it happen?", 'date', short="When"),
        ],
    },
    {
        'key': 'unsafe',
        'title': "Unsafe partner / incident",
        'arabic': "شريك غير آمن",
        'examples': "Too rough, ignored taps, unsafe behaviour",
        'save_as': 'coach_inbox',     # a task in the "Coach Inbox" project, never a matsafe.report
        'needs_code': True,
        'needs_consent': False,
        'note': "This goes to the head coach only. It does not change your door check-in.",
        'fields': [
            _field('session', "Which class?", 'session', short="Class"),
            _field('what', "What happened?", 'text', short="What happened"),
            _field('partner', "Partner's name (optional)", 'line', short="Partner named", required=False,
                   hint="Only the head coach sees this."),
        ],
    },
    {
        'key': 'facility',
        'title': "Facility issue",
        'arabic': "مشكلة في المرفق",
        'examples': "Dirty mat, broken equipment, AC not working, no water, bathroom issue",
        'save_as': 'facility',        # a task in the "Facility Issues" project, never a matsafe.report
        'needs_code': False,
        'needs_consent': False,
        'fields': [
            _field('area', "Where is it?", 'choice', short="Area",
                   options=["Mat A", "Mat B", "Changing room", "Showers", "Reception", "Other"]),
            _field('what', "What's wrong?", 'text', short="What's wrong"),
            _field('photo', "Photo (optional)", 'photo', required=False, hint="Image only, up to 5 MB."),
        ],
    },
]

REPORT_KINDS_BY_KEY = {kind['key']: kind for kind in REPORT_KINDS}
