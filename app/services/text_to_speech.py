"""Text-to-speech (Person A).

Only turns text into audio. It knows nothing about commands, orders or stock.

Engines (choose with the ``TTS_ENGINE`` environment variable):

* ``browser`` (default): no server audio. The worker UI speaks with the
  browser's ``speechSynthesis``. Nothing to install.
* ``google``: Google Cloud Text-to-Speech via the official
  ``google-cloud-texttospeech`` package. Returns MP3. Voices default to the
  cheapest ``Standard`` tier: ``hi-IN-Standard-A``, ``mr-IN-Standard-A``,
  ``en-IN-Standard-A`` (override with ``GOOGLE_TTS_VOICE_HI`` / ``_MR`` / ``_EN``).
  ``GOOGLE_TTS_DAILY_CHAR_LIMIT`` caps characters sent per server process per
  UTC day (default 20000, 0 = no limit); ``GOOGLE_TTS_TIMEOUT_SECONDS``
  (default 10). Uses the same Application Default Credentials as Speech-to-Text.
* ``indic-parler``: AI4Bharat Indic Parler-TTS (``ai4bharat/indic-parler-tts``),
  Apache-2.0, 0.9B parameters, Hindi/Marathi/English and more. Calls follow the
  model card exactly. The model repo is gated: accept its terms on Hugging Face
  and log in before the first download. Needs ``parler-tts`` (pins
  ``transformers==4.46.1``), ``torch`` and ``numpy``.
* ``indic-speak``: Bodhan AI / AI4Bharat Indic-Speak (``bodhan-ai/indic-speak``),
  3.78B parameters (bf16), Indic Open Model License v1.0 (read its terms before
  serving it to others). Uses the ``TTS`` class from the ``inference.py`` shipped
  in the downloaded model folder (``TTS_MODEL_DIR``), as documented on the card.
  Needs ``transformers>=5``, ``torch``, ``snac``, ``soundfile``. It cannot share
  a venv with ``indic-parler`` because of the transformers versions.

Other settings: ``TTS_MODEL`` (Hugging Face id or local path for indic-parler),
``TTS_MODEL_DIR`` (local folder for indic-speak), ``TTS_DEVICE`` (``cuda``/``cpu``,
default: cuda if available).

Models load lazily on the first request and are reused. Generation is
serialized with a lock, and repeated sentences come from a small cache.
Local models return 16-bit mono PCM WAV; Google returns MP3. Browsers play both.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import threading
import wave
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Sequence

from app.services.command_parser import detect_language
from app.services.speech_to_text import SpeechServiceError, UsageMeter, _env_int, classify_google_error

MAX_TTS_CHARS = 500
CACHE_SIZE = 64
VOICE_TAGS = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}

# Speakers named on each model card for the languages we use.
PARLER_SPEAKERS = {"hi": "Divya", "mr": "Sunita", "en": "Mary"}
INDIC_SPEAK_SPEAKERS = {"hi": "Kavya", "mr": "Anagha", "en": "Amit"}


class TextToSpeechUnavailable(SpeechServiceError):
    """No server engine is configured, it could not be loaded, or credentials/quota failed."""

    status, code = 503, "TTS_UNAVAILABLE"


class TextToSpeechFailed(SpeechServiceError):
    """The engine is loaded but could not produce audio for this text."""

    status, code = 500, "TTS_FAILED"


@dataclass
class SpeechAudio:
    content: bytes
    mime_type: str  # audio/wav or audio/mpeg
    sample_rate: Optional[int]
    engine: str
    language: str
    voice: str


class TextToSpeechEngine(Protocol):
    name: str

    def synthesize(self, text: str, language: str) -> tuple[Sequence[float], int, str]:
        """Return (float samples in -1..1, sample rate, voice name)."""
        ...


def speech_language(text: str, hint: Optional[str] = None) -> str:
    """Pick hi/mr/en for the voice. English is the fallback for anything else."""
    if hint in ("en", "hi", "mr"):
        return hint
    detected = detect_language(text)
    if detected in ("hi", "mr"):
        return detected
    if detected == "mixed" and any("ऀ" <= ch <= "ॿ" for ch in text):
        return "hi"
    return "en"


def to_wav(samples: Sequence[float], sample_rate: int) -> bytes:
    """Float samples (-1..1) -> 16-bit mono PCM WAV bytes."""
    try:
        import numpy as np
    except ImportError as exc:  # numpy comes with torch, so a real engine always has it
        raise TextToSpeechUnavailable("numpy is not installed.") from exc
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if data.size == 0:
        raise TextToSpeechFailed("The model produced no audio.")
    if not np.all(np.isfinite(data)):
        raise TextToSpeechFailed("The model produced invalid audio.")
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
    return out.getvalue()


def _pick_device(torch) -> str:
    wanted = os.getenv("TTS_DEVICE", "").strip().lower()
    if wanted in ("cuda", "cpu"):
        return "cuda" if wanted == "cuda" and torch.cuda.is_available() else "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


class IndicParlerEngine:
    """AI4Bharat Indic Parler-TTS, following the model card's example."""

    name = "indic-parler"

    def __init__(self, model_id: str = "ai4bharat/indic-parler-tts"):
        self.model_id = model_id
        self._parts = None

    def _load(self):
        if self._parts is None:
            try:
                import torch
                from parler_tts import ParlerTTSForConditionalGeneration
                from transformers import AutoTokenizer
            except ImportError as exc:
                raise TextToSpeechUnavailable(
                    f"Indic Parler-TTS dependencies are missing ({exc.name}). See the install steps."
                ) from exc
            device = _pick_device(torch)
            try:
                model = ParlerTTSForConditionalGeneration.from_pretrained(self.model_id).to(device)
                tokenizer = AutoTokenizer.from_pretrained(self.model_id)
                description_tokenizer = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
            except Exception as exc:  # gated repo not accepted, no network, out of memory...
                raise TextToSpeechUnavailable(f"Could not load {self.model_id}: {exc}") from exc
            self._parts = (torch, model, tokenizer, description_tokenizer, device)
        return self._parts

    def synthesize(self, text: str, language: str):
        torch, model, tokenizer, description_tokenizer, device = self._load()
        speaker = PARLER_SPEAKERS.get(language, PARLER_SPEAKERS["en"])
        description = (
            f"{speaker}'s voice is clear and calm, with a moderate speed and pitch. "
            "The recording is of very high quality, with no background noise."
        )
        try:
            description_ids = description_tokenizer(description, return_tensors="pt").to(device)
            prompt_ids = tokenizer(text, return_tensors="pt").to(device)
            with torch.inference_mode():
                generation = model.generate(
                    input_ids=description_ids.input_ids,
                    attention_mask=description_ids.attention_mask,
                    prompt_input_ids=prompt_ids.input_ids,
                    prompt_attention_mask=prompt_ids.attention_mask,
                )
            audio = generation.cpu().numpy().squeeze()
        except Exception as exc:
            raise TextToSpeechFailed(str(exc)) from exc
        return audio, int(model.config.sampling_rate), speaker


