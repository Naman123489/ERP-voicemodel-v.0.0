// Worker screen.
// The browser only sends what the worker said and shows what the server answers.
// It never changes stock and never decides by itself that something was saved.
//
// Language: there is no language picker. The server works out from each command
// whether the worker spoke English, Hindi or Marathi and replies in that language
// (reply.response_language). This page follows it for its own labels and voice.

let token = null;
let sessionId = null;
let busy = false;
let devLoginEnabled = false; // set from /api/v1/voice/config
let language = null;         // "en" | "hi" | "mr" once the server has heard the worker

const SPEECH_TAGS = { en: "en-IN", hi: "hi-IN", mr: "mr-IN" };
const LANGUAGE_NAMES = { en: "English", hi: "हिन्दी", mr: "मराठी" };

// Texts this page shows by itself (the assistant's replies come from the server).
const UI_TEXT = {
    en: {
        ready: "Ready", processing: "Processing...", listening: "Listening...", recognising: "Recognising...",
        saved: "Saved", problem: "Problem", confirm: "Please confirm", typeFirst: "Type a command first",
        notHeard: "Didn't hear anything", micBlocked: "Microphone blocked - allow it or type",
        noMic: "No microphone found - please type", speechOffline: "Speech service offline - please type",
        cantReach: "Cannot reach the server - please type", notCaught: "Didn't catch that - please say it again",
        speechLimit: "Speech limit reached - please type", badFormat: "This browser's audio format isn't supported - please type",
        speechSlow: "Speech service too slow - try again or type", speechUnavailable: "Speech service not available - please type",
        problemPrefix: "Problem: ", nothingSaved: " Nothing was saved.", noOrder: "No active order",
        order: "Order", target: "target", completed: "completed", notSavedYet: "(not saved yet)",
        yesSave: "Yes, save", noChange: "No, change", cancel: "Cancel", finished: "Assembly finished",
        speak: "🎤 Speak", stop: "■ Stop", send: "Send", logout: "Log out", placeholder: "Type: 20 cycle assemble karo",
    },
    hi: {
        ready: "तैयार", processing: "समझ रहा हूँ...", listening: "सुन रहा हूँ...", recognising: "पहचान रहा हूँ...",
        saved: "सेव हो गया", problem: "समस्या", confirm: "कृपया पुष्टि करें", typeFirst: "पहले कोई कमांड लिखिए",
        notHeard: "कुछ सुनाई नहीं दिया", micBlocked: "माइक्रोफ़ोन बंद है - अनुमति दीजिए या लिखिए",
        noMic: "माइक्रोफ़ोन नहीं मिला - कृपया लिखिए", speechOffline: "आवाज़ सेवा ऑफ़लाइन है - कृपया लिखिए",
        cantReach: "सर्वर से संपर्क नहीं हुआ - कृपया लिखिए", notCaught: "समझ नहीं आया - कृपया दोबारा बोलिए",
        speechLimit: "आवाज़ की सीमा पूरी हो गई - कृपया लिखिए", badFormat: "इस ब्राउज़र का ऑडियो फ़ॉर्मेट समर्थित नहीं है - कृपया लिखिए",
        speechSlow: "आवाज़ सेवा धीमी है - दोबारा कोशिश करें या लिखिए", speechUnavailable: "आवाज़ सेवा उपलब्ध नहीं है - कृपया लिखिए",
        problemPrefix: "समस्या: ", nothingSaved: " कुछ भी सेव नहीं हुआ।", noOrder: "कोई ऑर्डर नहीं चल रहा",
        order: "ऑर्डर", target: "टारगेट", completed: "पूरी हुईं", notSavedYet: "(अभी सेव नहीं)",
        yesSave: "हाँ, सेव करो", noChange: "नहीं, बदलो", cancel: "रद्द करो", finished: "असेंबली हो गई",
        speak: "🎤 बोलिए", stop: "■ रोकें", send: "भेजें", logout: "लॉग आउट", placeholder: "लिखिए: बीस साइकिल असेंबल करो",
    },
    mr: {
        ready: "तयार", processing: "समजून घेत आहे...", listening: "ऐकत आहे...", recognising: "ओळखत आहे...",
        saved: "सेव्ह झाले", problem: "अडचण", confirm: "कृपया खात्री करा", typeFirst: "आधी एखादी कमांड लिहा",
        notHeard: "काहीच ऐकू आले नाही", micBlocked: "मायक्रोफोन बंद आहे - परवानगी द्या किंवा लिहा",
        noMic: "मायक्रोफोन सापडला नाही - कृपया लिहा", speechOffline: "आवाज सेवा ऑफलाइन आहे - कृपया लिहा",
        cantReach: "सर्व्हरशी संपर्क झाला नाही - कृपया लिहा", notCaught: "समजले नाही - कृपया पुन्हा सांगा",
        speechLimit: "आवाजाची मर्यादा संपली - कृपया लिहा", badFormat: "या ब्राउझरचा ऑडिओ फॉरमॅट चालत नाही - कृपया लिहा",
        speechSlow: "आवाज सेवा संथ आहे - पुन्हा प्रयत्न करा किंवा लिहा", speechUnavailable: "आवाज सेवा उपलब्ध नाही - कृपया लिहा",
        problemPrefix: "अडचण: ", nothingSaved: " काहीही सेव्ह झाले नाही.", noOrder: "कोणतीही ऑर्डर सुरू नाही",
        order: "ऑर्डर", target: "लक्ष्य", completed: "पूर्ण", notSavedYet: "(अजून सेव्ह नाही)",
        yesSave: "हो, सेव्ह करा", noChange: "नाही, बदला", cancel: "रद्द करा", finished: "असेंबली झाली",
        speak: "🎤 बोला", stop: "■ थांबा", send: "पाठवा", logout: "लॉग आउट", placeholder: "लिहा: वीस सायकली असेंबल करा",
    },
};

