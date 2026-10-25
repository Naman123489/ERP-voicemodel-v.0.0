"""Automatic English / Hindi / Marathi conversation.

The worker never picks a language. These tests check that the reply language
follows the worker, that short answers keep the conversation's language, that
speech in and out use the matching language, and that language never changes
what is sent to the production backend or the safety rules.
"""

import re

import pytest

from app.services import speech_to_text, text_to_speech
from app.services.command_parser import parse
from app.services.responses import MESSAGES, SUPPORTED, placeholders, resolve_response_language, say
from tests.test_voice_api import (  # noqa: F401  (pytest fixtures are used by name)
    TOKEN, WEBM, FakeGoogle, FakeTTSEngine, Worker, api, backend, google, speak, transcribe,
)

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

FLOWS = {
    "en": ["Assemble 20 bicycles.", "Assembly finished", "I assembled 18 bicycles.", "Yes, save it."],
    "hi": ["बीस साइकिल असेंबल करो।", "असेंबली हो गई है", "अठारह साइकिल बन गई हैं।", "हाँ, सेव करो।"],
    "mr": ["वीस सायकली असेंबल करा।", "असेंबली पूर्ण झाली आहे", "अठरा सायकली तयार झाल्या आहेत।", "हो, सेव करा।"],
    "hi-mixed": ["20 cycle assemble karo.", "assembly ho gaya", "18 cycle ban gaye.", "Haan, save karo."],
}


# --- the response catalogue ------------------------------------------------------

@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_every_message_exists_in_all_three_languages_with_same_placeholders(key):
    templates = MESSAGES[key]
    assert set(templates) == set(SUPPORTED)
    # Hindi/Marathi may omit English-only values (plural noun, the backend's English message)
    # and add the HTTP code instead, but never use a value the English text isn't given.
    for lang in ("hi", "mr"):
        assert placeholders(templates[lang]) <= placeholders(templates["en"]) | {"code"}
    assert not DEVANAGARI.search(templates["en"])
    fixed_text = re.sub(r"\{[a-z_]+\}", "", templates["hi"] + templates["mr"]).strip(" .।:()")
    if fixed_text:  # templates that are only placeholders have nothing to translate
        assert DEVANAGARI.search(templates["hi"]) and DEVANAGARI.search(templates["mr"])


def test_numbers_and_ids_are_never_changed_by_translation():
    for lang in SUPPORTED:
        text = say("read_back", lang, d=18, target=20)
        assert "18" in text and "20" in text
        assert "order-007" in say("cancelled_order", lang, order_id="order-007")
        backend_failure = say("fail_backend_message", lang, prefix="x", message="Not enough wheels.", code=409)
        assert ("Not enough wheels." in backend_failure) if lang == "en" else ("409" in backend_failure)


# --- full conversations, one per language ---------------------------------------------

@pytest.mark.parametrize("flow", sorted(FLOWS))
def test_conversation_replies_in_the_workers_language(api, backend, flow):
    expected = flow.split("-")[0]
    w = Worker(api)
    replies = [w.say(text) for text in FLOWS[flow]]
    assert [r["response_language"] for r in replies] == [expected] * 4
    order, finished, count, confirm = replies
    assert order["assistant_text"] == say("order_created_available", expected, n=20)
    assert finished["assistant_text"] == say("ask_completed", expected)
    assert count["assistant_text"] == say("read_back", expected, d=18, target=20)
    assert confirm["outcome"] == "saved"
    assert confirm["assistant_text"] == say("saved", expected, n=18)
    for r in replies:
        assert r["speech"]["lang"] == {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}[expected]
        assert bool(DEVANAGARI.search(r["assistant_text"])) == (expected != "en")


@pytest.mark.parametrize("flow", sorted(FLOWS))
def test_language_never_changes_what_is_sent_to_the_backend(api, backend, flow):
    w = Worker(api)
    for text in FLOWS[flow]:
        w.say(text)
    order, draft, confirm = backend.calls
    assert order["body"] == {"product_id": "bicycle-standard", "target_quantity": 20, "unit": "piece"}
    assert draft["body"] == {"actual_completed_quantity": 18, "unit": "piece"}
    assert confirm["body"] == {"draft_id": "draft-001", "confirmed": True}
    assert confirm["headers"]["idempotency-key"]


# --- short answers and switching ---------------------------------------------------------