class IndicSpeakEngine:
    """Indic-Speak via the ``TTS`` class in the model folder's ``inference.py``.

    That file comes from the downloaded model repository and runs as Python
    code, so review it before enabling this engine.
    """

    name = "indic-speak"
    SAMPLE_RATE = 24000  # documented on the model card

    def __init__(self, model_dir: Optional[str]):
        self.model_dir = model_dir
        self._tts = None

    def _load(self):
        if self._tts is None:
            if not self.model_dir or not (Path(self.model_dir) / "inference.py").is_file():
                raise TextToSpeechUnavailable(
                    "TTS_MODEL_DIR must point to a downloaded bodhan-ai/indic-speak folder containing inference.py."
                )
            spec = importlib.util.spec_from_file_location("indic_speak_inference", Path(self.model_dir) / "inference.py")
            module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(self.model_dir))  # the file may import helpers next to it
            try:
                spec.loader.exec_module(module)
                self._tts = module.TTS(str(self.model_dir))
            except ImportError as exc:
                raise TextToSpeechUnavailable(
                    f"Indic-Speak dependencies are missing ({exc.name}). See the install steps."
                ) from exc
            except Exception as exc:
                raise TextToSpeechUnavailable(f"Could not load Indic-Speak: {exc}") from exc
            finally:
                if sys.path and sys.path[0] == str(self.model_dir):
                    sys.path.pop(0)
        return self._tts

    def synthesize(self, text: str, language: str):
        tts = self._load()
        speaker = INDIC_SPEAK_SPEAKERS.get(language, INDIC_SPEAK_SPEAKERS["en"])
        try:
            audio = tts(text, speaker=speaker)
        except Exception as exc:
            raise TextToSpeechFailed(str(exc)) from exc
        return audio, self.SAMPLE_RATE, speaker


