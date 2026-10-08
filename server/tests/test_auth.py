# server/tests/test_auth.py
import pytest

from server.app.extensions import db
from server.app.models.user import User


@pytest.fixture()
def register_new_user(client):
    response = client.post(
        "/auth/register",
        json={
            "username": "testuser1",
            "email": "testuser1@example.com",
            "password": "test123"
        }
    )

    assert response.status_code == 201

    yield response.json

    # Delete the test user after the test.
    user = User.query.filter_by(
        email="testuser1@example.com"
    ).first()

    if user:
        db.session.delete(user)
        db.session.commit()


@pytest.fixture()
def login_user(client, register_new_user):
    response = client.post(
        "/auth/login",
        json={
            "email": "testuser1@example.com",
            "password": "test123"
        }
    )

    assert response.status_code == 200

    return True


# Check that a new user can register.
def test_register_new_user(client, register_new_user):
    assert register_new_user is not None


# Check that a registered user can log in and keep the session.
def test_login_user(client, register_new_user):
    response = client.post(
        "/auth/login",
        json={
            "email": "testuser1@example.com",
            "password": "test123"
        }
    )

    assert response.status_code == 200

    current_response = client.get("/auth/me")

    assert current_response.status_code == 200
    assert current_response.json["data"]["user"]["email"] == "testuser1@example.com"
    assert current_response.json["data"]["user"]["store_name"] == "testuser1's Store"


# Check that a logged-in user can log out.
def test_logout_user(client, login_user):
    response = client.post("/auth/logout")

    assert response.status_code == 200


# Check that duplicate emails are rejected.
def test_register_duplicate_email(client):
    first_response = client.post(
        "/auth/register",
        json={
            "username": "testuser1",
            "email": "testuser@example.com",
            "password": "test123"
        }
    )

    second_response = client.post(
        "/auth/register",
        json={
            "username": "testuser2",
            "email": "testuser@example.com",
            "password": "test456"
        }
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 400


# Check that a wrong password is rejected.
def test_wrong_password(client, register_new_user):
    response = client.post(
        "/auth/login",
        json={
            "email": "testuser1@example.com",
            "password": "test456"
        }
    )

    assert response.status_code == 401


# Check that protected routes reject logged-out users.
def test_unauthorized_access(client):
    response = client.post("/auth/logout")

    assert response.status_code == 401


# Check that logging out twice rejects the second request.
def test_double_logout(client, login_user):
    first_response = client.post("/auth/logout")
    second_response = client.post("/auth/logout")

    assert first_response.status_code == 200
    assert second_response.status_code == 401


# Check that a deleted user cannot use an old session.
def test_stale_session(client, login_user):
    user = User.query.filter_by(
        email="testuser1@example.com"
    ).first()

    if user:
        db.session.delete(user)
        db.session.commit()

    response = client.post("/auth/logout")

    assert response.status_code == 401


# Check that passwords longer than bcrypt's byte limit are rejected cleanly.
def test_register_rejects_password_longer_than_bcrypt_limit(client):
    response = client.post(
        "/auth/register",
        json={
            "username": "longpassworduser",
            "email": "longpassword@example.com",
            "password": "a" * 73
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert "password" in response.json["error"]["fields"]
    assert "72 bytes" in response.json["error"]["fields"]["password"]


# Check that login rejects an overlong password before bcrypt verification.
def test_login_rejects_password_longer_than_bcrypt_limit(client, register_new_user):
    response = client.post(
        "/auth/login",
        json={
            "email": "testuser1@example.com",
            "password": "a" * 73
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert "password" in response.json["error"]["fields"]