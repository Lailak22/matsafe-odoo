// MatSafe door kiosk: webcam + Teachable Machine model -> POST /matsafe/checkin
(function () {
    'use strict';

    // ---- Settings -------------------------------------------------------
    const MODEL_URL = '/matsafe/static/model/model.json';       // added by Person D
    const METADATA_URL = '/matsafe/static/model/metadata.json'; // added by Person D
    const CHECKIN_URL = '/matsafe/checkin';
    const PREDICT_EVERY_MS = 500;  // one prediction every 500 ms
    const MIN_PROBABILITY = 0.90;  // the top class must be above this...
    const HOLD_MS = 1500;          // ...for 1.5 seconds in a row
    const RESULT_MS = 3000;        // the green / red screen stays 3 seconds
    const IGNORE_MS = 10000;       // the same face is ignored for 10 seconds after a result
    const NOBODY = 'nobody';       // class name that means "no one at the camera"
    const CAMERA_SIZE = 224;       // webcam picture size in pixels = the model's input size (CSS scales it up)
    const DRAW_EVERY_MS = 1000 / 15;  // camera preview at most 15 frames per second
    const SCAN_TEXT = 'Look at the camera';

    // ---- Page elements --------------------------------------------------
    const statusEl = document.getElementById('ms-status');
    const cameraEl = document.getElementById('ms-camera');
    const resultEl = document.getElementById('ms-result');
    const resultTextEl = document.getElementById('ms-result-text');
    const codeForm = document.getElementById('ms-code-form');
    const codeInput = document.getElementById('ms-code-input');

    // ---- State ----------------------------------------------------------
    let model = null;
    let modelPromise = null;  // the model is downloaded once, even if loadModel() is called again
    let webcam = null;
    let loopId = 0;           // each (re)start of the camera loops gets a new id; older loops stop
    let busy = false;         // true while a check-in is being sent or a result is on screen
    let streakLabel = null;   // the face we are currently confirming
    let streakStart = 0;      // when we first saw that face
    const ignoreUntil = {};   // face label -> time until which it is ignored

    function setStatus(text) {
        statusEl.textContent = text;
    }

    // ---- Result screen --------------------------------------------------
    function showResult(allowed, text) {
        resultTextEl.textContent = text;  // textContent: names are never read as HTML
        resultEl.className = 'ms-result ' + (allowed ? 'ms-allowed' : 'ms-blocked');
        resultEl.hidden = false;
    }

    function hideResult() {
        resultEl.hidden = true;
    }

    // ---- Check-in: same endpoint for a face and for a typed code -----------
    async function checkIn(params) {
        if (busy) {
            return;
        }
        busy = true;
        try {
            // Odoo JSON-RPC: the arguments go inside "params", the answer comes back in "result"
            const response = await fetch(CHECKIN_URL, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: params}),
            });
            const data = await response.json();
            if (data.error) {
                throw new Error(data.error.message);
            }
            if (data.result.result === 'allowed') {
                showResult(true, 'Welcome, ' + data.result.member_name);
            } else {
                showResult(false, 'BLOCKED - ' + data.result.reason);
            }
        } catch (error) {
            console.warn('MatSafe: check-in failed', error);
            showResult(false, 'ERROR - No answer from the server');
        }
        if (params.face_label) {
            ignoreUntil[params.face_label] = Date.now() + IGNORE_MS;
        }
        // Back to scanning after 3 seconds
        setTimeout(function () {
            hideResult();
            busy = false;
            resumeScanning();
        }, RESULT_MS);
    }

    // ---- Face logic: called with the top class of every prediction ---------
    function onPrediction(label, probability) {
        const now = Date.now();
        const isFace = label.trim().toLowerCase() !== NOBODY && probability > MIN_PROBABILITY;
        if (busy || !isFace || now < (ignoreUntil[label] || 0)) {
            // Nobody, not sure enough, or this face just got a result: start over
            streakLabel = null;
            if (!busy) {
                setStatus(SCAN_TEXT);
            }
            return;
        }
        if (label !== streakLabel) {
            // A new face: start counting the 1.5 seconds
            streakLabel = label;
            streakStart = now;
            setStatus('Hold still...');
            return;
        }
        if (now - streakStart >= HOLD_MS) {
            // The same face for 1.5 seconds in a row: check in
            streakLabel = null;
            setStatus(SCAN_TEXT);
            checkIn({face_label: label});
        }
    }

    // ---- Webcam loops ---------------------------------------------------
    // Both loops stop while `busy` (the result screen covers the camera) and
    // resumeScanning() starts them again. A loop also stops if a newer one was started.
    let lastDraw = 0;
    function drawLoop(id) {
        if (busy || id !== loopId) {
            return;
        }
        // requestAnimationFrame runs ~60 times a second: only copy a frame 15 times a second
        const now = performance.now();
        if (now - lastDraw >= DRAW_EVERY_MS) {
            lastDraw = now;
            webcam.update();  // copy the current video frame to the canvas
        }
        requestAnimationFrame(function () { drawLoop(id); });
    }

    async function predictLoop(id) {
        if (busy || id !== loopId) {
            return;
        }
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
        if (!busy && id === loopId) {
            setTimeout(function () { predictLoop(id); }, PREDICT_EVERY_MS);
        }
    }

    function resumeScanning() {
        if (!model || !webcam) {
            return;  // model or camera not ready: only the member code box works
        }
        const id = ++loopId;
        setStatus(SCAN_TEXT);
        requestAnimationFrame(function () { drawLoop(id); });
        setTimeout(function () { predictLoop(id); }, PREDICT_EVERY_MS);
    }

    // The first prediction is slow (TensorFlow.js prepares its WebGL code), so do it now
    // on a blank canvas instead of when the first member stands at the door.
    async function warmUp() {
        const blank = document.createElement('canvas');
        blank.width = blank.height = CAMERA_SIZE;
        try {
            await model.predict(blank);
        } catch (error) {
            console.warn('MatSafe: warm-up prediction failed', error);  // not fatal: scanning still works
        }
    }

    function loadModel() {
        if (!modelPromise) {
            modelPromise = tmImage.load(MODEL_URL, METADATA_URL);
        }
        return modelPromise;
    }

    // ---- Start ----------------------------------------------------------
    async function start() {
        // 1. Face model. If the libraries or the model files are missing, the code box still works.
        try {
            setStatus('Loading face model...');
            model = await loadModel();
            await warmUp();
        } catch (error) {
            console.warn('MatSafe: face model not loaded', error);
            setStatus('Face model not loaded, use member code');
            return;
        }
        // 2. Webcam. Browsers only allow it on localhost or https.
        try {
            webcam = new tmImage.Webcam(CAMERA_SIZE, CAMERA_SIZE, true);  // true = mirror, like Teachable Machine
            await webcam.setup();
            await webcam.play();
        } catch (error) {
            console.warn('MatSafe: camera not available', error);
            setStatus('Camera not available, use member code');
            return;
        }
        cameraEl.appendChild(webcam.canvas);
        resumeScanning();
    }

    // ---- Member code fallback -------------------------------------------
    codeForm.addEventListener('submit', function (event) {
        event.preventDefault();
        const code = codeInput.value.trim();
        if (!code || busy) {
            return;
        }
        codeInput.value = '';
        codeInput.blur();  // closes the on-screen keyboard on tablets
        checkIn({member_code: code});
    });

    // For testing without a camera, from the browser console: call
    // matsafeKiosk.onPrediction('Hoor', 0.99) every 500 ms for 2 seconds.
    window.matsafeKiosk = {onPrediction: onPrediction};

    start();
})();