@pytest.mark.parametrize("short_answer", ["yes", "ok", "haan", "हाँ", "हो"])
def test_short_confirmation_keeps_the_conversation_language(api, backend, short_answer):
    w = Worker(api)
    for text in FLOWS["mr"][:3]:
        w.say(text)
    r = w.say(short_answer)
    assert r["response_language"] == "mr" and r["language_reason"] == "conversation"
    assert r["outcome"] == "saved"


def test_bare_number_keeps_hindi(api, backend):
    w = Worker(api)
    w.say("बीस साइकिल असेंबल करो।")
    w.say("असेंबली हो गई है")
    r = w.say("18")
    assert r["response_language"] == "hi"
    assert r["assistant_text"] == say("read_back", "hi", d=18, target=20)


def test_worker_can_switch_language_mid_conversation(api, backend):
    w = Worker(api)
    assert w.say("बीस साइकिल असेंबल करो।")["response_language"] == "hi"
    assert w.say("असेंबली पूर्ण झाली आहे")["response_language"] == "mr"
    assert w.say("I assembled 18 bicycles.")["response_language"] == "en"


def test_each_session_keeps_its_own_language(api, backend):
    hindi, english = Worker(api), Worker(api, token="Bearer another-worker")
    hindi.say("बीस साइकिल असेंबल करो।")
    english.say("Assemble 20 bicycles.")
    assert hindi.say("yes")["response_language"] == "hi"
    assert english.say("yes")["response_language"] == "en"


def test_first_short_word_uses_its_own_evidence():
    assert resolve_response_language("ok", parse("ok"), None, None) == ("en", "words")
    assert resolve_response_language("हाँ", parse("हाँ"), None, None)[0] == "hi"


def test_speech_recognition_language_decides_when_words_do_not():
    # Devanagari without Hindi/Marathi grammar words: Google's language id decides.
    text = "असेंबल 20"
    assert resolve_response_language(text, parse(text), "mr", None) == ("mr", "speech_recognition")
    assert resolve_response_language(text, parse(text), "hi", None) == ("hi", "speech_recognition")
    # grammar words beat the recogniser's guess
    text = "वीस सायकली असेंबल करा"
    assert resolve_response_language(text, parse(text), "hi", None) == ("mr", "words")


def test_code_switching_follows_grammar_not_english_nouns():
    for text, lang in [("20 cycle assemble karo", "hi"), ("18 bicycles ban gaye", "hi"),
                       ("हो, save करा", "mr"), ("20 cycle assemble kara", "mr")]:
        assert resolve_response_language(text, parse(text), None, None)[0] == lang


# --- unclear speech in each language ---------------------------------------------------------

@pytest.mark.parametrize("lang,start", [("hi", "बीस साइकिल असेंबल करो।"), ("mr", "वीस सायकली असेंबल करा।")])
def test_unclear_or_low_confidence_speech_asks_again_in_the_workers_language(api, backend, lang, start):
    w = Worker(api)
    w.say(start)
    calls_before = len(backend.calls)
    r = w.say("haan", confidence=0.2)
    assert r["intent"] == "unknown" and r["response_language"] == lang
    assert r["assistant_text"].startswith(say("lead_low_confidence", lang))
    assert len(backend.calls) == calls_before


# --- safety is the same in every language ------------------------------------------------------

@pytest.mark.parametrize("lang,cancel", [("hi", "नहीं, रद्द करो।"), ("mr", "नाही, रद्द करा।"), ("en", "No, cancel.")])
def test_cancel_never_finalizes_in_any_language(api, backend, lang, cancel):
    flow = {"hi": FLOWS["hi"], "mr": FLOWS["mr"], "en": FLOWS["en"]}[lang]
    w = Worker(api)
    for text in flow[:3]:
        w.say(text)
    r = w.say(cancel)
    assert r["intent"] == "cancel" and r["response_language"] == lang
    assert backend.ops("confirm") == []


@pytest.mark.parametrize("lang", ["hi", "mr"])
def test_backend_failure_is_never_announced_as_saved_in_any_language(api, backend, lang):
    import httpx

    w = Worker(api)
    for text in FLOWS[lang][:3]:
        w.say(text)
    backend.overrides["confirm"] = lambda r: httpx.Response(500, json={"error": {"code": "DB", "message": "x"}})
    r = w.say(FLOWS[lang][3])
    assert r["outcome"] == "uncertain"
    assert r["assistant_text"] == say("confirm_uncertain", lang)
    assert all(r["assistant_text"] != say("saved", l, n=18) for l in SUPPORTED)
    del backend.overrides["confirm"]
    retry = w.say("हाँ" if lang == "hi" else "हो")
    keys = [c["headers"]["idempotency-key"] for c in backend.ops("confirm")]
    assert retry["outcome"] == "saved" and len(keys) == 2 and keys[0] == keys[1]