function ui(key) {
    return (UI_TEXT[language] || UI_TEXT.en)[key];
}

// Put this page's own labels in the conversation's language.
function applyLanguage() {
    const badge = document.getElementById("language-badge");
    badge.hidden = !language;
    badge.textContent = language ? LANGUAGE_NAMES[language] : "";
    document.getElementById("yes-button").textContent = ui("yesSave");
    document.getElementById("no-button").textContent = ui("noChange");
    document.getElementById("cancel-button").textContent = ui("cancel");
    document.getElementById("finished-button").textContent = ui("finished");
    document.getElementById("send-button").textContent = ui("send");
    document.getElementById("logout-button").textContent = ui("logout");
    document.getElementById("message-input").placeholder = ui("placeholder");
    if (!micButton.classList.contains("on")) {
        micButton.textContent = ui("speak");
    }
}


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
        return ui("cantReach");
    }
    if (result.data && result.data.error && result.data.error.message) {
        return result.data.error.message;
    }
    return "HTTP " + result.status;
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
        language = null;
        applyLanguage();
        document.getElementById("dev-banner").hidden = result.data.dev_login !== true;
        document.getElementById("login-screen").hidden = true;
        document.getElementById("work-screen").hidden = false;
        // first greeting in all three languages: we don't know the worker's language yet
        addMessage("system", "Speak in English, हिन्दी or मराठी. For example: 20 cycle assemble karo / बीस साइकिल असेंबल करो / वीस सायकली असेंबल करा.");
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
    language = null;
    applyLanguage();
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
        info.textContent = ui("noOrder");
        return;
    }
    let text = ui("order") + " " + state.order_id + " · " + ui("target") + " " + state.target_quantity;
    if (state.draft_quantity) {
        text += " · " + ui("completed") + " " + state.draft_quantity + " " + ui("notSavedYet");
    }
    info.textContent = text;
}

// ---------- speaking the reply ----------
// If the server has a TTS engine (Google Cloud TTS, or a local Indic model) we play its audio
// in the reply's language. If that is off or fails, we use the browser voice. The text is always on screen.

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

function browserSpeak(text, replyLanguage) {
    if (!("speechSynthesis" in window)) {
        return; // no speech output at all, the text is still on screen
    }
    const voice = new SpeechSynthesisUtterance(text);
    voice.lang = SPEECH_TAGS[replyLanguage] || "en-IN";
    window.speechSynthesis.speak(voice);
}

