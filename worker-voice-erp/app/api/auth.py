
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    decode_access_token,
    verify_password,
)
from app.db.database import get_db
from app.db.models import Worker
from app.schemas.auth import LoginRequest, LoginResponse, WorkerInfo


router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_current_worker(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> Worker:
    """Validate the bearer token and return the active worker."""

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()

    try:
        worker_id = decode_access_token(token)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    worker = db.get(Worker, worker_id)

    if worker is None or not worker.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Worker account is unavailable.",
        )

    return worker


@router.post("/login", response_model=LoginResponse)
def login(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
):
    """Authenticate a worker and issue an access token."""

    statement = select(Worker).where(
        Worker.username == credentials.username
    )
    worker = db.scalar(statement)

    if worker is None or not verify_password(
        credentials.password,
        worker.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not worker.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This worker account is inactive.",
        )

    try:
        token = create_access_token(worker.id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is not configured.",
        ) from exc

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        worker=WorkerInfo(
            id=worker.id,
            username=worker.username,
            display_name=worker.display_name,
        ),
    )


@router.get("/me", response_model=WorkerInfo)
def get_me(
    worker: Worker = Depends(get_current_worker),
):
    """Return the identity of the currently authenticated worker."""

    return WorkerInfo(
        id=worker.id,
        username=worker.username,
        display_name=worker.display_name,
    )