@pytest.mark.parametrize("lang", ["hi", "mr"])
def test_correction_is_read_back_again_in_the_workers_language(api, backend, lang):
    w = Worker(api)
    for text in FLOWS[lang][:3]:
        w.say(text)
    correction = "नहीं, सत्रह" if lang == "hi" else "नाही, सतरा"
    r = w.say(correction)
    assert r["assistant_text"] == say("read_back", lang, d=17, target=20)
    assert r["requires_confirmation"] is True


# --- speech in: automatic language detection (Google) -------------------------------------

def test_google_recognition_checks_all_three_languages_without_a_hint(api, google):
    google.stt_response = google.response(("वीस सायकली असेंबल करा", 0.9), language="mr-in")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    body = transcribe(api, WEBM).json()  # no language_hint: the worker chose nothing
    config = google.stt_calls[0]["config"]
    assert config["language_code"] == "hi-IN"
    assert sorted(config["alternative_language_codes"]) == ["en-IN", "mr-IN"]
    assert body["detected_language"] == "mr"
    assert len(google.stt_calls) == 1  # one request, not one per language


def test_conversation_language_is_tried_first_but_others_still_checked(api, google):
    google.stt_response = google.response(("I assembled 18 bicycles", 0.9), language="en-in")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    body = transcribe(api, WEBM, hint="mr").json()
    config = google.stt_calls[0]["config"]
    assert config["language_code"] == "mr-IN" and sorted(config["alternative_language_codes"]) == ["en-IN", "hi-IN"]
    assert body["detected_language"] == "en"


def test_recognised_language_reaches_the_reply(api, backend, google):
    google.stt_response = google.response(("असेंबल 20", 0.9), language="mr-in")
    speech_to_text.set_engine(speech_to_text.GoogleSpeechEngine())
    heard = transcribe(api, WEBM).json()
    r = Worker(api).say(heard["transcript"], language=heard["detected_language"], confidence=heard["confidence"])
    assert r["response_language"] == "mr" and r["language_reason"] == "speech_recognition"


# --- speech out: matching voice (Google) ----------------------------------------------------------

@pytest.mark.parametrize("flow,voice", [("en", "en-IN-Standard-A"), ("hi", "hi-IN-Standard-A"),
                                        ("mr", "mr-IN-Standard-A")])
def test_reply_is_spoken_with_the_matching_google_voice(api, backend, google, flow, voice):
    text_to_speech.set_engine(text_to_speech.GoogleTextToSpeechEngine())
    r = Worker(api).say(FLOWS[flow][0])
    res = speak(api, r["assistant_text"], language=r["response_language"])
    assert res.status_code == 200 and res.headers["x-tts-voice"] == voice
    assert google.tts_calls[0]["voice"]["language_code"] == voice[:5]
    assert google.tts_calls[0]["input"] == {"text": r["assistant_text"]}


def test_speech_failure_never_blocks_the_multilingual_reply(api, backend):
    text_to_speech.set_engine(FakeTTSEngine(fail=True))
    try:
        r = Worker(api).say("वीस सायकली असेंबल करा।")
        assert r["assistant_text"] == say("order_created_available", "mr", n=20)
        assert speak(api, r["assistant_text"], language="mr").status_code == 500
    finally:
        text_to_speech.set_engine(None)


# --- frontend -------------------------------------------------------------------------------

def test_worker_screen_has_no_language_picker(api):
    html = api.get("/").text
    assert "<select" not in html and "speech-language" not in html
    assert 'id="language-badge"' in html


# --- Speech-to-Text v2 / chirp_3: automatic language (the default "google" engine) ---------------
# Stand-in for google.cloud.speech_v2 with the field names of google-cloud-speech 2.41.

import sys
import types
from datetime import timedelta


