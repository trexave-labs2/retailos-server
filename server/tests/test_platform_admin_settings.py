from server.app.extensions import db
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLE, User


def create_admin(client, app, email="admin-settings@example.com"):
    response = client.post("/auth/register", json={
        "username": "Admin User", "email": email, "password": "test-password-123"
    })
    assert response.status_code == 201
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        user.platform_role = PLATFORM_ADMIN_ROLE
        db.session.commit()
    response = client.post("/auth/login", json={
        "email": email, "password": "test-password-123"
    })
    assert response.status_code == 200


def test_admin_can_read_own_profile_without_sensitive_fields(client, app):
    create_admin(client, app)
    response = client.get("/admin/profile")
    assert response.status_code == 200
    profile = response.json["data"]["profile"]
    assert profile["email"] == "admin-settings@example.com"
    assert profile["platform_role"] == PLATFORM_ADMIN_ROLE
    assert "password_hash" not in profile
    assert "session_version" not in profile


def test_admin_profile_update_changes_only_name_and_audits(client, app):
    create_admin(client, app)
    response = client.patch("/admin/profile", json={"name": "Updated Admin"})
    assert response.status_code == 200
    assert response.json["data"]["changed"] is True
    with app.app_context():
        user = User.query.filter_by(email="admin-settings@example.com").one()
        assert user.name == "Updated Admin"
        audit = PlatformAuditLog.query.filter_by(
            action_type="platform_admin.profile_updated"
        ).one()
        assert audit.actor_user_id == user.id
        assert audit.details == {
            "previous_name": "Admin User", "new_name": "Updated Admin"
        }


def test_admin_profile_rejects_email_and_role_changes(client, app):
    create_admin(client, app)
    response = client.patch("/admin/profile", json={
        "name": "Changed", "email": "attacker@example.com",
        "platform_role": "owner"
    })
    assert response.status_code == 400
    with app.app_context():
        user = User.query.filter_by(email="admin-settings@example.com").one()
        assert user.name == "Admin User"
        assert user.email == "admin-settings@example.com"
        assert user.platform_role == PLATFORM_ADMIN_ROLE
        assert PlatformAuditLog.query.count() == 0


def test_admin_profile_noop_does_not_create_duplicate_audit(client, app):
    create_admin(client, app)
    response = client.patch("/admin/profile", json={"name": "Admin User"})
    assert response.status_code == 200
    assert response.json["data"]["changed"] is False
    with app.app_context():
        assert PlatformAuditLog.query.count() == 0


def test_admin_can_revoke_all_sessions_and_audit(client, app):
    create_admin(client, app)
    response = client.post("/admin/sessions/revoke-all")
    assert response.status_code == 200
    assert response.json["data"]["sessions_invalidated"] == "all"
    # The current cookie is cleared and every previously issued cookie is stale.
    protected = client.get("/admin/profile")
    assert protected.status_code == 401
    with app.app_context():
        user = User.query.filter_by(email="admin-settings@example.com").one()
        assert user.session_version == 1
        audit = PlatformAuditLog.query.filter_by(
            action_type="platform_admin.sessions_revoked"
        ).one()
        assert audit.actor_user_id == user.id
        assert audit.details["new_session_version"] == 1


def test_revoke_all_rejects_request_body(client, app):
    create_admin(client, app)
    response = client.post("/admin/sessions/revoke-all", json={"user_id": 99})
    assert response.status_code == 400
    with app.app_context():
        user = User.query.filter_by(email="admin-settings@example.com").one()
        assert user.session_version == 0
        assert PlatformAuditLog.query.count() == 0


def test_admin_password_change_is_audited_and_invalidates_sessions(client, app):
    create_admin(client, app)
    response = client.post("/auth/change-password", json={
        "current_password": "test-password-123",
        "new_password": "a-different-password-456"
    })
    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="admin-settings@example.com").one()
        assert user.session_version == 1
        audit = PlatformAuditLog.query.filter_by(
            action_type="platform_admin.password_changed"
        ).one()
        assert audit.actor_user_id == user.id
        assert audit.details == {"sessions_invalidated": True}
        assert "password" not in str(audit.details).lower()
