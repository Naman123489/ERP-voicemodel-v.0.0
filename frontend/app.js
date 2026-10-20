// Worker screen.
// The browser only sends what the worker said and shows what the server answers.
// It never changes stock and never decides by itself that something was saved.

let token = null;
let sessionId = null;
let busy = false;
let devLoginEnabled = false; // set from /api/v1/voice/config


// ---------- talking to the server ----------

// Sends a request and always returns { status, data }.
// status 0 means the server did not answer (offline or timeout).
async function callServer(url, body) {
    const headers = { "Content-Type": "application/json" };
    if (token) {
        headers["Authorization"] = "Bearer " + token;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);

    try {
        const response = await fetch(url, {
            method: "POST",
            headers: headers,
            body: JSON.stringify(body),
            signal: controller.signal
        });
        let data = null;
        try {
            data = await response.json();
        } catch (e) {
            data = null;
        }
        return { status: response.status, data: data };
    } catch (e) {
        return { status: 0, data: null };
    } finally {
        clearTimeout(timer);
    }
}

function errorMessage(result) {
    if (result.status === 0) {
        return "Cannot reach the server.";
    }
    if (result.data && result.data.error && result.data.error.message) {
        return result.data.error.message;
    }
    return "Server error (HTTP " + result.status + ").";
}


// ---------- login ----------

document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();

    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const errorBox = document.getElementById("login-error");
    errorBox.textContent = "";

    // always try Person B's real login first
    let result = await callServer("/api/v1/auth/login", { username: username, password: password });
    const realLoginMissing = result.status === 404 || result.status === 405;

    // developer fallback, only if the real login does not exist and the server allows it (DEV_LOGIN=1)
    if (realLoginMissing && devLoginEnabled) {
        result = await callServer("/api/v1/voice/dev-login", { username: username });
    }
    document.getElementById("password").value = "";

    if (result.status === 200 && result.data && result.data.access_token) {
        token = result.data.access_token;
        sessionId = "session-" + Date.now();
        document.getElementById("dev-banner").hidden = result.data.dev_login !== true;
        document.getElementById("login-screen").hidden = true;
        document.getElementById("work-screen").hidden = false;
        addMessage("system", "Tell me how many bicycles to assemble. For example: 20 cycle assemble karo.");
        return;
    }

    if (result.status === 401) {
        errorBox.textContent = "Wrong username or password.";
    } else if (realLoginMissing) {
        errorBox.textContent = "The login service is not available on this server yet.";
    } else {
        errorBox.textContent = errorMessage(result);
    }
});

document.getElementById("logout-button").addEventListener("click", () => {
    token = null;
    sessionId = null;
    document.getElementById("messages").innerHTML = "";
    document.getElementById("work-screen").hidden = true;
    document.getElementById("login-screen").hidden = false;
});


// ---------- screen helpers ----------

function addMessage(who, text, extraClass, note) {
    const item = document.createElement("li");
    item.className = who;
    if (extraClass) {
        item.classList.add(extraClass);
    }
    item.textContent = text;

    if (note) {
        const small = document.createElement("small");
        small.textContent = note;
        item.appendChild(small);
    }

    const list = document.getElementById("messages");
    list.appendChild(item);
    list.scrollTop = list.scrollHeight;
}

function setStatus(name, text) {
    const status = document.getElementById("status");
    status.className = name;
    status.textContent = text;
}

function setBusy(value) {
    busy = value;
    for (const button of document.querySelectorAll("#work-screen button")) {
        button.disabled = value;
    }
    // keep the mic off when no voice input is possible
    if (!micAvailable()) {
        micButton.disabled = true;
    }
}

function showOrder(state) {
    const info = document.getElementById("order-info");
    if (!state || !state.order_id) {
        info.textContent = "No active order";
        return;
    }
    let text = "Order " + state.order_id + " · target " + state.target_quantity;
    if (state.draft_quantity) {
        text += " · completed " + state.draft_quantity + " (not saved yet)";
    }
    info.textContent = text;
}

// ---------- speaking the reply ----------
// If the server has a TTS engine (Google Cloud TTS, or a local Indic model) we play its audio.
// If that is off or fails, we use the browser voice. The text is always on screen anyway.