class FakeChirp:
    """Scripted chirp_3 answers keyed by the language list sent; records every request."""

    def __init__(self, monkeypatch, google_fake):
        self.requests, self.options = [], []
        self.answers = {}  # tuple(language_codes) -> (transcript, language_code) or an exception
        self.adc = (types.SimpleNamespace(quota_project_id="adc-project"), None)
        fake = self

        class ClientOptions:
            def __init__(self, api_endpoint=None, quota_project_id=None):
                self.api_endpoint, self.quota_project_id = api_endpoint, quota_project_id

        class SpeechClient:
            def __init__(self, client_options=None):
                fake.options.append(client_options)

            def recognize(self, request, retry, timeout):
                codes = tuple(request.config.language_codes)
                fake.requests.append({"recognizer": request.recognizer, "codes": list(codes),
                                      "model": request.config.model, "retry": retry, "timeout": timeout,
                                      "content": request.content})
                answer = fake.answers.get(codes, ("", None))
                if isinstance(answer, Exception):
                    raise answer
                text, lang = answer
                alt = [types.SimpleNamespace(transcript=text, confidence=0.0)] if text else []
                return types.SimpleNamespace(
                    results=[types.SimpleNamespace(alternatives=alt, language_code=lang)],
                    metadata=types.SimpleNamespace(total_billed_duration=timedelta(seconds=2)),
                )

        cloud_speech = types.ModuleType("google.cloud.speech_v2.types.cloud_speech")
        cloud_speech.RecognizeRequest = lambda recognizer, config, content: types.SimpleNamespace(
            recognizer=recognizer, config=config, content=content)
        cloud_speech.RecognitionConfig = lambda auto_decoding_config, language_codes, model: types.SimpleNamespace(
            auto_decoding_config=auto_decoding_config, language_codes=language_codes, model=model)
        cloud_speech.AutoDetectDecodingConfig = lambda: "auto"
        speech_v2 = types.ModuleType("google.cloud.speech_v2")
        speech_v2.SpeechClient = SpeechClient
        speech_v2_types = types.ModuleType("google.cloud.speech_v2.types")
        speech_v2_types.cloud_speech = cloud_speech
        client_options = types.ModuleType("google.api_core.client_options")
        client_options.ClientOptions = ClientOptions
        auth = sys.modules["google.auth"]
        auth.default = lambda: fake.adc
        for name, module in {"google.cloud.speech_v2": speech_v2, "google.cloud.speech_v2.types": speech_v2_types,
                             "google.cloud.speech_v2.types.cloud_speech": cloud_speech,
                             "google.api_core.client_options": client_options}.items():
            monkeypatch.setitem(sys.modules, name, module)
        self.exc = google_fake.exc


@pytest.fixture()
def chirp(monkeypatch, google):
    for var in ("GOOGLE_STT_LOCATION", "GOOGLE_STT_V2_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-erp-project")
    fake = FakeChirp(monkeypatch, google)
    speech_to_text.set_engine(speech_to_text.GoogleChirpSpeechEngine())
    yield fake
    speech_to_text.set_engine(None)


def test_chirp_english_or_hindi_needs_one_request(api, chirp):
    chirp.answers[("en-IN", "hi-IN")] = ("Assemble 20 bicycles.", "en-IN")
    body = transcribe(api, WEBM).json()
    assert body == {"transcript": "Assemble 20 bicycles.", "detected_language": "en", "confidence": None}
    assert len(chirp.requests) == 1
    req = chirp.requests[0]
    assert req["codes"] == ["en-IN", "hi-IN"] and req["model"] == "chirp_3"
    assert req["recognizer"] == "projects/my-erp-project/locations/eu/recognizers/_"
    assert req["retry"] is None and req["timeout"] == 15
    assert chirp.options[0].api_endpoint == "eu-speech.googleapis.com"
    assert chirp.options[0].quota_project_id == "my-erp-project"


def test_chirp_rechecks_marathi_only_when_marathi_grammar_is_heard(api, chirp):
    # what really happened on this project: Marathi speech through the en/hi list
    chirp.answers[("en-IN", "hi-IN")] = ("वी साइकिल असेंबल करा।", "hi-IN")
    chirp.answers[("mr-IN",)] = ("20 सायकल असेम्बल करा.", "mr-IN")
    body = transcribe(api, WEBM).json()
    assert body["transcript"] == "20 सायकल असेम्बल करा." and body["detected_language"] == "mr"
    assert [r["codes"] for r in chirp.requests] == [["en-IN", "hi-IN"], ["mr-IN"]]


def test_chirp_hindi_never_triggers_the_marathi_request(api, chirp):
    chirp.answers[("en-IN", "hi-IN")] = ("20 साइकिल असेंबल करो।", "hi-IN")
    assert transcribe(api, WEBM).json()["detected_language"] == "hi"
    assert len(chirp.requests) == 1


