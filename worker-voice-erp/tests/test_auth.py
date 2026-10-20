
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.auth import login
from app.core.security import hash_password
from app.schemas.auth import LoginRequest


class FakeDB:
    def __init__(self, worker):
        self.worker = worker

    def scalar(self, statement):
        return self.worker


@pytest.fixture
def active_worker():
    return SimpleNamespace(
        id="worker-01",
        username="worker01",
        password_hash=hash_password("BikeWorker123!"),
        display_name="Test Worker",
        active=True,
    )


def test_login_success(monkeypatch, active_worker):
    monkeypatch.setattr(
        "app.api.auth.create_access_token",
        lambda worker_id: "test-jwt-token",
    )

    response = login(
        LoginRequest(username="worker01", password="BikeWorker123!"),
        db=FakeDB(active_worker),
    )

    assert response.access_token == "test-jwt-token"
    assert response.token_type == "bearer"
    assert response.worker.id == "worker-01"


def test_login_wrong_password(active_worker):
    with pytest.raises(HTTPException) as exc_info:
        login(
            LoginRequest(username="worker01", password="WrongPassword!"),
            db=FakeDB(active_worker),
        )

    assert exc_info.value.status_code == 401


def test_login_worker_not_found():
    with pytest.raises(HTTPException) as exc_info:
        login(
            LoginRequest(username="unknown", password="BikeWorker123!"),
            db=FakeDB(None),
        )

    assert exc_info.value.status_code == 401


def test_login_inactive_worker(monkeypatch, active_worker):
    active_worker.active = False

    monkeypatch.setattr(
        "app.api.auth.create_access_token",
        lambda worker_id: "test-jwt-token",
    )

    with pytest.raises(HTTPException) as exc_info:
        login(
            LoginRequest(username="worker01", password="BikeWorker123!"),
            db=FakeDB(active_worker),
        )

    assert exc_info.value.status_code == 403
