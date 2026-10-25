"""Voice API tests. Person B's production API is replaced by an httpx.MockTransport
that returns contract-shaped responses and records every request we send."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import voice
from app.main import app
from app.services import speech_to_text

TOKEN = "Bearer test-token-worker01"


class FakeProductionApi:
    """Records requests; responses can be overridden per operation."""

    def __init__(self, target_max=20):
        self.calls = []
        self.target_max = target_max
        self.overrides = {}  # operation -> callable(request) -> httpx.Response | raises

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        path = request.url.path
        op = ("confirm" if path.endswith("/confirm-completion") else
              "draft" if path.endswith("/completion-draft") else "order")
        self.calls.append({"op": op, "path": path, "body": body, "headers": dict(request.headers)})
        if op in self.overrides:
            return self.overrides[op](request)
        if op == "order":
            target = body["target_quantity"]
            if target > self.target_max:
                return httpx.Response(409, json={"error": {
                    "code": "INSUFFICIENT_STOCK", "message": "Not enough wheels.",
                    "details": {"component_id": "wheel", "maximum_feasible_quantity": self.target_max}}})
            return httpx.Response(201, json={
                "order_id": "order-001", "product_id": "bicycle-standard", "target_quantity": target,
                "unit": "piece", "status": "created", "maximum_feasible_quantity": self.target_max,
                "inventory_changed": False, "created_at": "2026-10-18T12:00:00Z"})
        if op == "draft":
            n = body["actual_completed_quantity"]
            return httpx.Response(200, json={
                "draft_id": f"draft-{len(self.ops('draft')):03d}", "order_id": "order-001", "target_quantity": 20,
                "actual_completed_quantity": n, "unit": "piece", "status": "pending_confirmation",
                "summary_text": "", "inventory_changed": False})
        drafts = self.ops("draft")
        n = drafts[-1]["body"]["actual_completed_quantity"]
        return httpx.Response(200, json={
            "order_id": "order-001", "status": "completed", "actual_completed_quantity": n, "unit": "piece",
            "inventory_updated": True, "finished_bicycles_added": n, "consumption_basis": "recipe_standard",
            "message": f"Saved. {n} bicycles added to stock."})

    def ops(self, op):
        return [c for c in self.calls if c["op"] == op]


@pytest.fixture()
def backend():
    fake = FakeProductionApi()
    client = voice.ProductionApiClient("http://backend.test/api/v1", transport=httpx.MockTransport(fake))
    app.dependency_overrides[voice.get_production_client] = lambda: client
    voice.reset_sessions()
    yield fake
    app.dependency_overrides.clear()
    voice.reset_sessions()


@pytest.fixture()
def api():
    return TestClient(app)


class Worker:
    def __init__(self, api, token=TOKEN):
        self.api, self.token = api, token
        self.session_id = f"session-{uuid.uuid4().hex}"

    def say(self, text, **extra):
        res = self.api.post("/api/v1/voice/turn", headers={"Authorization": self.token},
                            json={"session_id": self.session_id, "transcript": text, "language": "mixed", **extra})
        assert res.status_code == 200, res.text
        return res.json()


# --- request validation and auth -----------------------------------------------

def test_turn_requires_bearer_token(api, backend):
    res = api.post("/api/v1/voice/turn", json={"session_id": "s", "transcript": "haan"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"
    assert backend.calls == []


@pytest.mark.parametrize("payload", [
    {"transcript": "x"},
    {"session_id": "", "transcript": "x"},
    {"session_id": "s", "transcript": "x" * 501},
    {"session_id": "s", "transcript": "x", "confidence": 1.5},
    ["not", "an", "object"],
])
def test_turn_validation_uses_error_envelope(api, backend, payload):
    res = api.post("/api/v1/voice/turn", headers={"Authorization": TOKEN}, json=payload)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_turn_rejects_non_json(api, backend):
    res = api.post("/api/v1/voice/turn", headers={"Authorization": TOKEN, "Content-Type": "application/json"},
                   content=b"{not json")
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_JSON"


def test_session_cannot_be_used_by_another_login(api, backend):
    w = Worker(api)
    w.say("hello")
    intruder = Worker(api, token="Bearer someone-else")
    intruder.session_id = w.session_id
    res = api.post("/api/v1/voice/turn", headers={"Authorization": intruder.token},
                   json={"session_id": w.session_id, "transcript": "haan"})
    assert res.status_code == 403


# --- contract shape and the full flow ---------------------------------------------

def test_response_contains_contract_fields(api, backend):
    r = Worker(api).say("Assemble 20 bicycles.")
    for key in ("session_id", "intent", "slots", "assistant_text", "requires_confirmation", "suggested_action"):
        assert key in r
    assert r["intent"] == "create_production_order"
    assert r["slots"] == {"product_id": "bicycle-standard", "target_quantity": 20, "unit": "piece"}
    assert r["suggested_action"] == "create_order"
    assert r["assistant_text"] == "Assembling 20 bicycles. All parts are available. You can start."
    assert r["backend_response"]["status_code"] == 201
    assert r["parse"]["original_text"] == "Assemble 20 bicycles."
    assert r["parse"]["confidence_available"] is False
    assert r["speech"]["text"] == r["assistant_text"]


def test_full_flow_sends_contract_requests_in_order(api, backend):
    w = Worker(api)
    w.say("Assemble 20 bicycles.")
    assert w.say("Assembly done")["assistant_text"] == "How many bicycles did you assemble?"
    r = w.say("18")
    assert r["assistant_text"] == "18 bicycles assembled against 20. Shall I save?"
    assert r["requires_confirmation"] is True
    assert backend.ops("confirm") == []  # nothing finalized before explicit confirmation
    r = w.say("Yes, save it.")
    assert r["outcome"] == "saved"
    assert r["assistant_text"] == "Saved. 18 bicycles added to stock."

    order, draft, confirm = backend.calls
    assert order["path"] == "/api/v1/production/orders"
    assert order["body"] == {"product_id": "bicycle-standard", "target_quantity": 20, "unit": "piece"}
    assert draft["path"] == "/api/v1/production/orders/order-001/completion-draft"
    assert draft["body"] == {"actual_completed_quantity": 18, "unit": "piece"}
    assert confirm["path"] == "/api/v1/production/orders/order-001/confirm-completion"
    assert confirm["body"] == {"draft_id": "draft-001", "confirmed": True}
    assert confirm["headers"]["idempotency-key"]
    assert all(c["headers"]["authorization"] == TOKEN for c in backend.calls)


def test_correction_sends_new_draft_and_new_confirmation(api, backend):
    w = Worker(api)
    w.say("Assemble 20 bicycles."); w.say("assembly done"); w.say("18")
    r = w.say("nahi 17")
    assert r["assistant_text"] == "17 bicycles assembled against 20. Shall I save?"
    w.say("haan")
    assert [d["body"]["actual_completed_quantity"] for d in backend.ops("draft")] == [18, 17]
    assert backend.ops("confirm")[0]["body"]["draft_id"] == "draft-002"


def test_cancel_never_calls_confirmation(api, backend):
    w = Worker(api)
    w.say("Assemble 20 bicycles."); w.say("assembly done"); w.say("18")
    r = w.say("No, cancel.")
    assert r["backend_response"] is None
    assert backend.ops("confirm") == []


@pytest.mark.parametrize("text", ["", "hmm", "20 ya 25", "yes I want to assemble 20 bicycles"])
def test_unclear_speech_takes_no_backend_action(api, backend, text):
    r = Worker(api).say(text)
    assert r["suggested_action"] == "ask_clarification"
    assert backend.calls == []


def test_confirm_without_draft_calls_nothing(api, backend):
    w = Worker(api)
    w.say("Assemble 20 bicycles.")
    r = w.say("Yes, save it.")
    assert "nothing to confirm" in r["assistant_text"].lower()
    assert backend.ops("confirm") == []


def test_insufficient_stock_from_backend(api, backend):
    backend.target_max = 15
    r = Worker(api).say("Assemble 20 bicycles.")
    assert r["outcome"] == "insufficient_stock"
    assert "up to 15" in r["assistant_text"]
    assert r["backend_response"]["status_code"] == 409


# --- backend errors never produce success -----------------------------------------

def _raise(exc):
    def handler(request):
        raise exc(f"simulated: {request.url}", request=request)
    return handler


@pytest.mark.parametrize("override,label", [
    (_raise(httpx.ReadTimeout), "timeout"),
    (_raise(httpx.ConnectError), "unreachable"),
    (lambda r: httpx.Response(500, json={"error": {"code": "DB", "message": "boom"}}), None),
    (lambda r: httpx.Response(200, content=b"<html>oops</html>"), "invalid_json"),
    (lambda r: httpx.Response(200, json={"status": "completed", "inventory_updated": False}), None),
    (lambda r: httpx.Response(409, json={"error": {"code": "INSUFFICIENT_STOCK", "message": "short"}}), None),
    (lambda r: httpx.Response(404, json={"detail": "Not Found"}), None),
])
def test_failed_confirmation_is_never_reported_as_saved(api, backend, override, label):
    w = Worker(api)
    w.say("Assemble 20 bicycles."); w.say("assembly done"); w.say("18")
    backend.overrides["confirm"] = override
    r = w.say("haan")
    assert r["outcome"] != "saved"
    assert not r["assistant_text"].lower().startswith("saved")
    assert "added to stock" not in r["assistant_text"]
    if label:
        assert r["backend_response"]["error"] == label


def test_timeout_then_retry_reuses_idempotency_key(api, backend):
    w = Worker(api)
    w.say("Assemble 20 bicycles."); w.say("assembly done"); w.say("18")
    backend.overrides["confirm"] = _raise(httpx.ReadTimeout)
    r = w.say("haan")
    assert r["outcome"] == "uncertain"
    assert "not shown as saved" in r["assistant_text"]
    del backend.overrides["confirm"]
    r = w.say("haan")
    assert r["outcome"] == "saved"
    keys = [c["headers"]["idempotency-key"] for c in backend.ops("confirm")]
    assert len(keys) == 2 and keys[0] == keys[1]


def test_missing_production_routes_are_reported(api, backend):
    backend.overrides["order"] = lambda r: httpx.Response(404, json={"detail": "Not Found"})
    r = Worker(api).say("Assemble 20 bicycles.")
    assert "not available on the production server" in r["assistant_text"]
    assert r["dialogue_state"]["state"] == "idle"


def test_unreachable_backend_on_order(api, backend):
    backend.overrides["order"] = _raise(httpx.ConnectError)
    r = Worker(api).say("Assemble 20 bicycles.")
    assert r["outcome"] == "error"
    assert "could not reach" in r["assistant_text"].lower()


# --- reset, config, transcription, static UI ---------------------------------------

def test_reset_forgets_dialogue(api, backend):
    w = Worker(api)
    w.say("Assemble 20 bicycles.")
    res = api.post("/api/v1/voice/reset", headers={"Authorization": TOKEN}, json={"session_id": w.session_id})
    assert res.status_code == 200 and res.json()["reset"] is True
    assert w.say("hello")["dialogue_state"]["state"] == "idle"


def test_transcribe_requires_login(api):
    res = api.post("/api/v1/voice/transcribe", files={"audio": ("a.webm", b"\x00", "audio/webm")})
    assert res.status_code == 401


def test_transcribe_without_engine_returns_503(api):
    speech_to_text.set_engine(None)
    res = api.post("/api/v1/voice/transcribe", headers={"Authorization": TOKEN}, files={"audio": ("a.webm", b"\x00\x01", "audio/webm")})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "STT_UNAVAILABLE"


def test_transcribe_with_fake_engine_and_validation(api):
    class FakeEngine:
        name = "fake"

        def transcribe(self, audio, language_hint=None):
            return speech_to_text.TranscriptionResult("20 cycle assemble karo", "mixed", None)

    speech_to_text.set_engine(FakeEngine())
    try:
        ok = api.post("/api/v1/voice/transcribe", headers={"Authorization": TOKEN}, files={"audio": ("a.webm", b"\x00", "audio/webm")},
                      data={"language_hint": "hi"})
        assert ok.json() == {"transcript": "20 cycle assemble karo", "detected_language": "mixed", "confidence": None}
        bad_hint = api.post("/api/v1/voice/transcribe", headers={"Authorization": TOKEN}, files={"audio": ("a.webm", b"\x00", "audio/webm")},
                            data={"language_hint": "fr"})
        assert bad_hint.status_code == 422
        empty = api.post("/api/v1/voice/transcribe", headers={"Authorization": TOKEN}, files={"audio": ("a.webm", b"", "audio/webm")})
        assert empty.status_code == 422
    finally:
        speech_to_text.set_engine(None)


def test_config_and_frontend_are_served(api):
    assert api.get("/api/v1/voice/config").json()["server_stt_available"] is False
    assert "Bicycle Assembly ERP" in api.get("/").text
    assert api.get("/app.js").status_code == 200
    assert api.get("/styles.css").status_code == 200


# --- text-to-speech ------------------------------------------------------------

import io
import sys
import types
import wave
from contextlib import nullcontext

from app.services import text_to_speech


class FakeTTSEngine:
    name = "fake-tts"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def synthesize(self, text, language):
        self.calls.append((text, language))
        if self.fail:
            raise text_to_speech.TextToSpeechFailed("model crashed")
        return [0.0, 0.5, -0.5, 1.0] * 100, 16000, {"hi": "Divya", "mr": "Sunita"}.get(language, "Mary")


@pytest.fixture()
def tts_engine():
    engine = FakeTTSEngine()
    text_to_speech.set_engine(engine)
    yield engine
    text_to_speech.set_engine(None)


def speak(api, text, token=TOKEN, **extra):
    headers = {"Authorization": token} if token else {}
    return api.post("/api/v1/voice/speak", headers=headers, json={"text": text, **extra})


def test_speak_returns_playable_wav(api, tts_engine):
    res = speak(api, "18 bicycles assembled against 20. Shall I save?")
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(res.content)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 400)
    assert res.headers["x-tts-engine"] == "fake-tts"
    assert res.headers["x-tts-language"] == "en"


@pytest.mark.parametrize("text,language,voice", [
    ("बीस साइकिल असेंबल हो गई है", "hi", "Divya"),
    ("वीस सायकली पूर्ण झाल्या आहेत", "mr", "Sunita"),
    ("Saved. 18 bicycles added to stock.", "en", "Mary"),
])
def test_speak_picks_hindi_marathi_or_english_voice(api, tts_engine, text, language, voice):
    res = speak(api, text)
    assert res.headers["x-tts-language"] == language
    assert res.headers["x-tts-voice"] == voice


def test_speak_language_hint_picks_hindi_or_marathi_voice(api, tts_engine):
    # Devanagari is shared by Hindi and Marathi: the reply language decides the voice.
    assert speak(api, "ठीक आहे", language="mr").headers["x-tts-language"] == "mr"
    assert speak(api, "ठीक है", language="hi").headers["x-tts-language"] == "hi"


def test_speak_never_reads_text_with_a_voice_for_another_script(api, tts_engine):
    # A Hindi voice is not used for Latin text, and the English voice not for Devanagari.
    assert speak(api, "Saved. 18 bicycles added to stock.", language="hi").headers["x-tts-language"] == "en"
    assert speak(api, "सेव हो गया।", language="en").headers["x-tts-language"] == "hi"


def test_repeated_sentence_is_generated_once(api, tts_engine):
    speak(api, "How many bicycles did you assemble?")
    speak(api, "How many bicycles did you assemble?")
    assert len(tts_engine.calls) == 1


def test_speak_without_server_engine_is_503_and_ui_falls_back(api):
    text_to_speech.set_engine(None)
    res = speak(api, "hello")
    assert res.status_code == 503 and res.json()["error"]["code"] == "TTS_UNAVAILABLE"
    assert api.get("/api/v1/voice/config").json()["server_tts_available"] is False


def test_speak_engine_failure_is_500_with_error_envelope(api):
    text_to_speech.set_engine(FakeTTSEngine(fail=True))
    try:
        res = speak(api, "hello")
        assert res.status_code == 500 and res.json()["error"]["code"] == "TTS_FAILED"
    finally:
        text_to_speech.set_engine(None)


@pytest.mark.parametrize("samples", [[], [float("nan")] * 10])
def test_empty_or_broken_audio_is_an_error(api, samples):
    class Broken(FakeTTSEngine):
        def synthesize(self, text, language):
            return samples, 16000, "x"

    text_to_speech.set_engine(Broken())
    try:
        assert speak(api, "hello").json()["error"]["code"] == "TTS_FAILED"
    finally:
        text_to_speech.set_engine(None)


def test_speak_requires_login_and_validates(api, tts_engine):
    assert speak(api, "hello", token=None).status_code == 401
    assert speak(api, "").status_code == 422
    assert speak(api, "x" * 501).status_code == 422


def test_tts_failure_does_not_affect_voice_turn(api, backend):
    text_to_speech.set_engine(FakeTTSEngine(fail=True))
    try:
        r = Worker(api).say("Assemble 20 bicycles.")
        assert r["assistant_text"].startswith("Assembling 20 bicycles")
        assert r["speech"]["server_audio"] is True and r["speech"]["text"] == r["assistant_text"]
    finally:
        text_to_speech.set_engine(None)


def test_tts_module_is_separate_from_commands_and_production():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(text_to_speech.__file__).read_text(encoding="utf-8"))
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    forbidden = ("app.api", "app.db", "app.services.dialogue_manager", "app.services.inventory",
                 "app.services.production_service", "httpx", "requests")
    assert not [m for m in imported if m and m.startswith(forbidden)]


# --- the real adapters, driven through fakes of the documented library APIs ----

def test_indic_parler_adapter_follows_model_card(monkeypatch):
    import numpy as np

    seen = {}

    class Ids:
        def __init__(self, tag):
            self.input_ids, self.attention_mask = f"{tag}-ids", f"{tag}-mask"

        def to(self, device):
            seen.setdefault("devices", []).append(device)
            return self

    class Tokenizer:
        def __init__(self, tag):
            self.tag = tag

        def __call__(self, text, return_tensors):
            seen[self.tag] = text
            assert return_tensors == "pt"
            return Ids(self.tag)

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(name):
            seen.setdefault("tokenizers", []).append(name)
            return Tokenizer("description" if name == "google/flan-t5-large" else "prompt")

    class Generated:
        def cpu(self):
            return self

        def numpy(self):
            return np.zeros((1, 800), dtype=np.float32)

    class Model:
        config = types.SimpleNamespace(text_encoder=types.SimpleNamespace(_name_or_path="google/flan-t5-large"),
                                       sampling_rate=44100)

        @classmethod
        def from_pretrained(cls, name):
            seen["loads"] = seen.get("loads", 0) + 1
            seen["model"] = name
            return cls()

        def to(self, device):
            return self

        def generate(self, **kwargs):
            seen["generate"] = kwargs
            return Generated()

    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False),
                                       inference_mode=nullcontext)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "parler_tts", types.SimpleNamespace(ParlerTTSForConditionalGeneration=Model))
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoTokenizer=AutoTokenizer))

    service = text_to_speech.TextToSpeechService(text_to_speech.IndicParlerEngine())
    audio = service.speak("बीस साइकिल असेंबल हो गई")
    service.speak("वीस सायकली पूर्ण झाल्या")

    assert seen["model"] == "ai4bharat/indic-parler-tts" and seen["loads"] == 1  # loaded once, reused
    assert seen["tokenizers"] == ["ai4bharat/indic-parler-tts", "google/flan-t5-large"]
    assert set(seen["generate"]) == {"input_ids", "attention_mask", "prompt_input_ids", "prompt_attention_mask"}
    assert seen["generate"]["prompt_input_ids"] == "prompt-ids"
    assert "Sunita" in seen["description"]  # last call was Marathi
    assert audio.sample_rate == 44100 and audio.voice == "Divya" and audio.language == "hi"
    assert set(seen["devices"]) == {"cpu"}


def test_indic_parler_reports_missing_dependencies(monkeypatch):
    monkeypatch.setitem(sys.modules, "parler_tts", None)  # import fails
    service = text_to_speech.TextToSpeechService(text_to_speech.IndicParlerEngine())
    with pytest.raises(text_to_speech.TextToSpeechUnavailable):
        service.speak("hello")


def test_indic_speak_adapter_uses_inference_py_from_model_dir(tmp_path):
    (tmp_path / "inference.py").write_text(
        "LOADS = []\n"
        "class TTS:\n"
        "    def __init__(self, path):\n"
        "        LOADS.append(path)\n"
        "        self.calls = []\n"
        "    def __call__(self, text, speaker):\n"
        "        self.calls.append((text, speaker))\n"
        "        return [0.1] * 2400\n",
        encoding="utf-8",
    )
    engine = text_to_speech.IndicSpeakEngine(str(tmp_path))
    service = text_to_speech.TextToSpeechService(engine)
    audio = service.speak("वीस सायकली पूर्ण झाल्या")
    service.speak("Saved. 18 bicycles added to stock.")
    assert audio.sample_rate == 24000 and audio.voice == "Anagha"
    assert engine._tts.calls == [("वीस सायकली पूर्ण झाल्या", "Anagha"), ("Saved. 18 bicycles added to stock.", "Amit")]
    with wave.open(io.BytesIO(audio.content)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2400


def test_indic_speak_without_model_dir_is_unavailable(tmp_path):
    service = text_to_speech.TextToSpeechService(text_to_speech.IndicSpeakEngine(str(tmp_path)))
    with pytest.raises(text_to_speech.TextToSpeechUnavailable):
        service.speak("hello")


# --- Google Cloud Speech-to-Text / Text-to-Speech ---------------------------------
# The google-cloud packages are replaced by fakes that mirror the class/enum names in
# google-cloud-speech 2.41 (speech_v1) and google-cloud-texttospeech 2.38. No network,
# no credentials and no billable calls.

from datetime import timedelta

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
OGG = b"OggS" + b"\x00" * 200
WAV = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 200
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200


class FakeGoogle:
    """Installs fake google.* modules and records every API call."""

    def __init__(self, monkeypatch):
        self.stt_calls, self.tts_calls, self.clients = [], [], []
        self.stt_response = None
        self.stt_error = None
        self.tts_audio = b"ID3fake-mp3"
        self.tts_error = None
        self.client_error = None
        fake = self

        # google.api_core.exceptions / google.auth.exceptions
        exc = types.ModuleType("google.api_core.exceptions")
        exc.GoogleAPIError = type("GoogleAPIError", (Exception,), {})
        exc.GoogleAPICallError = type("GoogleAPICallError", (exc.GoogleAPIError,), {})
        exc.RetryError = type("RetryError", (exc.GoogleAPIError,), {})
        for name in ("InvalidArgument", "FailedPrecondition", "Unauthenticated", "PermissionDenied",
                     "ResourceExhausted", "ServiceUnavailable", "DeadlineExceeded"):
            setattr(exc, name, type(name, (exc.GoogleAPICallError,), {}))
        auth_exc = types.ModuleType("google.auth.exceptions")
        auth_exc.GoogleAuthError = type("GoogleAuthError", (Exception,), {})
        auth_exc.DefaultCredentialsError = type("DefaultCredentialsError", (auth_exc.GoogleAuthError,), {})
        self.exc, self.auth_exc = exc, auth_exc

        # google.cloud.speech (v1 surface)
        speech = types.ModuleType("google.cloud.speech")

        class RecognitionConfig:
            AudioEncoding = types.SimpleNamespace(WEBM_OPUS="WEBM_OPUS", OGG_OPUS="OGG_OPUS",
                                                  LINEAR16="LINEAR16", FLAC="FLAC")

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class RecognitionAudio:
            def __init__(self, content):
                self.content = content

        class SpeechClient:
            def __init__(self, client_options=None):
                if fake.client_error:
                    raise fake.client_error
                fake.clients.append(("speech", client_options))

            def recognize(self, config, audio, retry, timeout):
                fake.stt_calls.append({"config": config.kwargs, "audio": audio.content, "retry": retry,
                                       "timeout": timeout})
                if fake.stt_error:
                    raise fake.stt_error
                return fake.stt_response

        speech.RecognitionConfig, speech.RecognitionAudio, speech.SpeechClient = (
            RecognitionConfig, RecognitionAudio, SpeechClient)

        # google.cloud.texttospeech
        tts = types.ModuleType("google.cloud.texttospeech")
        tts.SynthesisInput = lambda text: {"text": text}
        tts.VoiceSelectionParams = lambda language_code, name: {"language_code": language_code, "name": name}
        tts.AudioConfig = lambda audio_encoding: {"audio_encoding": audio_encoding}
        tts.AudioEncoding = types.SimpleNamespace(MP3="MP3", LINEAR16="LINEAR16")

        class TextToSpeechClient:
            def __init__(self, client_options=None):
                if fake.client_error:
                    raise fake.client_error
                fake.clients.append(("tts", client_options))

            def synthesize_speech(self, input, voice, audio_config, retry, timeout):
                fake.tts_calls.append({"input": input, "voice": voice, "audio_config": audio_config,
                                       "retry": retry, "timeout": timeout})
                if fake.tts_error:
                    raise fake.tts_error
                return types.SimpleNamespace(audio_content=fake.tts_audio)

        tts.TextToSpeechClient = TextToSpeechClient

        google = types.ModuleType("google")
        cloud = types.ModuleType("google.cloud")
        api_core = types.ModuleType("google.api_core")
        auth = types.ModuleType("google.auth")
        cloud.speech, cloud.texttospeech = speech, tts
        api_core.exceptions, auth.exceptions = exc, auth_exc
        google.cloud, google.api_core, google.auth = cloud, api_core, auth
        for name, module in {"google": google, "google.cloud": cloud, "google.cloud.speech": speech,
                             "google.cloud.texttospeech": tts, "google.api_core": api_core,
                             "google.api_core.exceptions": exc, "google.auth": auth,
                             "google.auth.exceptions": auth_exc}.items():
            monkeypatch.setitem(sys.modules, name, module)

    @staticmethod
    def response(*segments, language="hi-in", billed=4):
        results = [types.SimpleNamespace(
            alternatives=[types.SimpleNamespace(transcript=text, confidence=conf)] if text is not None else [],
            language_code=language) for text, conf in segments]
        return types.SimpleNamespace(results=results, total_billed_time=timedelta(seconds=billed))


@pytest.fixture()
def google(monkeypatch):
    for var in ("GOOGLE_STT_LANGUAGE", "GOOGLE_STT_ALTERNATIVE_LANGUAGES", "GOOGLE_STT_MODEL",
                "GOOGLE_STT_DAILY_REQUEST_LIMIT", "GOOGLE_TTS_DAILY_CHAR_LIMIT", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    fake = FakeGoogle(monkeypatch)
    yield fake
    speech_to_text.set_engine(None)
    text_to_speech.set_engine(None)


def transcribe(api, audio, hint=None, token=TOKEN):
    data = {"language_hint": hint} if hint else {}
    headers = {"Authorization": token} if token else {}
    return api.post("/api/v1/voice/transcribe", headers=headers, data=data,
                    files={"audio": ("speech.webm", audio, "audio/webm")})


def test_google_stt_sends_documented_v1_request_and_returns_transcript(api, google):
    google.stt_response = google.response(("बीस साइकिल असेंबल करो", 0.91))
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    res = transcribe(api, WEBM, hint="hi")
    assert res.status_code == 200
    assert res.json() == {"transcript": "बीस साइकिल असेंबल करो", "detected_language": "hi", "confidence": 0.91}
    call = google.stt_calls[0]
    assert call["config"] == {
        "language_code": "hi-IN", "alternative_language_codes": ["en-IN", "mr-IN"], "model": "latest_short",
        "max_alternatives": 1, "enable_automatic_punctuation": False,
        "encoding": "WEBM_OPUS", "sample_rate_hertz": 48000,
    }
    assert call["audio"] == WEBM and call["retry"] is None and call["timeout"] == 15


@pytest.mark.parametrize("audio,expected", [
    (OGG, {"encoding": "OGG_OPUS", "sample_rate_hertz": 48000}),
    (WAV, {}),  # WAV/FLAC: encoding and rate come from the header
])
def test_google_stt_encoding_follows_the_audio_container(api, google, audio, expected):
    google.stt_response = google.response(("18", 0.8), language="en-in")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    assert transcribe(api, audio, hint="mr").status_code == 200
    config = google.stt_calls[0]["config"]
    assert {k: config[k] for k in ("encoding", "sample_rate_hertz") if k in config} == expected
    assert config["language_code"] == "mr-IN"


def test_google_stt_language_configuration(api, google, monkeypatch):
    monkeypatch.setenv("GOOGLE_STT_LANGUAGE", "mr-IN")
    monkeypatch.setenv("GOOGLE_STT_ALTERNATIVE_LANGUAGES", "")
    google.stt_response = google.response(("वीस", 0.7), language="mr-in")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    transcribe(api, WEBM)  # no hint -> configured default
    assert google.stt_calls[0]["config"]["language_code"] == "mr-IN"
    assert google.stt_calls[0]["config"]["alternative_language_codes"] == []


@pytest.mark.parametrize("audio,status,code", [
    (MP4, 415, "STT_UNSUPPORTED_FORMAT"),
    (b"\x1a\x45\xdf\xa3", 422, "EMPTY_AUDIO"),
])
def test_google_stt_rejects_bad_audio_before_any_billable_call(api, google, audio, status, code):
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    res = transcribe(api, audio)
    assert (res.status_code, res.json()["error"]["code"]) == (status, code)
    assert google.stt_calls == []


@pytest.mark.parametrize("segments", [(), ((None, 0),), (("   ", 0.0),)])
def test_google_stt_no_speech_is_an_error_not_invented_text(api, google, segments):
    google.stt_response = google.response(*segments)
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    res = transcribe(api, WEBM)
    assert res.status_code == 422 and res.json()["error"]["code"] == "STT_NO_SPEECH"
    assert "transcript" not in res.json()


def test_google_stt_joins_segments_and_keeps_lowest_confidence(api, google):
    google.stt_response = google.response(("20 cycle", 0.9), ("assemble karo", 0.6))
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    body = transcribe(api, WEBM).json()
    assert body["transcript"] == "20 cycle assemble karo" and body["confidence"] == 0.6


def test_google_stt_missing_confidence_is_null(api, google):
    google.stt_response = google.response(("haan", 0.0), language="hi-in")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    assert transcribe(api, WEBM).json()["confidence"] is None


@pytest.mark.parametrize("error_name,status,code", [
    ("Unauthenticated", 503, "STT_AUTH"),
    ("PermissionDenied", 503, "STT_AUTH"),
    ("ResourceExhausted", 429, "STT_QUOTA"),
    ("DeadlineExceeded", 504, "STT_TIMEOUT"),
    ("InvalidArgument", 422, "STT_INVALID"),
    ("ServiceUnavailable", 503, "STT_UNAVAILABLE"),
    ("GoogleAPICallError", 502, "STT_API"),
])
def test_google_stt_api_errors(api, google, error_name, status, code):
    google.stt_error = getattr(google.exc, error_name)("simulated")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    res = transcribe(api, WEBM)
    assert (res.status_code, res.json()["error"]["code"]) == (status, code)


def test_google_stt_missing_credentials(api, google):
    google.client_error = google.auth_exc.DefaultCredentialsError("no ADC")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    res = transcribe(api, WEBM)
    assert (res.status_code, res.json()["error"]["code"]) == (503, "STT_AUTH")
    assert "application-default login" in res.json()["error"]["message"]


def test_google_stt_package_not_installed(api, monkeypatch):
    monkeypatch.setitem(sys.modules, "google.cloud.speech", None)
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    try:
        res = transcribe(api, WEBM)
        assert (res.status_code, res.json()["error"]["code"]) == (503, "STT_NOT_INSTALLED")
    finally:
        speech_to_text.set_engine(None)


def test_google_stt_daily_limit_stops_calls_and_usage_is_reported(api, google, monkeypatch):
    monkeypatch.setenv("GOOGLE_STT_DAILY_REQUEST_LIMIT", "1")
    google.stt_response = google.response(("18", 0.8), billed=15)
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    assert transcribe(api, WEBM).status_code == 200
    res = transcribe(api, WEBM)
    assert (res.status_code, res.json()["error"]["code"]) == (429, "STT_DAILY_LIMIT")
    assert len(google.stt_calls) == 1
    usage = api.get("/api/v1/voice/config").json()["usage_today"]["stt"]
    assert usage["calls"] == 1 and usage["daily_limit"] == 1 and usage["billed_seconds"] == 15


def test_google_stt_client_is_created_once_with_quota_project(api, google, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-erp-project")
    google.stt_response = google.response(("18", 0.8))
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    transcribe(api, WEBM)
    transcribe(api, WEBM)
    assert google.clients == [("speech", {"quota_project_id": "my-erp-project"})]


def test_recognised_text_goes_through_the_same_parser(api, backend, google):
    google.stt_response = google.response(("20 cycle assemble karo", 0.9), language="hi-in")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    heard = transcribe(api, WEBM, hint="hi").json()
    r = Worker(api).say(heard["transcript"], confidence=heard["confidence"])
    assert r["intent"] == "create_production_order" and r["slots"]["target_quantity"] == 20


def test_low_google_confidence_is_not_acted_on(api, backend, google):
    google.stt_response = google.response(("haan", 0.2))
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    heard = transcribe(api, WEBM).json()
    r = Worker(api).say(heard["transcript"], confidence=heard["confidence"])
    assert r["intent"] == "unknown" and backend.calls == []


# --- Google Text-to-Speech ----------------------------------------------------------

@pytest.mark.parametrize("text,voice,language_code", [
    ("बीस साइकिल असेंबल हो गई", "hi-IN-Standard-A", "hi-IN"),
    ("वीस सायकली पूर्ण झाल्या आहेत", "mr-IN-Standard-A", "mr-IN"),
    ("Saved. 18 bicycles added to stock.", "en-IN-Standard-A", "en-IN"),
])
def test_google_tts_returns_mp3_with_language_voice(api, google, text, voice, language_code):
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    res = speak(api, text)
    assert res.status_code == 200 and res.headers["content-type"] == "audio/mpeg"
    assert res.content == b"ID3fake-mp3" and res.headers["x-tts-voice"] == voice
    call = google.tts_calls[0]
    assert call["voice"] == {"language_code": language_code, "name": voice}
    assert call["audio_config"] == {"audio_encoding": "MP3"} and call["input"] == {"text": text}
    assert call["retry"] is None and call["timeout"] == 10


def test_google_tts_voice_is_configurable(api, google, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_VOICE_HI", "hi-IN-Wavenet-D")
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    speak(api, "बीस साइकिल")
    assert google.tts_calls[0]["voice"] == {"language_code": "hi-IN", "name": "hi-IN-Wavenet-D"}


def test_google_tts_repeated_sentence_costs_one_call(api, google):
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    speak(api, "How many bicycles did you assemble?")
    speak(api, "How many bicycles did you assemble?")
    assert len(google.tts_calls) == 1


@pytest.mark.parametrize("error_name,status,code", [
    ("ResourceExhausted", 429, "TTS_QUOTA"),
    ("PermissionDenied", 503, "TTS_AUTH"),
    ("DeadlineExceeded", 504, "TTS_TIMEOUT"),
    ("InvalidArgument", 422, "TTS_INVALID"),
])
def test_google_tts_errors(api, google, error_name, status, code):
    google.tts_error = getattr(google.exc, error_name)("simulated")
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    res = speak(api, "hello")
    assert (res.status_code, res.json()["error"]["code"]) == (status, code)


def test_google_tts_empty_audio_is_an_error(api, google):
    google.tts_audio = b""
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    assert speak(api, "hello").json()["error"]["code"] == "TTS_FAILED"


def test_google_tts_daily_character_limit(api, google, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_DAILY_CHAR_LIMIT", "30")
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    assert speak(api, "Saved. 18 bicycles added.").status_code == 200  # 25 chars
    res = speak(api, "How many bicycles?")
    assert (res.status_code, res.json()["error"]["code"]) == (429, "TTS_DAILY_LIMIT")
    assert len(google.tts_calls) == 1


def test_google_tts_failure_never_blocks_the_text_reply(api, backend, google):
    google.tts_error = google.exc.ResourceExhausted("quota")
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    r = Worker(api).say("Assemble 20 bicycles.")
    assert r["assistant_text"].startswith("Assembling 20 bicycles")
    assert speak(api, r["assistant_text"]).status_code == 429


# --- developer login (stand-in until Person B's /auth/login exists) ----------------

@pytest.fixture()
def local_client(monkeypatch):
    # TestClient reports its host as "testclient"; treat it as this computer.
    monkeypatch.setattr(voice, "LOCAL_HOSTS", voice.LOCAL_HOSTS | {"testclient"})


def test_dev_login_is_off_by_default(api, local_client, monkeypatch):
    monkeypatch.delenv("DEV_LOGIN", raising=False)
    res = api.post("/api/v1/voice/dev-login", json={"username": "naman"})
    assert res.status_code == 404
    assert api.get("/api/v1/voice/config").json()["dev_login_enabled"] is False


def test_dev_login_returns_contract_shaped_token(api, local_client, monkeypatch):
    monkeypatch.setenv("DEV_LOGIN", "1")
    body = api.post("/api/v1/voice/dev-login", json={"username": "naman", "password": "ignored"}).json()
    assert body["access_token"].startswith("dev-") and body["token_type"] == "bearer"
    assert body["worker"]["display_name"] == "naman (dev login)" and body["dev_login"] is True
    assert api.get("/api/v1/voice/config").json()["dev_login_enabled"] is True


def test_dev_login_only_from_this_computer(api, monkeypatch):
    monkeypatch.setenv("DEV_LOGIN", "1")  # "testclient" is not in LOCAL_HOSTS here
    res = api.post("/api/v1/voice/dev-login", json={"username": "naman"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "DEV_LOGIN_LOCAL_ONLY"


def test_dev_login_validates_username(api, local_client, monkeypatch):
    monkeypatch.setenv("DEV_LOGIN", "1")
    assert api.post("/api/v1/voice/dev-login", json={"username": ""}).status_code == 422


def test_dev_token_is_just_forwarded_and_grants_nothing(api, backend, local_client, monkeypatch):
    monkeypatch.setenv("DEV_LOGIN", "1")
    token = api.post("/api/v1/voice/dev-login", json={"username": "naman"}).json()["access_token"]
    backend.overrides["order"] = lambda r: httpx.Response(401, json={"error": {"code": "UNAUTHORIZED",
                                                                                 "message": "Invalid token."}})
    r = Worker(api, token=f"Bearer {token}").say("Assemble 20 bicycles.")
    assert backend.calls[0]["headers"]["authorization"] == f"Bearer {token}"
    assert r["outcome"] == "error" and "not allowed" in r["assistant_text"]
