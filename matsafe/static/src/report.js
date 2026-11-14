// MatSafe "Report something" page:
// step 1 pick a card, step 2 show only that card's questions, step 3 check them and send.
// Plain JavaScript, no framework. The controller checks everything again.
(function () {
    'use strict';

    const form = document.getElementById('mr-form');
    if (!form) {
        return;
    }
    // report.css only hides things when this class is set:
    // without JavaScript every question stays visible and the form still works.
    document.documentElement.classList.add('mr-js');

    // ---- Settings -------------------------------------------------------
    const SESSIONS_URL = '/matsafe/report/sessions';
    const MAX_PHOTO_BYTES = 5 * 1024 * 1024;  // same limit as the controller
    const NOT_LISTED = 'other';               // the "Class not listed" choice

    // ---- Page elements --------------------------------------------------
    const kindInputs = form.querySelectorAll('input[name="kind"]');
    const sections = form.querySelectorAll('.mr-kind');
    const changeButton = document.getElementById('mr-change');
    const codeInput = document.getElementById('mr-code');
    const codeField = document.getElementById('mr-code-field');
    const consentInput = document.getElementById('mr-consent');
    const consentField = document.getElementById('mr-consent-field');
    const sessionSelect = form.querySelector('.mr-session');
    const submitButton = document.getElementById('mr-submit');
    const progress = document.getElementById('mr-progress');

    // The 3-step bar at the top: choose / details / send
    function setProgress(step) {
        if (!progress) {
            return;
        }
        progress.dataset.at = step;
        progress.querySelectorAll('li').forEach(function (item) {
            if (item.dataset.step === step) {
                item.setAttribute('aria-current', 'step');
            } else {
                item.removeAttribute('aria-current');
            }
        });
    }

    function chosenKind() {
        return form.querySelector('input[name="kind"]:checked');
    }

    function chosenSection() {
        const chosen = chosenKind();
        return chosen ? form.querySelector('.mr-kind[data-kind="' + chosen.value + '"]') : null;
    }

    // ---- Steps 1 and 2 --------------------------------------------------
    // Show the cards (nothing picked) or the questions of the picked card
    function showStep() {
        const chosen = chosenKind();
        form.dataset.step = chosen ? 'fill' : 'pick';
        // report.css shows / hides the "optional" texts and the consent box from these
        form.dataset.code = chosen && chosen.dataset.code ? '1' : '0';
        form.dataset.consent = chosen && chosen.dataset.consent ? '1' : '0';
        setProgress(chosen ? 'details' : 'choose');

        kindInputs.forEach(function (input) {
            input.closest('.mr-card').classList.toggle('mr-card-on', input === chosen);
        });
        sections.forEach(function (section) {
            const active = chosen !== null && section.dataset.kind === chosen.value;
            section.hidden = !active;
            // Questions of the other cards are switched off, so they are not sent
            section.querySelectorAll('input, select, textarea').forEach(function (element) {
                element.disabled = !active;
            });
        });
        consentInput.disabled = form.dataset.consent !== '1';
        loadSessions();
    }

    kindInputs.forEach(function (input) {
        input.addEventListener('change', function () {
            showStep();
            window.scrollTo(0, 0);
        });
    });

    changeButton.addEventListener('click', function () {
        const chosen = chosenKind();
        if (chosen) {
            chosen.checked = false;
        }
        showStep();
        window.scrollTo(0, 0);
    });

    // ---- "Which class?" list (unsafe partner card) -------------------------
    let sessionsFor = null;  // the member code the list was last loaded for

    async function loadSessions() {
        const chosen = chosenKind();
        const code = codeInput.value.trim();
        if (!sessionSelect || !chosen || chosen.value !== 'unsafe' || code === sessionsFor) {
            return;
        }
        sessionsFor = code;
        let sessions = [];
        if (code) {
            try {
                // Odoo JSON-RPC: the arguments go inside "params", the answer comes back in "result"
                const response = await fetch(SESSIONS_URL, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {member_code: code}}),
                });
                sessions = (await response.json()).result || [];
            } catch (error) {
                // No list: "Class not listed" can still be chosen
                console.warn('MatSafe: class list not loaded', error);
            }
        }
        if (code !== sessionsFor) {
            return;  // the member code changed while we were waiting
        }
        // Keep what was picked (data-selected: picked before an error sent the form back)
        const wanted = sessionSelect.value || sessionSelect.dataset.selected || '';
        sessionSelect.querySelectorAll('option[data-session]').forEach(function (option) {
            option.remove();
        });
        const notListed = sessionSelect.querySelector('option[value="' + NOT_LISTED + '"]');
        sessions.forEach(function (session) {
            const option = document.createElement('option');
            option.value = session.id;
            option.textContent = session.label;  // textContent: never read as HTML
            option.dataset.session = '1';
            sessionSelect.insertBefore(option, notListed);
        });
        sessionSelect.value = wanted;
        if (sessionSelect.value !== wanted) {
            sessionSelect.value = '';  // that class is not in the new list
        }
    }

    // Reload the list when the member code changes (shortly after the last key press)
    let codeTimer = null;
    codeInput.addEventListener('input', function () {
        clearTimeout(codeTimer);
        codeTimer = setTimeout(loadSessions, 400);
    });
    codeInput.addEventListener('change', loadSessions);

    // ---- Step 3: check before sending ---------------------------------------
    // Show (or with an empty message, remove) a problem under one question
    function setProblem(field, message) {
        let problem = field.querySelector('.mr-problem');
        if (!message) {
            if (problem) {
                problem.remove();
            }
            field.classList.remove('mr-field-bad');
            return;
        }
        if (!problem) {
            problem = document.createElement('p');
            problem.className = 'mr-problem';
            field.appendChild(problem);
        }
        problem.textContent = message;
        field.classList.add('mr-field-bad');
    }

    function today() {
        // Local date as YYYY-MM-DD
        const now = new Date();
        return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    }

    // The problem with one question of the picked card, or '' when it is fine
    function questionProblem(field) {
        const widget = field.dataset.widget;
        if (widget === 'checks' || widget === 'choice' || widget === 'scale') {
            const picked = field.querySelector('input:checked') !== null;
            return field.dataset.required && !picked ? 'Please choose an answer.' : '';
        }
        const input = field.querySelector('input, select, textarea');
        if (widget === 'photo') {
            const file = input.files[0];
            if (file && !file.type.startsWith('image/')) {
                return 'Please choose an image file.';
            }
            if (file && file.size > MAX_PHOTO_BYTES) {
                return 'This photo is larger than 5 MB. Please choose a smaller one.';
            }
            return '';
        }
        if (field.dataset.required && !input.value.trim()) {
            return 'Please answer this question.';
        }
        if (widget === 'date' && input.value > today()) {
            return 'Please choose today or an earlier date.';
        }
        return '';
    }

    form.addEventListener('submit', function (event) {
        const chosen = chosenKind();
        const section = chosenSection();
        if (!chosen || !section) {
            event.preventDefault();
            return;
        }
        // [field, problem] for the member code, every question of the card, and the consent box
        const checks = [[codeField, chosen.dataset.code && !codeInput.value.trim() ? 'Please enter your member code.' : '']];
        section.querySelectorAll('.mr-field').forEach(function (field) {
            checks.push([field, questionProblem(field)]);
        });
        checks.push([consentField, chosen.dataset.consent && !consentInput.checked ? 'Please tick this box to send your report.' : '']);

        let firstBad = null;
        checks.forEach(function (check) {
            setProblem(check[0], check[1]);
            if (check[1] && !firstBad) {
                firstBad = check[0];
            }
        });
        if (firstBad) {
            event.preventDefault();
            firstBad.scrollIntoView({behavior: 'smooth', block: 'center'});
            return;
        }
        // No double sending
        setProgress('send');
        submitButton.disabled = true;
        submitButton.textContent = 'Sending...';
    });

    // A problem goes away as soon as its question is answered
    function clearProblem(event) {
        const field = event.target.closest('.mr-field');
        if (field) {
            setProblem(field, '');
        }
    }
    form.addEventListener('input', clearProblem);
    form.addEventListener('change', clearProblem);

    // ---- Start ----------------------------------------------------------
    // Dates: nothing after today
    form.querySelectorAll('input[type="date"]').forEach(function (input) {
        input.max = today();
    });

    // pageshow also runs when the browser's Back button brings the page back
    window.addEventListener('pageshow', function () {
        submitButton.disabled = false;
        submitButton.textContent = 'Send report';
        showStep();
    });
    showStep();
})();