let serverSpeech = false;
let currentAudio = null;

// ask the server once which speech services are switched on
fetch("/api/v1/voice/config")
    .then((response) => response.json())
    .then((config) => {
        serverSpeech = config.server_tts_available === true;
        serverRecognition = config.server_stt_available === true;
        devLoginEnabled = config.dev_login_enabled === true;
        updateMicButton();
    })
    .catch(() => {
        serverSpeech = false;
        serverRecognition = false;
    });

function stopSpeaking() {
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
    if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
    }
}

function browserSpeak(text, lang) {
    if (!("speechSynthesis" in window)) {
        return; // no speech output at all, the text is still on screen
    }
    const voice = new SpeechSynthesisUtterance(text);
    voice.lang = lang || "en-IN";
    window.speechSynthesis.speak(voice);
}

async function speak(text, lang) {
    stopSpeaking();
    if (!serverSpeech) {
        browserSpeak(text, lang);
        return;
    }
    try {
        const response = await fetch("/api/v1/voice/speak", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
            body: JSON.stringify({ text: text })
        });
        if (response.status === 503 || response.status === 429) {
            serverSpeech = false; // not set up, or quota/daily limit reached: stop asking, use the browser voice
        }
        if (!response.ok) {
            throw new Error("speak failed with HTTP " + response.status);
        }
        const url = URL.createObjectURL(await response.blob());
        currentAudio = new Audio(url);
        currentAudio.onended = () => URL.revokeObjectURL(url);
        await currentAudio.play();
    } catch (e) {
        console.warn("Server speech failed, using the browser voice:", e);
        browserSpeak(text, lang);
    }
}


// ---------- sending a command ----------

async function sendText(text, confidence) {
    if (busy) {
        return;
    }
    text = text.trim();
    if (!text) {
        setStatus("error", "Type a command first");
        return;
    }

    addMessage("worker", text);
    setBusy(true);
    setStatus("processing", "Processing...");

    const result = await callServer("/api/v1/voice/turn", {
        session_id: sessionId,
        transcript: text,
        confidence: confidence === undefined ? null : confidence
    });

    setBusy(false);

    if (result.status !== 200 || !result.data) {
        addMessage("system", "Problem: " + errorMessage(result) + " Nothing was saved.", "error");
        setStatus("error", "Problem");
        return;
    }

    const reply = result.data;

    // show which backend call happened and what it answered
    let note = null;
    if (reply.backend_response) {
        const backend = reply.backend_response;
        note = backend.operation + " → " + (backend.status_code ? "HTTP " + backend.status_code : "no answer");
    }

    let look = null;
    if (reply.outcome === "saved") {
        look = "success";
    } else if (["error", "not_saved", "uncertain", "insufficient_stock"].includes(reply.outcome)) {
        look = "error";
    }

    addMessage("system", reply.assistant_text, look, note);
    speak(reply.assistant_text, reply.speech && reply.speech.lang);
    showOrder(reply.dialogue_state);
    document.getElementById("confirm-buttons").hidden = !reply.requires_confirmation;

    if (look === "success") {
        setStatus("success", "Saved");
    } else if (look === "error") {
        setStatus("error", "Problem");
    } else if (reply.requires_confirmation) {
        setStatus("confirm", "Please confirm");
    } else {
        setStatus("", "Ready");
    }
}

document.getElementById("message-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.getElementById("message-input");
    const text = input.value;
    input.value = "";
    sendText(text);
});


// ---------- microphone ----------
// If the server has Google Speech-to-Text turned on, we record a short clip and send it there.
// Otherwise we use the browser's own speech recognition. Typing always works.

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const micButton = document.getElementById("mic-button");
const canRecord = !!(navigator.mediaDevices && window.MediaRecorder);
const MAX_RECORDING_MS = 7000; // short clips keep cloud costs low
let serverRecognition = false;
let recorder = null;

function micAvailable() {
    return (serverRecognition && canRecord) || !!SpeechRecognition;
}

function updateMicButton() {
    micButton.disabled = !micAvailable();
    micButton.title = micAvailable() ? "" : "Voice input is not available in this browser. Please type.";
}

