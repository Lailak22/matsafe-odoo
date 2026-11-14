// MatSafe pairing camera: two faces, one after the other -> POST /matsafe/pair
// The webcam and model part is the same as in kiosk.js.
(function () {
    'use strict';

    // ---- Settings -------------------------------------------------------
    const MODEL_URL = '/matsafe/static/model/model.json';       // added by Person D
    const METADATA_URL = '/matsafe/static/model/metadata.json'; // added by Person D
    const PAIR_URL = '/matsafe/pair';
    const PREDICT_EVERY_MS = 300;    // one prediction every 300 ms
    const MIN_PROBABILITY = 0.90;    // the top class must be above this...
    const HOLD_MS = 1500;            // ...for 1.5 seconds in a row
    const PARTNER2_WAIT_MS = 15000;  // partner 2 has 15 seconds to show up
    const RESULT_MS = 3000;          // the green / red screen stays 3 seconds
    const IGNORE_MS = 10000;         // a saved pair is ignored for 10 seconds
    const NOBODY = 'nobody';         // class name that means "no one at the camera"
    const CAMERA_SIZE = 400;         // webcam picture size in pixels
    const STEP1_TEXT = 'Partner 1 - look at the camera';
    const STEP2_TEXT = 'Partner 2 - look at the camera';

    // ---- Page elements --------------------------------------------------
    const statusEl = document.getElementById('ms-status');
    const partnerEl = document.getElementById('ms-partner');
    const cameraEl = document.getElementById('ms-camera');
    const resultEl = document.getElementById('ms-result');
    const resultTextEl = document.getElementById('ms-result-text');
    const resultDetailEl = document.getElementById('ms-result-detail');
    const stepsEl = document.getElementById('ms-steps');  // the "01 / 02" scoreboard

    // The session comes from the URL (?session_id=...); the server puts it on <body>
    const sessionId = parseInt(document.body.dataset.sessionId, 10);

    // ---- State ----------------------------------------------------------
    let model = null;
    let webcam = null;
    let busy = false;          // true while a pair is being saved or a result is on screen
    let partnerA = null;       // face label of partner 1; null = still waiting for partner 1
    let partner2Timer = null;  // the 15 second countdown for partner 2
    let streakLabel = null;    // the face we are currently confirming
    let streakStart = 0;       // when we first saw that face
    const ignoreUntil = {};    // face label -> time until which it is ignored

    // warn: a problem (no session, no model, no camera), shown as a calm bar
    function setStatus(text, warn) {
        statusEl.textContent = text;
        statusEl.classList.toggle('ms-status-warn', Boolean(warn));
    }

    // Scoreboard: 1 = waiting for partner 1, 2 = waiting for partner 2
    function setStep(step) {
        stepsEl.dataset.at = String(step);
    }

    // Under the camera: partner 1's name tag, or a short message
    function setPartner(text, isName) {
        partnerEl.textContent = text || '';
        partnerEl.className = 'ms-partner' + (text ? (isName ? ' ms-partner-name' : ' ms-partner-msg') : '');
    }

    // ---- Result screen --------------------------------------------------
    // title: "Pair saved" / "Not saved"; detail: the two names or the reason
    function showResult(ok, title, detail) {
        resultTextEl.textContent = title;  // textContent: names are never read as HTML
        resultDetailEl.textContent = detail || '';
        resultEl.className = 'ms-result ' + (ok ? 'ms-saved' : 'ms-blocked');
        resultEl.hidden = false;
    }

    function hideResult() {
        resultEl.hidden = true;
    }

    // ---- Steps ----------------------------------------------------------
    // Back to "Partner 1"
    function startOver(message) {
        clearTimeout(partner2Timer);
        partnerA = null;
        streakLabel = null;
        setPartner(message, false);
        setStep(1);
        setStatus(STEP1_TEXT);
    }

    // A face was seen for 1.5 seconds in a row
    function onFaceConfirmed(label) {
        if (partnerA === null) {
            // Step 1 done: remember partner 1 and wait up to 15 seconds for partner 2
            partnerA = label;
            setPartner(label, true);
            setStep(2);
            setStatus(STEP2_TEXT);
            partner2Timer = setTimeout(function () {
                startOver('No second partner in 15 seconds, start again');
            }, PARTNER2_WAIT_MS);
        } else {
            // Step 2 done: save the pair
            savePair(partnerA, label);
        }
    }

    async function savePair(labelA, labelB) {
        busy = true;
        clearTimeout(partner2Timer);
        try {
            // Odoo JSON-RPC: the arguments go inside "params", the answer comes back in "result"
            const response = await fetch(PAIR_URL, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    jsonrpc: '2.0',
                    method: 'call',
                    params: {face_label_a: labelA, face_label_b: labelB, session_id: sessionId},
                }),
            });
            const data = await response.json();
            if (data.error) {
                throw new Error(data.error.message);
            }
            if (data.result.result === 'saved') {
                showResult(true, 'Pair saved', data.result.member_a_name + ' + ' + data.result.member_b_name);
                // Both are probably still standing there: do not start a new pair with them at once
                ignoreUntil[labelA] = ignoreUntil[labelB] = Date.now() + IGNORE_MS;
            } else {
                showResult(false, 'Not saved', data.result.reason);
            }
        } catch (error) {
            console.warn('MatSafe: pair not saved', error);
            showResult(false, 'Not saved', 'Are you logged in as a coach?');
        }
        // Back to partner 1 after 3 seconds
        setTimeout(function () {
            hideResult();
            busy = false;
            startOver();
        }, RESULT_MS);
    }

    // ---- Face logic: called with the top class of every prediction ---------
    function onPrediction(label, probability) {
        const now = Date.now();
        const isFace = label.trim().toLowerCase() !== NOBODY && probability > MIN_PROBABILITY;
        // Partner 2 must be a different face than partner 1
        const usable = isFace && label !== partnerA && now >= (ignoreUntil[label] || 0);
        if (busy || !usable) {
            streakLabel = null;
            return;
        }
        if (label !== streakLabel) {
            // A new face: start counting the 1.5 seconds
            streakLabel = label;
            streakStart = now;
            return;
        }
        if (now - streakStart >= HOLD_MS) {
            // The same face for 1.5 seconds in a row
            streakLabel = null;
            onFaceConfirmed(label);
        }
    }

    // ---- Webcam loops ---------------------------------------------------
    function drawLoop() {
        webcam.update();  // copy the current video frame to the canvas
        requestAnimationFrame(drawLoop);
    }

    async function predictLoop() {
        if (!busy) {
            try {
                const predictions = await model.predict(webcam.canvas);
                let top = predictions[0];
                for (const prediction of predictions) {
                    if (prediction.probability > top.probability) {
                        top = prediction;
                    }
                }
                onPrediction(top.className, top.probability);
            } catch (error) {
                console.warn('MatSafe: prediction failed', error);
            }
        }
        setTimeout(predictLoop, PREDICT_EVERY_MS);
    }

    // ---- Start ----------------------------------------------------------
    async function start() {
        if (!sessionId) {
            setStatus('Session not found. Open this page from the session form.', true);
            return;
        }
        // 1. Face model
        try {
            setStatus('Loading face model...');
            model = await tmImage.load(MODEL_URL, METADATA_URL);
        } catch (error) {
            console.warn('MatSafe: face model not loaded', error);
            setStatus('Face model not loaded. Add the pair by hand on the session form.', true);
            return;
        }
        // 2. Webcam. Browsers only allow it on localhost or https.
        try {
            webcam = new tmImage.Webcam(CAMERA_SIZE, CAMERA_SIZE, true);  // true = mirror, like Teachable Machine
            await webcam.setup();
            await webcam.play();
        } catch (error) {
            console.warn('MatSafe: camera not available', error);
            setStatus('Camera not available. Add the pair by hand on the session form.', true);
            return;
        }
        cameraEl.appendChild(webcam.canvas);
        startOver();
        requestAnimationFrame(drawLoop);
        predictLoop();
    }

    // For testing without a camera, from the browser console: call
    // matsafePairCam.onPrediction('Hoor', 0.99) every 300 ms for 2 seconds, then the same with another face.
    window.matsafePairCam = {onPrediction: onPrediction};

    start();
})();
