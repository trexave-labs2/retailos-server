import pytest

from server.app.middleware.auth_middleware import require_platform_admin
from server.app.extensions import db
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLE, SUPER_ADMIN_ROLE, User
from server.app.utils.response import Response


@pytest.fixture()
def platform_admin_probe(app):
    @app.get("/_test/platform-admin")
    @require_platform_admin
    def platform_admin_only():
        return Response.success_response({}, "PLATFORM_ADMIN_ACCESS_GRANTED"), 200

    return "/_test/platform-admin"


def register_user(client, email="owner@example.com", extra=None):
    payload = {
        "username": "Store Owner",
        "email": email,
        "password": "test-password-123",
    }
    if extra:
        payload.update(extra)
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    return response


def login_user(client, email="owner@example.com"):
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "test-password-123"},
    )
    assert response.status_code == 200


def test_platform_admin_guard_returns_401_without_session(client, platform_admin_probe):
    response = client.get(platform_admin_probe)

    assert response.status_code == 401
    assert response.json["error"]["code"] == "PLATFORM_ADMIN_SESSION_REQUIRED"


def test_store_owner_is_forbidden_from_platform_admin_routes(
    client, platform_admin_probe
):
    register_user(client)
    login_user(client)

    response = client.get(platform_admin_probe)

    assert response.status_code == 401
    assert response.json["error"]["code"] == "PLATFORM_ADMIN_SESSION_REQUIRED"


def test_client_supplied_platform_role_is_ignored(client, app, platform_admin_probe):
    register_user(
        client,
        extra={"platform_role": PLATFORM_ADMIN_ROLE, "is_admin": True},
    )
    login_user(client)

    response = client.get(platform_admin_probe)

    assert response.status_code == 401
    with app.app_context():
        user = User.query.filter_by(email="owner@example.com").one()
        assert user.platform_role is None


def test_platform_admin_can_access_protected_route(
    client, app, platform_admin_probe
):
    register_user(client)
    with app.app_context():
        user = User.query.filter_by(email="owner@example.com").one()
        user.platform_role = PLATFORM_ADMIN_ROLE
        user.admin_password_hash = user.password_hash
        db.session.commit()

    response = client.post("/admin/auth/login", json={"email": "owner@example.com", "password": "test-password-123"})
    assert response.status_code == 200
    response = client.get(platform_admin_probe)

    assert response.status_code == 200
    assert response.json["message"] == "PLATFORM_ADMIN_ACCESS_GRANTED"


def test_bootstrap_command_provisions_only_the_configured_first_admin(
    app, client
):
    email = "first-admin@example.com"
    register_user(client, email=email)
    app.config["PLATFORM_ADMIN_EMAIL"] = email
    app.config["PLATFORM_ADMIN_PASSWORD"] = "initial-password-123"

    runner = app.test_cli_runner()
    result = runner.invoke(args=["platform-admin", "bootstrap"])

    assert result.exit_code == 0, result.output
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.platform_role == SUPER_ADMIN_ROLE
        assert user.admin_password_hash is not None
        audit = PlatformAuditLog.query.filter_by(
            action_type="platform_admin.bootstrap"
        ).one()
        assert audit.actor_user_id == user.id
        assert audit.target_id == str(user.id)

    second_attempt = runner.invoke(args=["platform-admin", "bootstrap"])
    assert second_attempt.exit_code == 0, second_attempt.output
    assert "already provisioned" in second_attempt.output
    with app.app_context():
        assert PlatformAuditLog.query.filter_by(
            action_type="platform_admin.bootstrap"
        ).count() == 1


def test_bootstrap_rejects_weak_configured_password(app, client):
    email = "first-admin@example.com"
    register_user(client, email=email)
    app.config["PLATFORM_ADMIN_EMAIL"] = email
    app.config["PLATFORM_ADMIN_PASSWORD"] = "short"

    result = app.test_cli_runner().invoke(
        args=["platform-admin", "bootstrap"]
    )

    assert result.exit_code != 0
    assert "PLATFORM_ADMIN_PASSWORD" in result.output
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.platform_role is None
        assert PlatformAuditLog.query.count() == 0


def test_platform_audit_records_are_immutable(app):
    with app.app_context():
        user = User(
            name="Audit Admin",
            email="audit-admin@example.com",
            password_hash="not-used",
            platform_role=PLATFORM_ADMIN_ROLE,
        )
        db.session.add(user)
        db.session.flush()
        audit = PlatformAuditLog(
            actor_user_id=user.id,
            action_type="platform_admin.test",
            target_type="user",
            target_id=str(user.id),
        )
        db.session.add(audit)
        db.session.commit()

        audit.action_type = "platform_admin.changed"
        with pytest.raises(ValueError, match="cannot be modified"):
            db.session.flush()
        db.session.rollback()

        audit = PlatformAuditLog.query.one()
        db.session.delete(audit)
        with pytest.raises(ValueError, match="cannot be deleted"):
            db.session.flush()
        db.session.rollback()


def test_client_and_admin_same_email_use_independent_passwords_and_sessions(app, client):
    email = "shared@example.com"
    register_user(client, email=email)
    app.config["PLATFORM_ADMIN_EMAIL"] = email
    app.config["PLATFORM_ADMIN_PASSWORD"] = "different-admin-password-456"
    result = app.test_cli_runner().invoke(args=["platform-admin", "bootstrap"])
    assert result.exit_code == 0, result.output

    # Each application accepts only the password in its own credential field.
    client_login = client.post("/auth/login", json={
        "email": email, "password": "test-password-123"
    })
    assert client_login.status_code == 200
    admin_login = client.post("/admin/auth/login", json={
        "email": email, "password": "different-admin-password-456"
    })
    assert admin_login.status_code == 200

    # Logging into either application does not invalidate the other session.
    assert client.get("/auth/me").status_code == 200
    assert client.get("/admin/profile").status_code == 200

    assert client.post("/auth/login", json={
        "email": email, "password": "different-admin-password-456"
    }).status_code == 401
    assert client.post("/admin/auth/login", json={
        "email": email, "password": "test-password-123"
    }).status_code == 401
