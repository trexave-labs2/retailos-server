from datetime import timedelta

import pytest

from server.app import create_app
from server.app.config import TestConfig
from server.app.extensions import db, limiter
from server.app.models.password_reset_token import PasswordResetToken
from server.app.models.user import User
from server.app.services.password_reset_service import PasswordResetService
from server.app.utils.time import now_utc


def register_user(client, email="reset@example.com", password="old-password"):
    response = client.post("/auth/register", json={
        "username": "Reset User",
        "email": email,
        "password": password,
    })
    assert response.status_code == 201
    return response.json["data"]["user"]


@pytest.fixture
def captured_reset_tokens(monkeypatch):
    tokens = []

    def capture(recipient, raw_token):
        tokens.append((recipient, raw_token))

    monkeypatch.setattr(
        PasswordResetService,
        "_deliver_reset_email",
        staticmethod(capture),
    )
    return tokens


def request_reset(client, email="reset@example.com"):
    return client.post("/auth/password-reset/request", json={"email": email})


def complete_reset(client, token, password="new-password"):
    return client.post("/auth/password-reset/complete", json={
        "token": token,
        "new_password": password,
    })


def test_unknown_email_and_registered_email_have_identical_public_response(
    client, captured_reset_tokens
):
    register_user(client)
    unknown = request_reset(client, "not-registered@example.com")
    registered = request_reset(client)

    assert unknown.status_code == registered.status_code == 202
    assert unknown.json == registered.json
    assert captured_reset_tokens and captured_reset_tokens[0][0] == "reset@example.com"


def test_reset_token_is_random_and_only_hash_is_persisted(
    client, app, captured_reset_tokens
):
    register_user(client)
    response = request_reset(client)
    assert response.status_code == 202
    raw_token = captured_reset_tokens[0][1]

    with app.app_context():
        record = PasswordResetToken.query.one()
        assert record.token_hash != raw_token
        assert len(record.token_hash) == 64
        assert record.used_at is None
        assert record.expires_at > record.created_at


def test_valid_token_resets_password_and_can_only_be_used_once(
    client, app, captured_reset_tokens
):
    register_user(client)
    assert request_reset(client).status_code == 202
    token = captured_reset_tokens[0][1]

    response = complete_reset(client, token)
    assert response.status_code == 200
    assert response.json["message"] == "PASSWORD_RESET_SUCCESSFUL_LOGIN_AGAIN"

    reused = complete_reset(client, token)
    assert reused.status_code == 400
    assert reused.json["error"]["code"] == "PASSWORD_RESET_INVALID_OR_EXPIRED"

    login = client.post("/auth/login", json={
        "email": "reset@example.com",
        "password": "new-password",
    })
    assert login.status_code == 200

    old_password = client.post("/auth/login", json={
        "email": "reset@example.com",
        "password": "old-password",
    })
    assert old_password.status_code == 401


def test_expired_token_is_rejected(client, app, captured_reset_tokens):
    register_user(client)
    request_reset(client)
    token = captured_reset_tokens[0][1]

    with app.app_context():
        record = PasswordResetToken.query.one()
        record.expires_at = now_utc().replace(tzinfo=None) - timedelta(seconds=1)
        db.session.commit()

    response = complete_reset(client, token)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "PASSWORD_RESET_INVALID_OR_EXPIRED"


def test_invalid_password_does_not_consume_valid_token(
    client, app, captured_reset_tokens
):
    register_user(client)
    request_reset(client)
    token = captured_reset_tokens[0][1]

    invalid = complete_reset(client, token, "short")
    assert invalid.status_code == 400
    assert invalid.json["error"]["code"] == "VALIDATION_ERROR"

    with app.app_context():
        record = PasswordResetToken.query.one()
        assert record.used_at is None

    valid = complete_reset(client, token, "valid-new-password")
    assert valid.status_code == 200


def test_issuing_new_reset_token_invalidates_previous_token(
    client, captured_reset_tokens
):
    register_user(client)
    request_reset(client)
    first_token = captured_reset_tokens[-1][1]
    request_reset(client)
    second_token = captured_reset_tokens[-1][1]

    assert complete_reset(client, first_token).status_code == 400
    assert complete_reset(client, second_token).status_code == 200


def test_password_reset_invalidates_all_existing_sessions(
    app, captured_reset_tokens
):
    with app.app_context():
        db.create_all()

    first_client = app.test_client()
    second_client = app.test_client()
    register_user(first_client)

    for test_client in (first_client, second_client):
        login = test_client.post("/auth/login", json={
            "email": "reset@example.com",
            "password": "old-password",
        })
        assert login.status_code == 200

    assert request_reset(first_client).status_code == 202
    token = captured_reset_tokens[0][1]
    assert complete_reset(first_client, token).status_code == 200

    assert first_client.get("/auth/me").status_code == 401
    assert second_client.get("/auth/me").status_code == 401


def test_password_reset_request_is_rate_limited_by_ip():
    class RateLimitedConfig(TestConfig):
        RATELIMIT_ENABLED = True

    app = create_app(RateLimitedConfig)
    with app.app_context():
        db.create_all()
        limiter.reset()
        test_client = app.test_client()
        try:
            for _ in range(5):
                response = request_reset(test_client, "unknown@example.com")
                assert response.status_code == 202
            limited = request_reset(test_client, "unknown@example.com")
            assert limited.status_code == 429
        finally:
            limiter.reset()
            db.session.remove()
            db.drop_all()


def test_password_reset_request_rejects_invalid_email(client):
    response = request_reset(client, "not-an-email")
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


def test_email_delivery_failure_does_not_reveal_account_existence(
    client, monkeypatch
):
    register_user(client)

    def fail_delivery(recipient, raw_token):
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(
        PasswordResetService,
        "_deliver_reset_email",
        staticmethod(fail_delivery),
    )

    known = request_reset(client)
    unknown = request_reset(client, "unknown@example.com")
    assert known.status_code == unknown.status_code == 202
    assert known.json == unknown.json

    with client.application.app_context():
        record = PasswordResetToken.query.one()
        assert record.used_at is not None



def test_password_reset_completion_is_rate_limited_by_ip():
    class RateLimitedConfig(TestConfig):
        RATELIMIT_ENABLED = True

    app = create_app(RateLimitedConfig)
    with app.app_context():
        db.create_all()
        limiter.reset()
        test_client = app.test_client()
        try:
            for _ in range(10):
                response = complete_reset(test_client, "a" * 32)
                assert response.status_code == 400
            limited = complete_reset(test_client, "a" * 32)
            assert limited.status_code == 429
        finally:
            limiter.reset()
            db.session.remove()
            db.drop_all()