class GoogleTextToSpeechEngine:
    """Google Cloud Text-to-Speech, MP3 output. Returns encoded audio directly."""

    name = "google"
    DEFAULT_VOICES = {"hi": "hi-IN-Standard-A", "mr": "mr-IN-Standard-A", "en": "en-IN-Standard-A"}

    def __init__(self):
        self.voices = {
            lang: os.getenv(f"GOOGLE_TTS_VOICE_{lang.upper()}", default).strip() or default
            for lang, default in self.DEFAULT_VOICES.items()
        }
        self.timeout = float(os.getenv("GOOGLE_TTS_TIMEOUT_SECONDS", "10"))
        self.project = os.getenv("GOOGLE_CLOUD_PROJECT") or None
        self.usage = UsageMeter("characters", _env_int("GOOGLE_TTS_DAILY_CHAR_LIMIT", 20000))
        self._client = None
        self._tts = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._client is None:
                try:
                    from google.cloud import texttospeech
                except ImportError as exc:
                    raise TextToSpeechUnavailable(
                        "The google-cloud-texttospeech package is not installed.", code="TTS_NOT_INSTALLED"
                    ) from exc
                try:
                    options = {"quota_project_id": self.project} if self.project else None
                    self._client = texttospeech.TextToSpeechClient(client_options=options)
                except Exception as exc:
                    kind, status, message = classify_google_error(exc)
                    raise TextToSpeechUnavailable(message, code=f"TTS_{kind.upper()}", status=status) from exc
                self._tts = texttospeech
        return self._tts, self._client

    def synthesize_encoded(self, text: str, language: str) -> tuple[bytes, str, Optional[int], str]:
        """Return (audio bytes, mime type, sample rate or None, voice name)."""
        tts, client = self._load()
        voice_name = self.voices.get(language, self.voices["en"])
        language_code = "-".join(voice_name.split("-")[:2])  # "hi-IN-Standard-A" -> "hi-IN"
        if not self.usage.reserve(len(text)):
            raise TextToSpeechUnavailable(
                f"Today's speech output limit ({self.usage.daily_limit} characters) is used up.",
                code="TTS_DAILY_LIMIT", status=429,
            )
        try:
            response = client.synthesize_speech(
                input=tts.SynthesisInput(text=text),
                voice=tts.VoiceSelectionParams(language_code=language_code, name=voice_name),
                audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
                retry=None,  # one billable call per sentence
                timeout=self.timeout,
            )
        except Exception as exc:
            kind, status, message = classify_google_error(exc)
            cls = TextToSpeechFailed if kind == "invalid" else TextToSpeechUnavailable
            raise cls(message, code=f"TTS_{kind.upper()}", status=status) from exc
        if not response.audio_content:
            raise TextToSpeechFailed("Google returned no audio.")
        return bytes(response.audio_content), "audio/mpeg", None, voice_name


class TextToSpeechService:
    """Holds one engine, loads it once, serializes generation, caches results."""

    def __init__(self, engine: Optional[TextToSpeechEngine]):
        self.engine = engine
        self._lock = threading.Lock()
        self._cache: "OrderedDict[tuple[str, str], SpeechAudio]" = OrderedDict()

    @property
    def available(self) -> bool:
        return self.engine is not None

    @property
    def engine_name(self) -> str:
        return self.engine.name if self.engine else "browser"

    def speak(self, text: str, language_hint: Optional[str] = None) -> SpeechAudio:
        text = " ".join((text or "").split())
        if not text:
            raise TextToSpeechFailed("Nothing to say.")
        if len(text) > MAX_TTS_CHARS:
            raise TextToSpeechFailed(f"Text is longer than {MAX_TTS_CHARS} characters.")
        if self.engine is None:
            raise TextToSpeechUnavailable("Server speech is not configured; the browser voice is used instead.")
        language = speech_language(text, language_hint)
        key = (language, text)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            if hasattr(self.engine, "synthesize_encoded"):  # cloud engines return finished audio files
                content, mime_type, rate, voice = self.engine.synthesize_encoded(text, language)
            else:  # local models return raw samples
                samples, rate, voice = self.engine.synthesize(text, language)
                content, mime_type = to_wav(samples, rate), "audio/wav"
            audio = SpeechAudio(content, mime_type, rate, self.engine.name, language, voice)
            self._cache[key] = audio
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            return audio


def _engine_from_env() -> Optional[TextToSpeechEngine]:
    choice = os.getenv("TTS_ENGINE", "browser").strip().lower()
    if choice == "google":
        return GoogleTextToSpeechEngine()
    if choice == "indic-parler":
        return IndicParlerEngine(os.getenv("TTS_MODEL", "ai4bharat/indic-parler-tts"))
    if choice == "indic-speak":
        return IndicSpeakEngine(os.getenv("TTS_MODEL_DIR"))
    return None


_service: Optional[TextToSpeechService] = None
_service_lock = threading.Lock()


def get_service() -> TextToSpeechService:
    global _service
    with _service_lock:
        if _service is None:
            _service = TextToSpeechService(_engine_from_env())
        return _service


def set_engine(engine: Optional[TextToSpeechEngine]) -> None:
    """Swap the engine (tests). ``None`` means browser-only."""
    global _service
    with _service_lock:
        _service = TextToSpeechService(engine)


def speech_profile(text: str, response_language: str = "en") -> dict:
    """What the UI should do with a reply: server audio if available, else the browser voice."""
    service = get_service()
    return {
        "engine": service.engine_name,
        "server_audio": service.available,
        "text": text,  # identical to assistant_text by construction
        "lang": VOICE_TAGS.get(response_language, "en-IN"),
    }
