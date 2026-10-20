
from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import settings


password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Convert a plain-text password into a secure password hash."""
    if not password:
        raise ValueError("Password cannot be empty.")

    return password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Check a password against its stored hash."""
    return password_hash.verify(password, stored_hash)


def create_access_token(worker_id: str) -> str:
    """Create a signed JWT for an authenticated worker."""
    if not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY is not configured.")

    if not worker_id:
        raise ValueError("Worker ID cannot be empty.")

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        "sub": worker_id,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> str:
    """Verify a JWT and return the worker ID stored in its subject."""
    if not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY is not configured.")

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except InvalidTokenError as exc:
        raise ValueError("Invalid or expired access token.") from exc

    worker_id = payload.get("sub")

    if not isinstance(worker_id, str) or not worker_id:
        raise ValueError("Invalid access token subject.")

    return worker_id
