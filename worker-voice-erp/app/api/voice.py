"""Voice routes (Person A): ``/api/v1/voice/*``.

``POST /turn`` parses the worker's words, runs the dialogue, and, when the
dialogue asks for it, calls Person B's production API over HTTP exactly as the
shared contract v1.0 defines it:

* ``POST /production/orders``
* ``POST /production/orders/{order_id}/completion-draft``
* ``POST /production/orders/{order_id}/confirm-completion`` (+ ``Idempotency-Key``)

The worker's own ``Authorization: Bearer`` header is forwarded unchanged. No
code here touches the database or inventory, and nothing is reported as saved
unless the production API confirms it. The backend's real status code and body
are returned to the UI in ``backend_response``.

Configuration (environment variables):
* ``ERP_API_BASE_URL``: production API base, default ``http://127.0.0.1:8000/api/v1``
* ``ERP_API_TIMEOUT_SECONDS``: per-request timeout, default ``10``
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from app.services import speech_to_text, text_to_speech
from app.services.command_parser import LANGUAGES, parse
from app.services.dialogue_manager import Action, ActionType, DialogueManager, Session, TurnResult
from app.services.text_to_speech import MAX_TTS_CHARS, speech_profile

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

DEFAULT_ERP_API_BASE_URL = "http://127.0.0.1:8000/api/v1"
MAX_TRANSCRIPT_CHARS = 500
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def error_response(status: int, code: str, message: str, details: Optional[dict] = None) -> JSONResponse:
    """Contract error envelope."""
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}}, status_code=status)


# --------------------------------------------------------- production client

@dataclass
class BackendResult:
    operation: str
    status_code: int  # 0 = no HTTP response
    body: Any = None
    error: Optional[str] = None  # "timeout" | "unreachable" | "invalid_json"

    def to_dict(self) -> dict:
        return {"operation": self.operation, "status_code": self.status_code, "body": self.body, "error": self.error}


class ProductionApiClient:
    """Thin HTTP client for Person B's production endpoints (contract v1.0)."""

    def __init__(self, base_url: str, timeout: float = 10.0, transport: Optional[httpx.BaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    @staticmethod
    def build_request(action: Action) -> tuple[str, dict, dict]:
        """Return (path, json body, extra headers) for a dialogue action."""
        if action.type == ActionType.CREATE_ORDER:
            return "/production/orders", dict(action.payload), {}
        if not action.order_id:
            raise ValueError(f"{action.type.value} requires an order_id")
        order = quote(action.order_id, safe="")
        if action.type == ActionType.CREATE_COMPLETION_DRAFT:
            return f"/production/orders/{order}/completion-draft", dict(action.payload), {}
        if action.type == ActionType.CONFIRM_COMPLETION:
            p = action.payload
            if not action.idempotency_key or not p.get("draft_id") or p.get("confirmed") is not True:
                raise ValueError("confirm_completion requires draft_id, confirmed=true and an idempotency key")
            return (
                f"/production/orders/{order}/confirm-completion",
                {"draft_id": p["draft_id"], "confirmed": True},
                {"Idempotency-Key": action.idempotency_key},
            )
        raise ValueError(f"Unknown action type {action.type}")

    def execute(self, action: Action, authorization: str) -> BackendResult:
        path, body, headers = self.build_request(action)
        headers = {**headers, "Authorization": authorization}
        try:
            res = self._client.post(path, json=body, headers=headers)
        except httpx.TimeoutException:
            return BackendResult(action.type.value, 0, None, "timeout")
        except httpx.HTTPError:
            return BackendResult(action.type.value, 0, None, "unreachable")
        try:
            data = res.json()
        except ValueError:
            return BackendResult(action.type.value, res.status_code, None, "invalid_json")
        return BackendResult(action.type.value, res.status_code, data)


_client: Optional[ProductionApiClient] = None


def get_production_client() -> ProductionApiClient:
    """FastAPI dependency; tests override it with a mocked transport."""
    global _client
    if _client is None:
        _client = ProductionApiClient(
            os.getenv("ERP_API_BASE_URL", DEFAULT_ERP_API_BASE_URL),
            float(os.getenv("ERP_API_TIMEOUT_SECONDS", "10")),
        )
    return _client


# ------------------------------------------------------------ session store

@dataclass
class _SessionEntry:
    session: Session
    owner: str  # hash of the bearer token that started the session
    lock: threading.Lock = field(default_factory=threading.Lock)


_manager = DialogueManager()
_sessions: dict[str, _SessionEntry] = {}
_sessions_lock = threading.Lock()


def _owner_of(authorization: str) -> str:
    return hashlib.sha256(authorization.encode("utf-8")).hexdigest()


def reset_sessions() -> None:
    """Forget every dialogue (tests)."""
    with _sessions_lock:
        _sessions.clear()


# ------------------------------------------------------------------ models

class TurnRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    transcript: str = Field(default="", max_length=MAX_TRANSCRIPT_CHARS)
    language: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    language: Optional[str] = None  # en | hi | mr; detected from the text when missing


class DevLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: Optional[str] = None  # ignored: this is not authentication


# Dev login: only for testing the voice screens before Person B's auth exists.
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def dev_login_enabled() -> bool:
    return os.getenv("DEV_LOGIN", "0").strip().lower() in ("1", "true", "yes")


async def _validated(request: Request, model: type[BaseModel]):
    try:
        data = await request.json()
    except ValueError:
        return error_response(400, "INVALID_JSON", "Request body must be JSON.")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return error_response(422, "VALIDATION_ERROR", "Invalid request.", {"errors": details})


def _bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    return auth if auth.lower().startswith("bearer ") and auth[7:].strip() else None


def _response(s: Session, r: TurnResult, parsed, suggested_action: str, backend: Optional[BackendResult]) -> dict:
    return {
        # contract fields
        "session_id": s.session_id,
        "intent": parsed.intent,
        "slots": parsed.contract_slots(),
        "assistant_text": r.assistant_text,
        "requires_confirmation": r.requires_confirmation,
        "suggested_action": suggested_action,
        # additions (see report / Person B handoff)
        "parse": parsed.to_dict(),
        "language": parsed.language,
        "outcome": r.outcome,
        "dialogue_state": s.snapshot(),
        "backend_response": backend.to_dict() if backend else None,
        "speech": speech_profile(r.assistant_text),
    }


# ------------------------------------------------------------------ routes

@router.post("/turn")
async def voice_turn(request: Request, client: ProductionApiClient = Depends(get_production_client)):
    authorization = _bearer(request)
    if authorization is None:
        return error_response(401, "UNAUTHORIZED", "Log in first: Authorization: Bearer <token> is required.")
    req = await _validated(request, TurnRequest)
    if isinstance(req, JSONResponse):
        return req
    owner = _owner_of(authorization)
    with _sessions_lock:
        entry = _sessions.get(req.session_id)
        if entry is None:
            entry = _SessionEntry(Session(session_id=req.session_id), owner)
            _sessions[req.session_id] = entry
    if entry.owner != owner:
        return error_response(403, "SESSION_FORBIDDEN", "This voice session belongs to another login.")
    if not entry.lock.acquire(blocking=False):
        return error_response(409, "SESSION_BUSY", "Still processing the previous command.")
    try:
        # Runs in the threadpool so a slow backend never blocks the event loop.
        return await run_in_threadpool(_run_turn, entry.session, req, authorization, client)
    finally:
        entry.lock.release()


def _run_turn(s: Session, req: TurnRequest, authorization: str, client: ProductionApiClient) -> dict:
    parsed = parse(req.transcript, confidence=req.confidence)
    if parsed.language == "unknown" and req.language in LANGUAGES:
        parsed.language = req.language
    result = _manager.handle_turn(s, parsed)
    suggested_action = result.suggested_action  # the action taken this turn, if any
    backend = None
    if result.action is not None:
        backend = client.execute(result.action, authorization)
        result = _manager.handle_action_result(s, result.action.action_id, backend.status_code, backend.body)
    return _response(s, result, parsed, suggested_action, backend)


@router.post("/reset")
async def reset_session(request: Request):
    """Forget the dialogue ("start over"). Never touches orders or inventory."""
    authorization = _bearer(request)
    if authorization is None:
        return error_response(401, "UNAUTHORIZED", "Authorization: Bearer <token> is required.")
    req = await _validated(request, SessionRequest)
    if isinstance(req, JSONResponse):
        return req
    with _sessions_lock:
        entry = _sessions.get(req.session_id)
        if entry is not None and entry.owner != _owner_of(authorization):
            return error_response(403, "SESSION_FORBIDDEN", "This voice session belongs to another login.")
        _sessions.pop(req.session_id, None)
    warning = None
    if entry is not None and entry.session.state.value in ("finalizing", "finalize_uncertain"):
        warning = "A save was in progress and its result was not confirmed. Check stock before repeating it."
    return {"session_id": req.session_id, "reset": True, "warning": warning}


@router.post("/dev-login")
async def dev_login(request: Request):
    """Developer-only stand-in for POST /api/v1/auth/login (Person B).

    Off unless DEV_LOGIN=1, and only answers requests from this computer. It
    checks no password and grants nothing: the token is a random string that
    Person B's real backend will reject. Remove DEV_LOGIN once real login exists.
    """
    if not dev_login_enabled():
        return error_response(404, "NOT_FOUND", "Dev login is disabled.")
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        return error_response(403, "DEV_LOGIN_LOCAL_ONLY", "Dev login only works from this computer.")
    req = await _validated(request, DevLoginRequest)
    if isinstance(req, JSONResponse):
        return req
    username = req.username.strip() or "dev"
    return {  # same shape as the contract's login response
        "access_token": f"dev-{secrets.token_hex(16)}",
        "token_type": "bearer",
        "expires_in": 3600,
        "worker": {"id": f"dev-{username}", "username": username, "display_name": f"{username} (dev login)"},
        "dev_login": True,
    }


@router.post("/speak")
async def speak(request: Request):
    """Turn text into WAV audio with the configured local TTS model.

    Only speech: it never parses commands or calls the production API. On any
    error the UI keeps the text on screen and falls back to the browser voice.
    """
    if _bearer(request) is None:
        return error_response(401, "UNAUTHORIZED", "Authorization: Bearer <token> is required.")
    req = await _validated(request, SpeakRequest)
    if isinstance(req, JSONResponse):
        return req
    service = text_to_speech.get_service()
    try:
        audio = await run_in_threadpool(service.speak, req.text, req.language)
    except speech_to_text.SpeechServiceError as exc:
        return error_response(exc.status, exc.code, str(exc))
    return Response(
        content=audio.content,
        media_type=audio.mime_type,
        headers={
            "X-TTS-Engine": audio.engine,
            "X-TTS-Language": audio.language,
            "X-TTS-Voice": audio.voice,
            "Cache-Control": "no-store",
        },
    )


@router.get("/config")
def voice_config():
    engine = speech_to_text.get_engine()
    tts = text_to_speech.get_service()
    usage = {}
    for label, meter in (("stt", getattr(engine, "usage", None)), ("tts", getattr(tts.engine, "usage", None))):
        if meter is not None:
            usage[label] = meter.snapshot()
    return {
        "server_stt_engine": engine.name,
        "server_stt_available": engine.name != "none",
        "server_tts_engine": tts.engine_name,
        "server_tts_available": tts.available,
        "response_language": "en",
        "dev_login_enabled": dev_login_enabled(),
        "usage_today": usage,  # this server process only; Google Cloud billing is the real record
    }


if importlib.util.find_spec("multipart") is not None:
    from fastapi import File, Form, UploadFile

    @router.post("/transcribe")
    async def transcribe(request: Request, audio: UploadFile = File(...), language_hint: Optional[str] = Form(None)):
        """Recognise one recording. Requires login because cloud recognition is billed."""
        if _bearer(request) is None:
            return error_response(401, "UNAUTHORIZED", "Authorization: Bearer <token> is required.")
        if language_hint is not None and language_hint not in speech_to_text.LANGUAGE_HINTS:
            return error_response(422, "VALIDATION_ERROR", "language_hint must be one of en, hi, mr.")
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            return error_response(413, "AUDIO_TOO_LARGE", "Audio is too large.")
        if not data:
            return error_response(422, "EMPTY_AUDIO", "No audio was received.")
        engine = speech_to_text.get_engine()
        try:
            result = await run_in_threadpool(engine.transcribe, data, language_hint)
        except speech_to_text.SpeechServiceError as exc:
            return error_response(exc.status, exc.code, str(exc))
        return result.to_dict()
else:
    @router.post("/transcribe")
    async def transcribe_unavailable():
        return error_response(
            503, "STT_UNAVAILABLE", "Audio upload needs the python-multipart package, which is not installed."
        )
