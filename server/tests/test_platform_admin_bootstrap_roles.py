import pytest

from server.app.extensions import db
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLE, SUPER_ADMIN_ROLE, User
from server.app.utils.security import verify_password


def _register(client, email):
    return client.post("/auth/register", json={
        "username": "Test User",
        "email": email,
        "password": "test-password-123",
    })


def _login(client, email):
    response = client.post("/auth/login", json={"email": email, "password": "test-password-123"})
    assert response.status_code == 200


def _bootstrap_config(app, email="root@example.com", password="initial-password-123"):
    app.config["PLATFORM_ADMIN_EMAIL"] = email
    app.config["PLATFORM_ADMIN_PASSWORD"] = password


def test_bootstrap_creates_super_admin_without_registration(app):
    _bootstrap_config(app)
    result = app.test_cli_runner().invoke(args=["platform-admin", "bootstrap"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        user = User.query.filter_by(email="root@example.com").one()
        assert user.platform_role == SUPER_ADMIN_ROLE
        assert verify_password("initial-password-123", user.password_hash)
        assert PlatformAuditLog.query.filter_by(action_type="platform_admin.bootstrap").count() == 1


def test_bootstrap_is_idempotent_and_never_resets_existing_password(app):
    _bootstrap_config(app)
    runner = app.test_cli_runner()
    first = runner.invoke(args=["platform-admin", "bootstrap"])
    assert first.exit_code == 0, first.output
    with app.app_context():
        user = User.query.filter_by(email="root@example.com").one()
        user.password_hash = "existing-password-hash"
        db.session.commit()
    second = runner.invoke(args=["platform-admin", "bootstrap"])
    assert second.exit_code == 0, second.output
    with app.app_context():
        user = User.query.filter_by(email="root@example.com").one()
        assert user.password_hash == "existing-password-hash"
        assert PlatformAuditLog.query.filter_by(action_type="platform_admin.bootstrap").count() == 1


def test_existing_registered_account_is_promoted_without_password_reset(app, client):
    _register(client, "root@example.com")
    _bootstrap_config(app)
    result = app.test_cli_runner().invoke(args=["platform-admin", "bootstrap"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        user = User.query.filter_by(email="root@example.com").one()
        assert user.platform_role == SUPER_ADMIN_ROLE
        assert verify_password("test-password-123", user.password_hash)


def test_missing_bootstrap_configuration_fails_closed(app):
    result = app.test_cli_runner().invoke(args=["platform-admin", "bootstrap"])
    assert result.exit_code != 0
    assert "PLATFORM_ADMIN_EMAIL" in result.output


def test_admin_cannot_manage_other_admin_roles(app, client):
    with app.app_context():
        root = User(name="Root", email="root@example.com", password_hash="hash", platform_role=SUPER_ADMIN_ROLE)
        admin = User(name="Admin", email="admin@example.com", password_hash="hash", platform_role=PLATFORM_ADMIN_ROLE)
        target = User(name="Target", email="target@example.com", password_hash="hash")
        db.session.add_all([root, admin, target])
        db.session.commit()
        admin_id, target_id = admin.id, target.id
    client.post("/auth/login", json={"email": "admin@example.com", "password": "not-valid"})
    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["session_version"] = 0
    response = client.put(f"/admin/users/{target_id}/admin-role", json={"platform_role": "admin"})
    assert response.status_code == 403
    assert response.json["error"]["code"] == "SUPER_ADMIN_REQUIRED"


def test_super_admin_can_grant_and_revoke_admin_with_audit(app, client):
    with app.app_context():
        root = User(name="Root", email="root@example.com", password_hash="hash", platform_role=SUPER_ADMIN_ROLE)
        target = User(name="Target", email="target@example.com", password_hash="hash")
        db.session.add_all([root, target])
        db.session.commit()
        root_id, target_id = root.id, target.id
    with client.session_transaction() as sess:
        sess["user_id"] = root_id
        sess["session_version"] = 0
    granted = client.put(f"/admin/users/{target_id}/admin-role", json={"platform_role": "admin"})
    assert granted.status_code == 200
    revoked = client.put(f"/admin/users/{target_id}/admin-role", json={"platform_role": None})
    assert revoked.status_code == 200
    with app.app_context():
        target = db.session.get(User, target_id)
        assert target.platform_role is None
        actions = [row.action_type for row in PlatformAuditLog.query.order_by(PlatformAuditLog.id).all()]
        assert actions == ["platform_admin.role_granted", "platform_admin.role_revoked"]