function micOn(label) {
    micButton.classList.add("on");
    micButton.textContent = label;
    setStatus("listening", "Listening...");
}

function micOff() {
    micButton.classList.remove("on");
    micButton.textContent = "🎤 Speak";
}

function shortLanguage() {
    // "hi-IN" -> "hi"
    return document.getElementById("speech-language").value.split("-")[0];
}

updateMicButton();

micButton.addEventListener("click", () => {
    if (recorder && recorder.state === "recording") {
        recorder.stop(); // second tap = done speaking
        return;
    }
    stopSpeaking();
    if (serverRecognition && canRecord) {
        recordForServer();
    } else if (SpeechRecognition) {
        listenInBrowser();
    }
});


// --- server (Google Cloud Speech-to-Text) ---

async function uploadAudio(blob) {
    const form = new FormData();
    form.append("audio", blob, "speech.webm");
    form.append("language_hint", shortLanguage());

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
        const response = await fetch("/api/v1/voice/transcribe", {
            method: "POST",
            headers: { "Authorization": "Bearer " + token },
            body: form,
            signal: controller.signal
        });
        let data = null;
        try {
            data = await response.json();
        } catch (e) {
            data = null;
        }
        return { status: response.status, data: data };
    } catch (e) {
        return { status: 0, data: null };
    } finally {
        clearTimeout(timer);
    }
}

function recognitionProblem(result) {
    const code = result.data && result.data.error ? result.data.error.code : "";
    if (result.status === 0) return "Cannot reach the server - please type";
    if (code === "STT_NO_SPEECH" || code === "EMPTY_AUDIO") return "Didn't catch that - try again";
    if (code === "STT_QUOTA" || code === "STT_DAILY_LIMIT") return "Speech limit reached - please type";
    if (code === "STT_UNSUPPORTED_FORMAT") return "This browser's audio format isn't supported - please type";
    if (code === "STT_TIMEOUT") return "Speech service too slow - try again or type";
    return "Speech service not available - please type";
}

async function recordForServer() {
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        setStatus("error", e.name === "NotAllowedError" ? "Microphone blocked - allow it or type" : "No microphone found - please type");
        return;
    }

    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
            chunks.push(event.data);
        }
    };

    recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        micOff();
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        if (blob.size < 1000) {
            setStatus("error", "Didn't hear anything");
            return;
        }

        setStatus("processing", "Recognising...");
        const result = await uploadAudio(blob);
        if (result.status === 200 && result.data && result.data.transcript) {
            // recognised text goes through exactly the same path as typed text
            sendText(result.data.transcript, result.data.confidence);
            return;
        }
        if (result.status === 503 || result.status === 429) {
            serverRecognition = false; // stop paying for failing calls; browser recognition takes over
            updateMicButton();
        }
        setStatus("error", recognitionProblem(result));
    };

    recorder.start();
    micOn("■ Stop");
    setTimeout(() => {
        if (recorder && recorder.state === "recording") {
            recorder.stop();
        }
    }, MAX_RECORDING_MS);
}


// --- browser speech recognition (fallback) ---

function listenInBrowser() {
    const recognition = new SpeechRecognition();
    recognition.lang = document.getElementById("speech-language").value;
    recognition.interimResults = false;
    let heardSomething = false;

    recognition.onresult = (event) => {
        heardSomething = true;
        const best = event.results[0][0];
        // some browsers say 0 when they don't know the confidence
        const confidence = best.confidence > 0 ? best.confidence : null;
        sendText(best.transcript, confidence);
    };

    recognition.onerror = (event) => {
        if (event.error === "not-allowed") {
            setStatus("error", "Microphone blocked - allow it or type");
        } else if (event.error === "no-speech") {
            setStatus("error", "Didn't hear anything");
        } else if (event.error === "network") {
            setStatus("error", "Speech service offline - please type");
        } else {
            setStatus("error", "Mic error: " + event.error);
        }
    };

    recognition.onend = () => {
        micOff();
        if (!heardSomething && document.getElementById("status").className === "listening") {
            setStatus("error", "Didn't hear anything");
        }
    };

    recognition.start();
    micOn("Listening...");
}