async function speak(text, replyLanguage) {
    stopSpeaking();
    if (!serverSpeech) {
        browserSpeak(text, replyLanguage);
        return;
    }
    try {
        const response = await fetch("/api/v1/voice/speak", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
            body: JSON.stringify({ text: text, language: replyLanguage })
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
        browserSpeak(text, replyLanguage);
    }
}


// ---------- sending a command ----------

// recognizedLanguage: the language Google heard in the audio ("en" | "hi" | "mr"), if any.
async function sendText(text, confidence, recognizedLanguage) {
    if (busy) {
        return;
    }
    text = text.trim();
    if (!text) {
        setStatus("error", ui("typeFirst"));
        return;
    }

    addMessage("worker", text);
    setBusy(true);
    setStatus("processing", ui("processing"));

    const result = await callServer("/api/v1/voice/turn", {
        session_id: sessionId,
        transcript: text,
        language: recognizedLanguage || "unknown",
        confidence: confidence === undefined ? null : confidence
    });

    setBusy(false);

    if (result.status !== 200 || !result.data) {
        addMessage("system", ui("problemPrefix") + errorMessage(result) + ui("nothingSaved"), "error");
        setStatus("error", ui("problem"));
        return;
    }

    const reply = result.data;
    language = reply.response_language || language;
    applyLanguage();

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
    speak(reply.assistant_text, reply.response_language);
    showOrder(reply.dialogue_state);
    document.getElementById("confirm-buttons").hidden = !reply.requires_confirmation;

    if (look === "success") {
        setStatus("success", ui("saved"));
    } else if (look === "error") {
        setStatus("error", ui("problem"));
    } else if (reply.requires_confirmation) {
        setStatus("confirm", ui("confirm"));
    } else {
        setStatus("", ui("ready"));
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
// If the server has Google Speech-to-Text turned on, we record a short clip and send it there;
// Google tells us which of English, Hindi or Marathi it heard. Otherwise we use the browser's
// own speech recognition, which needs one language up front (the conversation's, else Hindi).
// Typing always works.

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
    setStatus("listening", ui("listening"));
}

function micOff() {
    micButton.classList.remove("on");
    micButton.textContent = ui("speak");
}

updateMicButton();
applyLanguage();

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


// --- server (Google Cloud Speech-to-Text, automatic language) ---

async function uploadAudio(blob) {
    const form = new FormData();
    form.append("audio", blob, "speech.webm");
    if (language) {
        // not a choice by the worker: the conversation's language is tried first,
        // and Google still checks the other two languages
        form.append("language_hint", language);
    }

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
    if (result.status === 0) return ui("cantReach");
    if (code === "STT_NO_SPEECH" || code === "EMPTY_AUDIO") return ui("notCaught");
    if (code === "STT_QUOTA" || code === "STT_DAILY_LIMIT") return ui("speechLimit");
    if (code === "STT_UNSUPPORTED_FORMAT") return ui("badFormat");
    if (code === "STT_TIMEOUT") return ui("speechSlow");
    return ui("speechUnavailable");
}

async function recordForServer() {
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        setStatus("error", e.name === "NotAllowedError" ? ui("micBlocked") : ui("noMic"));
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
            setStatus("error", ui("notHeard"));
            return;
        }

        setStatus("processing", ui("recognising"));
        const result = await uploadAudio(blob);
        if (result.status === 200 && result.data && result.data.transcript) {
            // recognised text goes through exactly the same path as typed text,
            // plus the language Google heard in the audio
            sendText(result.data.transcript, result.data.confidence, result.data.detected_language);
            return;
        }
        if (result.status === 503 || result.status === 429) {
            serverRecognition = false; // stop paying for failing calls; browser recognition takes over
            updateMicButton();
        }
        setStatus("error", recognitionProblem(result));
    };

    recorder.start();
    micOn(ui("stop"));
    setTimeout(() => {
        if (recorder && recorder.state === "recording") {
            recorder.stop();
        }
    }, MAX_RECORDING_MS);
}


// --- browser speech recognition (fallback, cannot detect the language itself) ---

function listenInBrowser() {
    const recognition = new SpeechRecognition();
    recognition.lang = SPEECH_TAGS[language] || "hi-IN";
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
            setStatus("error", ui("micBlocked"));
        } else if (event.error === "no-speech") {
            setStatus("error", ui("notHeard"));
        } else if (event.error === "network") {
            setStatus("error", ui("speechOffline"));
        } else {
            setStatus("error", ui("speechUnavailable") + " (" + event.error + ")");
        }
    };

    recognition.onend = () => {
        micOff();
        if (!heardSomething && document.getElementById("status").className === "listening") {
            setStatus("error", ui("notHeard"));
        }
    };

    recognition.start();
    micOn(ui("listening"));
}