def test_chirp_marathi_conversation_tries_marathi_first(api, chirp):
    chirp.answers[("mr-IN",)] = ("अठरा सायकली तयार झाल्या आहेत", "mr-IN")
    body = transcribe(api, WEBM, hint="mr").json()
    assert body["detected_language"] == "mr" and len(chirp.requests) == 1


def test_chirp_switch_away_from_marathi_falls_back_to_english_hindi(api, chirp):
    chirp.answers[("mr-IN",)] = ("बीस साइकिल असेंबल करो", "mr-IN")  # Hindi words through the Marathi model
    chirp.answers[("en-IN", "hi-IN")] = ("बीस साइकिल असेंबल करो", "hi-IN")
    body = transcribe(api, WEBM, hint="mr").json()
    assert body["detected_language"] == "hi"
    assert [r["codes"] for r in chirp.requests] == [["mr-IN"], ["en-IN", "hi-IN"]]


def test_chirp_nothing_heard_is_an_error_not_invented_text(api, chirp):
    res = transcribe(api, WEBM)
    assert res.status_code == 422 and res.json()["error"]["code"] == "STT_NO_SPEECH"


def test_chirp_rejects_unknown_audio_before_any_paid_request(api, chirp):
    res = transcribe(api, b"\x00\x00\x00\x18ftypmp42" + bytes(200))
    assert res.status_code == 415 and chirp.requests == []


@pytest.mark.parametrize("error_name,status,code", [("InvalidArgument", 422, "STT_INVALID"),
                                                    ("ResourceExhausted", 429, "STT_QUOTA"),
                                                    ("Unauthenticated", 503, "STT_AUTH")])
def test_chirp_errors(api, chirp, error_name, status, code):
    chirp.answers[("en-IN", "hi-IN")] = getattr(chirp.exc, error_name)("simulated")
    res = transcribe(api, WEBM)
    assert (res.status_code, res.json()["error"]["code"]) == (status, code)


def test_chirp_uses_credentials_quota_project_when_env_is_missing(api, chirp, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    speech_to_text.set_engine(speech_to_text.GoogleChirpSpeechEngine())
    chirp.answers[("en-IN", "hi-IN")] = ("Assemble 20 bicycles.", "en-IN")
    transcribe(api, WEBM)
    assert chirp.requests[0]["recognizer"].startswith("projects/adc-project/")


def test_chirp_without_any_project_says_how_to_fix_it(api, chirp, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    chirp.adc = (types.SimpleNamespace(quota_project_id=None), None)
    speech_to_text.set_engine(speech_to_text.GoogleChirpSpeechEngine())
    res = transcribe(api, WEBM)
    assert res.status_code == 503 and "GOOGLE_CLOUD_PROJECT" in res.json()["error"]["message"]


def test_chirp_daily_limit_counts_every_request(api, chirp, monkeypatch):
    monkeypatch.setenv("GOOGLE_STT_DAILY_REQUEST_LIMIT", "1")
    speech_to_text.set_engine(speech_to_text.GoogleChirpSpeechEngine())
    chirp.answers[("en-IN", "hi-IN")] = ("वी साइकिल असेंबल करा।", "hi-IN")  # would need a 2nd request
    res = transcribe(api, WEBM)
    assert (res.status_code, res.json()["error"]["code"]) == (429, "STT_DAILY_LIMIT")
    assert len(chirp.requests) == 1


@pytest.mark.parametrize("choice,cls", [("google", "GoogleChirpSpeechEngine"), ("google-v1", "GoogleSpeechEngine"),
                                        ("none", "UnavailableEngine")])
def test_engine_selection(monkeypatch, choice, cls):
    monkeypatch.setenv("STT_ENGINE", choice)
    speech_to_text.set_engine(None)
    try:
        assert type(speech_to_text.get_engine()).__name__ == cls
    finally:
        speech_to_text.set_engine(None)


def test_spoken_marathi_order_end_to_end(api, backend, chirp):
    chirp.answers[("en-IN", "hi-IN")] = ("वी साइकिल असेंबल करा।", "hi-IN")
    chirp.answers[("mr-IN",)] = ("20 सायकल असेम्बल करा.", "mr-IN")
    heard = transcribe(api, WEBM).json()
    r = Worker(api).say(heard["transcript"], language=heard["detected_language"], confidence=heard["confidence"])
    assert r["response_language"] == "mr" and r["slots"]["target_quantity"] == 20
    assert r["assistant_text"] == say("order_created_available", "mr", n=20)
