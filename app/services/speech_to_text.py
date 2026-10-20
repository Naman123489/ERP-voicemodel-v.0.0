"""Speech-to-text behind a replaceable interface (Person A).

Engines (``STT_ENGINE``):

* ``none`` (default): no server recognition. The UI uses the browser's own
  speech recognition, and typing always works.
* ``google``: Google Cloud Speech-to-Text **v1** ``recognize`` (synchronous,
  under 60 s / 10 MB per request) via the official ``google-cloud-speech``
  package. Hindi ``hi-IN``, Marathi ``mr-IN`` and English ``en-IN`` are listed
  for the ``latest_short`` model on Google's v1 language page.
* ``faster-whisper``: local Whisper model (optional, downloads a model).

Google settings (environment variables):

* ``GOOGLE_STT_LANGUAGE``: default recognition language, ``hi-IN`` unless set.
  The worker's language choice in the UI (en/hi/mr) overrides it per request.
* ``GOOGLE_STT_ALTERNATIVE_LANGUAGES``: comma list (max 3) Google may also
  detect, default: the other two of hi-IN/mr-IN/en-IN. Empty string disables.
* ``GOOGLE_STT_MODEL``: default ``latest_short`` (short commands).
* ``GOOGLE_STT_TIMEOUT_SECONDS``: default 15.
* ``GOOGLE_STT_DAILY_REQUEST_LIMIT``: requests per server process per UTC day,
  default 200 (0 = no limit). Protects free-trial credits.
* ``GOOGLE_CLOUD_PROJECT``: optional quota/billing project for the client.

Authentication uses Application Default Credentials: ``gcloud auth
application-default login`` or ``GOOGLE_APPLICATION_CREDENTIALS`` pointing to a
service-account key file kept outside the repository.

Recognition failures are reported as errors. A failed or empty recognition is
never replaced with invented text.
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from app.services.command_parser import detect_language

LANGUAGE_HINTS = ("en", "hi", "mr")
BCP47 = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}
MAX_ALTERNATIVE_LANGUAGES = 3  # Google v1 limit


# ------------------------------------------------------------------ errors

class SpeechServiceError(RuntimeError):
    """Base error with the HTTP status and error code the voice API returns."""

    status = 503
    code = "SPEECH_ERROR"

    def __init__(self, message: str, *, code: Optional[str] = None, status: Optional[int] = None):
        super().__init__(message)
        if code:
            self.code = code
        if status:
            self.status = status


class SpeechToTextUnavailable(SpeechServiceError):
    """No engine is configured, it cannot be loaded, or credentials are rejected."""

    status, code = 503, "STT_UNAVAILABLE"


class SpeechToTextFailed(SpeechServiceError):
    """The engine ran but could not transcribe this audio."""

    status, code = 422, "STT_FAILED"


def classify_google_error(exc: Exception) -> tuple[str, int, str]:
    """Map a Google client/auth exception to (kind, HTTP status, message).

    kind is one of: auth, quota, timeout, invalid, unavailable, api.
    Imports are lazy so this module works without the Google packages.
    """
    try:
        from google.api_core import exceptions as gexc
    except ImportError:  # pragma: no cover - only reached with the package missing
        gexc = None
    try:
        from google.auth import exceptions as auth_exc
    except ImportError:  # pragma: no cover
        auth_exc = None

    if auth_exc is not None and isinstance(exc, auth_exc.DefaultCredentialsError):
        return ("auth", 503, "Google Cloud credentials were not found. Run "
                "'gcloud auth application-default login' or set GOOGLE_APPLICATION_CREDENTIALS.")
    if auth_exc is not None and isinstance(exc, auth_exc.GoogleAuthError):
        return "auth", 503, f"Google Cloud authentication failed: {exc}"
    if gexc is not None:
        if isinstance(exc, (gexc.Unauthenticated, gexc.PermissionDenied)):
            return ("auth", 503, "Google Cloud rejected the credentials or the API is not enabled "
                    f"for this project: {_short(exc)}")
        if isinstance(exc, gexc.ResourceExhausted):
            return "quota", 429, f"Google Cloud quota or credit limit reached: {_short(exc)}"
        if isinstance(exc, gexc.DeadlineExceeded):
            return "timeout", 504, "Google Cloud did not answer in time."
        if isinstance(exc, (gexc.InvalidArgument, gexc.FailedPrecondition)):
            return "invalid", 422, f"Google Cloud rejected the request: {_short(exc)}"
        if isinstance(exc, (gexc.ServiceUnavailable, gexc.RetryError)):
            return "unavailable", 503, "Google Cloud is temporarily unavailable."
        if isinstance(exc, gexc.GoogleAPIError):
            return "api", 502, f"Google Cloud error: {_short(exc)}"
    return "api", 502, f"Speech service error: {exc}"


def _short(exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc)
    return message.strip().splitlines()[0][:200] if message else exc.__class__.__name__


# ------------------------------------------------------------- usage meter

@dataclass
class UsageMeter:
    """Per-process, per-UTC-day usage counter with an optional cap.

    It does not replace Google Cloud billing alerts; it stops this server from
    making more calls than expected (loops, stuck buttons) and shows usage.
    """

    unit: str
    daily_limit: int = 0  # 0 = no limit
    day: str = ""
    used: float = 0
    calls: int = 0
    billed_seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _roll(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.used, self.calls, self.billed_seconds = today, 0, 0, 0.0

    def reserve(self, amount: float = 1) -> bool:
        with self._lock:
            self._roll()
            if self.daily_limit and self.used + amount > self.daily_limit:
                return False
            self.used += amount
            self.calls += 1
            return True

    def add_billed_seconds(self, seconds: float) -> None:
        with self._lock:
            self.billed_seconds += max(0.0, seconds)

    def snapshot(self) -> dict:
        with self._lock:
            self._roll()
            return {
                "day_utc": self.day, "calls": self.calls, f"{self.unit}_used": self.used,
                "daily_limit": self.daily_limit or None, "billed_seconds": round(self.billed_seconds, 1),
            }


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# ----------------------------------------------------------------- engines

@dataclass
class TranscriptionResult:
    transcript: str
    detected_language: str
    confidence: Optional[float] = None  # None = engine gives no confidence

    def to_dict(self) -> dict:
        return {
            "transcript": self.transcript,
            "detected_language": self.detected_language,
            "confidence": self.confidence,
        }


class SpeechToTextEngine(Protocol):
    name: str

    def transcribe(self, audio: bytes, language_hint: Optional[str] = None) -> TranscriptionResult: ...


class UnavailableEngine:
    name = "none"

    def transcribe(self, audio: bytes, language_hint: Optional[str] = None) -> TranscriptionResult:
        raise SpeechToTextUnavailable(
            "Server speech recognition is not configured. Use the browser microphone or type."
        )


def sniff_audio_format(audio: bytes) -> Optional[str]:
    """Identify the container from magic bytes: webm, ogg, wav, flac, or None."""
    if audio.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if audio.startswith(b"OggS"):
        return "ogg"
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        return "wav"
    if audio.startswith(b"fLaC"):
        return "flac"
    return None


class GoogleSpeechEngine:
    """Google Cloud Speech-to-Text v1 synchronous recognition."""

    name = "google"
    OPUS_SAMPLE_RATE = 48000  # what browsers record; one of the rates v1 accepts for Opus

    def __init__(self):
        self.default_language = os.getenv("GOOGLE_STT_LANGUAGE", "hi-IN").strip() or "hi-IN"
        raw_alt = os.getenv("GOOGLE_STT_ALTERNATIVE_LANGUAGES")
        self.alternative_languages = (
            [c.strip() for c in raw_alt.split(",") if c.strip()] if raw_alt is not None else None
        )
        self.model = os.getenv("GOOGLE_STT_MODEL", "latest_short").strip() or "latest_short"
        self.timeout = float(os.getenv("GOOGLE_STT_TIMEOUT_SECONDS", "15"))
        self.project = os.getenv("GOOGLE_CLOUD_PROJECT") or None
        self.usage = UsageMeter("requests", _env_int("GOOGLE_STT_DAILY_REQUEST_LIMIT", 200))
        self._client = None
        self._speech = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._client is None:
                try:
                    from google.cloud import speech
                except ImportError as exc:
                    raise SpeechToTextUnavailable(
                        "The google-cloud-speech package is not installed.", code="STT_NOT_INSTALLED"
                    ) from exc
                try:
                    options = {"quota_project_id": self.project} if self.project else None
                    self._client = speech.SpeechClient(client_options=options)
                except Exception as exc:  # no credentials, bad key file...
                    kind, status, message = classify_google_error(exc)
                    raise SpeechToTextUnavailable(message, code=f"STT_{kind.upper()}", status=status) from exc
                self._speech = speech
        return self._speech, self._client

    def languages_for(self, language_hint: Optional[str]) -> tuple[str, list[str]]:
        primary = BCP47.get(language_hint or "", self.default_language)
        if self.alternative_languages is None:
            alternatives = [c for c in BCP47.values() if c != primary]
        else:
            alternatives = [c for c in self.alternative_languages if c != primary]
        return primary, alternatives[:MAX_ALTERNATIVE_LANGUAGES]

    def build_config(self, speech, audio: bytes, language_hint: Optional[str]):
        fmt = sniff_audio_format(audio)
        encodings = speech.RecognitionConfig.AudioEncoding
        primary, alternatives = self.languages_for(language_hint)
        config = {
            "language_code": primary,
            "alternative_language_codes": alternatives,
            "model": self.model,
            "max_alternatives": 1,
            "enable_automatic_punctuation": False,
        }
        if fmt == "webm":
            config.update(encoding=encodings.WEBM_OPUS, sample_rate_hertz=self.OPUS_SAMPLE_RATE)
        elif fmt == "ogg":
            config.update(encoding=encodings.OGG_OPUS, sample_rate_hertz=self.OPUS_SAMPLE_RATE)
        elif fmt in ("wav", "flac"):
            pass  # encoding and sample rate come from the file header
        else:
            raise SpeechToTextFailed(
                "Unsupported audio format. Send WebM/Opus, Ogg/Opus, WAV or FLAC.",
                code="STT_UNSUPPORTED_FORMAT", status=415,
            )
        return speech.RecognitionConfig(**config)

    def transcribe(self, audio: bytes, language_hint: Optional[str] = None) -> TranscriptionResult:
        if not audio or len(audio) < 64:
            raise SpeechToTextFailed("The recording is empty.", code="EMPTY_AUDIO")
        speech, client = self._load()
        config = self.build_config(speech, audio, language_hint)  # validates before any paid call
        if not self.usage.reserve(1):
            raise SpeechToTextUnavailable(
                f"Today's speech recognition limit ({self.usage.daily_limit} requests) is used up. Please type.",
                code="STT_DAILY_LIMIT", status=429,
            )
        try:
            # retry=None: one billable call per recording; the worker can simply try again.
            response = client.recognize(
                config=config, audio=speech.RecognitionAudio(content=audio), retry=None, timeout=self.timeout
            )
        except Exception as exc:
            kind, status, message = classify_google_error(exc)
            cls = SpeechToTextFailed if kind == "invalid" else SpeechToTextUnavailable
            raise cls(message, code=f"STT_{kind.upper()}", status=status) from exc

        billed = getattr(response, "total_billed_time", None)
        if billed is not None and hasattr(billed, "total_seconds"):
            self.usage.add_billed_seconds(billed.total_seconds())

        texts, confidences, language = [], [], None
        for result in response.results:
            if not result.alternatives:
                continue
            best = result.alternatives[0]
            if best.transcript.strip():
                texts.append(best.transcript.strip())
                if best.confidence:
                    confidences.append(float(best.confidence))
            language = language or (result.language_code or None)
        transcript = " ".join(texts).strip()
        if not transcript:
            raise SpeechToTextFailed("No speech was recognised. Please try again or type.", code="STT_NO_SPEECH")
        confidence = min(confidences) if confidences else None  # lowest segment = most cautious
        return TranscriptionResult(transcript, _label(language, transcript), confidence)


def _label(google_language: Optional[str], transcript: str) -> str:
    """Google returns e.g. 'hi-in'; map to our en/hi/mr/mixed labels."""
    if google_language:
        short = google_language.split("-")[0].lower()
        if short in LANGUAGE_HINTS:
            detected = detect_language(transcript)
            return "mixed" if detected == "mixed" else short
    return detect_language(transcript)


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, model_size: str = "small"):
        self.model_size = model_size
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # optional dependency
            except ImportError as exc:
                raise SpeechToTextUnavailable("faster-whisper is not installed.") from exc
            try:
                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            except Exception as exc:  # download/load failure
                raise SpeechToTextUnavailable(f"Could not load speech model: {exc}") from exc
        return self._model

    def transcribe(self, audio: bytes, language_hint: Optional[str] = None) -> TranscriptionResult:
        if not audio:
            raise SpeechToTextFailed("Empty audio.", code="EMPTY_AUDIO")
        model = self._load()
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fh:
            fh.write(audio)
            path = fh.name
        try:
            segments, _info = model.transcribe(path, language=language_hint, vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as exc:  # decoder/model errors must not crash the API
            raise SpeechToTextFailed(str(exc)) from exc
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if not text:
            raise SpeechToTextFailed("No speech was recognised.", code="STT_NO_SPEECH")
        return TranscriptionResult(text, detect_language(text), None)


_engine: Optional[SpeechToTextEngine] = None


def get_engine() -> SpeechToTextEngine:
    global _engine
    if _engine is None:
        choice = os.getenv("STT_ENGINE", "none").strip().lower()
        if choice == "google":
            _engine = GoogleSpeechEngine()
        elif choice == "faster-whisper":
            _engine = FasterWhisperEngine(os.getenv("STT_MODEL", "small"))
        else:
            _engine = UnavailableEngine()
    return _engine


def set_engine(engine: Optional[SpeechToTextEngine]) -> None:
    """Swap the engine (tests, or a different provider). ``None`` re-reads config."""
    global _engine
    _engine = engine
