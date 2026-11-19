import ipaddress
from unittest.mock import patch

import pytest

from server.app.extensions import db
from server.app.models.monitor import Monitor, MonitorCheck, MonitorIncident
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLE, User
from server.app.services.monitoring_service import validate_target_url, _resolve_public_addresses


ADMIN_PATHS = [
    "/admin/monitoring/summary",
    "/admin/monitors",
    "/admin/incidents",
    "/admin/monitors/1/checks",
]


def register_and_login(client, email, name="Test User"):
    response = client.post("/auth/register", json={
        "username": name,
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 201
    response = client.post("/auth/login", json={
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 200


def promote_to_admin(client, app, email):
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        user.platform_role = PLATFORM_ADMIN_ROLE
        # Preserve the current retail password as the initial Admin password,
        # matching the existing-account promotion compatibility behavior.
        user.admin_password_hash = user.password_hash
        db.session.commit()

    admin_login = client.post("/admin/auth/login", json={
        "email": email,
        "password": "test-password-123",
    })
    assert admin_login.status_code == 200


def create_monitor(client, name="RetailOS API", target="https://example.com/health"):
    return client.post("/admin/monitors", json={
        "name": name,
        "target_url": target,
        "interval_seconds": 600,
        "enabled": True,
    })


def test_all_monitoring_routes_require_platform_admin(client):
    for path in ADMIN_PATHS:
        response = client.get(path)
        assert response.status_code == 401, path

    register_and_login(client, "owner@example.com", "Store Owner")
    for path in ADMIN_PATHS:
        response = client.get(path)
        # A Client cookie is not an Admin credential, even for a retail user.
        assert response.status_code == 401, path

    assert client.post("/admin/monitors", json={}).status_code == 401
    assert client.patch("/admin/monitors/1", json={"enabled": False}).status_code == 401
    assert client.post("/admin/monitors/1/checks").status_code == 401


def test_admin_can_create_update_pause_resume_and_list_monitor(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote_to_admin(client, app, "admin@example.com")

    created = create_monitor(client)
    assert created.status_code == 201
    monitor_data = created.json["data"]["monitor"]
    monitor_id = monitor_data["id"]
    assert monitor_data["current_status"] == "unknown"
    assert monitor_data["next_check_at"] is not None

    listed = client.get("/admin/monitors")
    assert listed.status_code == 200
    assert listed.json["data"]["pagination"]["total"] == 1

    paused = client.patch(f"/admin/monitors/{monitor_id}", json={"enabled": False})
    assert paused.status_code == 200
    assert paused.json["data"]["monitor"]["state"] == "paused"
    assert paused.json["data"]["monitor"]["next_check_at"] is None

    resumed = client.patch(f"/admin/monitors/{monitor_id}", json={"enabled": True})
    assert resumed.status_code == 200
    assert resumed.json["data"]["monitor"]["state"] == "enabled"
    assert resumed.json["data"]["monitor"]["current_status"] == "unknown"

    renamed = client.patch(f"/admin/monitors/{monitor_id}", json={"name": "Production API"})
    assert renamed.status_code == 200
    assert renamed.json["data"]["monitor"]["name"] == "Production API"

    with app.app_context():
        assert PlatformAuditLog.query.filter(PlatformAuditLog.action_type.like("monitor.%")).count() == 4


def test_monitor_payload_validation_rejects_invalid_urls_and_intervals(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote_to_admin(client, app, "admin@example.com")

    for target in [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/health",
        "http://192.168.1.10/health",
        "http://169.254.169.254/latest/meta-data",
        "https://user:password@example.com/health",
        "https://example.com/health?token=secret",
    ]:
        response = create_monitor(client, target=target)
        assert response.status_code == 400, target

    invalid_interval = client.post("/admin/monitors", json={
        "name": "Invalid interval",
        "target_url": "https://example.com/health",
        "interval_seconds": 5,
    })
    assert invalid_interval.status_code == 400

    extra_field = client.post("/admin/monitors", json={
        "name": "Invalid",
        "target_url": "https://example.com/health",
        "method": "POST",
    })
    assert extra_field.status_code == 400


def test_manual_check_persists_history_and_incident_recovery(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote_to_admin(client, app, "admin@example.com")
    created = create_monitor(client)
    monitor_id = created.json["data"]["monitor"]["id"]

    with patch("server.app.services.monitoring_service.perform_http_check") as check:
        check.return_value = {
            "status": "down", "response_time_ms": 25,
            "status_code": 503, "error_type": "http_error",
        }
        failed = client.post(f"/admin/monitors/{monitor_id}/checks")
        assert failed.status_code == 200
        assert failed.json["data"]["check"]["status"] == "down"

        check.return_value = {
            "status": "up", "response_time_ms": 12,
            "status_code": 200, "error_type": None,
        }
        recovered = client.post(f"/admin/monitors/{monitor_id}/checks")
        assert recovered.status_code == 200
        assert recovered.json["data"]["monitor"]["current_status"] == "up"

    history = client.get(f"/admin/monitors/{monitor_id}/checks")
    assert history.status_code == 200
    assert len(history.json["data"]["items"]) == 2
    assert history.json["data"]["items"][0]["status"] == "up"

    incidents = client.get("/admin/incidents")
    assert incidents.status_code == 200
    assert len(incidents.json["data"]["items"]) == 1
    assert incidents.json["data"]["items"][0]["status"] == "resolved"

    with app.app_context():
        assert MonitorCheck.query.filter_by(monitor_id=monitor_id).count() == 2
        assert MonitorIncident.query.filter_by(monitor_id=monitor_id).one().status == "resolved"


def test_manual_check_rejects_paused_monitor(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote_to_admin(client, app, "admin@example.com")
    monitor_id = create_monitor(client).json["data"]["monitor"]["id"]
    client.patch(f"/admin/monitors/{monitor_id}", json={"enabled": False})
    response = client.post(f"/admin/monitors/{monitor_id}/checks")
    assert response.status_code == 409
    assert response.json["error"]["code"] == "MONITOR_PAUSED"


def test_target_validation_rejects_non_public_dns_results():
    with patch("server.app.services.monitoring_service.socket.getaddrinfo") as resolve:
        resolve.return_value = [
            (None, None, None, None, ("8.8.8.8", 443)),
            (None, None, None, None, ("10.0.0.5", 443)),
        ]
        with pytest.raises(ValueError, match="prohibited"):
            _resolve_public_addresses("example.com", 443)


def test_target_validation_accepts_only_http_schemes_and_public_literal_hosts():
    assert validate_target_url("https://example.com/health").hostname == "example.com"
    with pytest.raises(ValueError):
        validate_target_url("ftp://example.com/file")
    with pytest.raises(ValueError):
        validate_target_url("http://[::1]/")


def test_response_size_is_bounded():
    from server.app.services.monitoring_service import MAX_RESPONSE_BYTES

    assert MAX_RESPONSE_BYTES == 65536
