from datetime import datetime, timedelta, timezone

from server.app.extensions import db
from server.app.models.feedback_submission import FeedbackSubmission
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLE, User
from server.app.services.feedback_service import FeedbackService


def feedback_payload(**overrides):
    payload = {
        "submission_id": "admin-feedback-001",
        "category": "bug",
        "message": "The sales report does not load correctly.",
        "contact_name": "Test Contact",
        "contact_email": "contact@example.com",
    }
    payload.update(overrides)
    return payload


def create_feedback(client, monkeypatch, **overrides):
    monkeypatch.setattr(FeedbackService, "_deliver", staticmethod(lambda record: None))
    payload = feedback_payload(**overrides)
    response = client.post("/feedback", json=payload)
    assert response.status_code == 202
    return FeedbackSubmission.query.filter_by(submission_id=payload["submission_id"]).one()


def login_admin(client, app):
    response = client.post("/auth/register", json={
        "username": "Platform Admin",
        "email": "platform-admin@example.com",
        "password": "test-password-123",
    })
    assert response.status_code == 201
    with app.app_context():
        user = User.query.filter_by(email="platform-admin@example.com").one()
        user.platform_role = PLATFORM_ADMIN_ROLE
        user.admin_password_hash = user.password_hash
        db.session.commit()
    client.post("/auth/logout")
    response = client.post("/admin/auth/login", json={
        "email": "platform-admin@example.com",
        "password": "test-password-123",
    })
    assert response.status_code == 200


def test_feedback_and_platform_audit_apis_require_platform_admin(client, app, monkeypatch):
    create_feedback(client, monkeypatch)
    for path in ("/admin/feedback", "/admin/feedback/1", "/admin/audit-logs"):
        assert client.get(path).status_code == 401, path
    assert client.patch("/admin/feedback/1/status", json={"support_status": "resolved"}).status_code == 401

    response = client.post("/auth/register", json={
        "username": "Ordinary Owner",
        "email": "owner@example.com",
        "password": "test-password-123",
    })
    assert response.status_code == 201
    response = client.post("/auth/login", json={
        "email": "owner@example.com",
        "password": "test-password-123",
    })
    assert response.status_code == 200
    for path in ("/admin/feedback", "/admin/feedback/1", "/admin/audit-logs"):
        assert client.get(path).status_code == 401, path
    assert client.patch("/admin/feedback/1/status", json={"support_status": "resolved"}).status_code == 401


def test_admin_feedback_list_filters_and_paginates(client, app, monkeypatch):
    create_feedback(client, monkeypatch)
    create_feedback(client, monkeypatch, submission_id="admin-feedback-002")
    login_admin(client, app)
    response = client.get("/admin/feedback?support_status=new&delivery_status=sent&category=bug&page=1&per_page=1")
    assert response.status_code == 200
    data = response.json["data"]
    assert len(data["items"]) == 1
    assert data["items"][0]["support_status"] == "new"
    assert data["items"][0]["delivery_status"] == "sent"
    assert data["pagination"]["total"] == 2
    assert data["pagination"]["has_next"] is True
    assert "message" not in data["items"][0]
    assert client.get("/admin/feedback?support_status=waiting").status_code == 400
    assert client.get("/admin/feedback?per_page=101").status_code == 400


def test_feedback_detail_and_status_update_keep_delivery_status_separate(client, app, monkeypatch):
    record = create_feedback(client, monkeypatch)
    login_admin(client, app)

    detail = client.get(f"/admin/feedback/{record.id}")
    assert detail.status_code == 200
    assert detail.json["data"]["feedback"]["message"] == "The sales report does not load correctly."
    assert detail.json["data"]["feedback"]["delivery_status"] == "sent"
    assert "delivery_error" not in detail.json["data"]["feedback"]

    changed = client.patch(
        f"/admin/feedback/{record.id}/status",
        json={"support_status": "in_progress"},
    )
    assert changed.status_code == 200
    assert changed.json["data"]["changed"] is True
    assert changed.json["data"]["feedback"]["support_status"] == "in_progress"
    db.session.refresh(record)
    assert record.status == "sent"
    assert record.support_status == "in_progress"

    audit = PlatformAuditLog.query.filter_by(
        action_type="feedback.support_status_changed",
        target_id=str(record.id),
    ).one()
    assert audit.actor_user_id == User.query.filter_by(email="platform-admin@example.com").one().id
    assert audit.target_type == "feedback_submission"
    assert audit.details == {
        "submission_id": "admin-feedback-001",
        "previous_support_status": "new",
        "new_support_status": "in_progress",
    }
    assert audit.created_at is not None


def test_invalid_and_missing_feedback_status_updates_do_not_write_audit(client, app, monkeypatch):
    record = create_feedback(client, monkeypatch)
    login_admin(client, app)
    assert client.patch(f"/admin/feedback/{record.id}/status", json={"support_status": "closed"}).status_code == 400
    assert client.patch("/admin/feedback/99999/status", json={"support_status": "resolved"}).status_code == 404
    assert PlatformAuditLog.query.filter_by(action_type="feedback.support_status_changed").count() == 0


def test_repeating_same_support_status_is_idempotent_without_duplicate_audit(client, app, monkeypatch):
    record = create_feedback(client, monkeypatch)
    login_admin(client, app)
    path = f"/admin/feedback/{record.id}/status"
    assert client.patch(path, json={"support_status": "resolved"}).status_code == 200
    second = client.patch(path, json={"support_status": "resolved"})
    assert second.status_code == 200
    assert second.json["data"]["changed"] is False
    assert PlatformAuditLog.query.filter_by(action_type="feedback.support_status_changed").count() == 1


def test_platform_audit_list_filters_and_paginates(client, app, monkeypatch):
    record = create_feedback(client, monkeypatch)
    login_admin(client, app)
    client.patch(f"/admin/feedback/{record.id}/status", json={"support_status": "resolved"})

    response = client.get(
        "/admin/audit-logs?action_type=feedback.support_status_changed"
        "&target_type=feedback_submission&target_id=1&page=1&per_page=10"
    )
    assert response.status_code == 200
    data = response.json["data"]
    assert data["pagination"]["total"] == 1
    assert data["items"][0]["details"]["new_support_status"] == "resolved"
    assert data["items"][0]["actor"]["name"] == "Platform Admin"

    actor_id = data["items"][0]["actor_user_id"]
    assert client.get(f"/admin/audit-logs?actor_user_id={actor_id}").status_code == 200
    assert client.get("/admin/audit-logs?actor_user_id=bad").status_code == 400
    assert client.get("/admin/audit-logs?start_date=not-a-date").status_code == 400
    assert client.get("/admin/audit-logs?start_date=2026-11-20&end_date=2026-11-19").status_code == 400


def test_platform_audit_records_remain_immutable_after_status_change(client, app, monkeypatch):
    record = create_feedback(client, monkeypatch)
    login_admin(client, app)
    client.patch(f"/admin/feedback/{record.id}/status", json={"support_status": "resolved"})
    audit = PlatformAuditLog.query.filter_by(action_type="feedback.support_status_changed").one()
    audit.details = {"tampered": True}
    try:
        db.session.commit()
        assert False, "Platform audit records must be append-only"
    except ValueError:
        db.session.rollback()
    persisted = PlatformAuditLog.query.filter_by(action_type="feedback.support_status_changed").one()
    assert persisted.details["new_support_status"] == "resolved"
