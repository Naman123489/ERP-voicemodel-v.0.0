
import pytest

from app.core import security


def test_password_hash_is_not_plain_text():
    password = "BikeWorker123!"

    hashed = security.hash_password(password)

    assert hashed != password


def test_correct_password_is_accepted():
    password = "BikeWorker123!"
    hashed = security.hash_password(password)

    assert security.verify_password(password, hashed) is True


def test_wrong_password_is_rejected():
    hashed = security.hash_password("BikeWorker123!")

    assert security.verify_password("WrongPassword!", hashed) is False


def test_empty_password_is_rejected():
    with pytest.raises(ValueError, match="Password cannot be empty"):
        security.hash_password("")


def test_access_token_round_trip(monkeypatch):
    monkeypatch.setattr(
        security.settings,
        "JWT_SECRET_KEY",
        "test-only-secret-not-for-production",
    )

    token = security.create_access_token("worker-01")

    assert security.decode_access_token(token) == "worker-01"


def test_invalid_token_is_rejected(monkeypatch):
    monkeypatch.setattr(
        security.settings,
        "JWT_SECRET_KEY",
        "test-only-secret-not-for-production",
    )

    with pytest.raises(ValueError, match="Invalid or expired access token"):
        security.decode_access_token("not-a-valid-token")


def test_token_with_wrong_secret_is_rejected(monkeypatch):
    monkeypatch.setattr(
        security.settings,
        "JWT_SECRET_KEY",
        "first-test-only-secret",
    )
    token = security.create_access_token("worker-01")

    monkeypatch.setattr(
        security.settings,
        "JWT_SECRET_KEY",
        "different-test-only-secret",
    )

    with pytest.raises(ValueError, match="Invalid or expired access token"):
        security.decode_access_token(token)


def test_missing_secret_prevents_token_creation(monkeypatch):
    monkeypatch.setattr(security.settings, "JWT_SECRET_KEY", None)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY is not configured"):
        security.create_access_token("worker-01")
