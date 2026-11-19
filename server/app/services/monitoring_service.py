"""Platform monitoring management and hardened outbound HTTP checks."""
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit

from flask import current_app, request
from sqlalchemy import func

from server.app.extensions import db
from server.app.models.monitor import Monitor, MonitorCheck, MonitorIncident
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.utils.response import Response


DEFAULT_INTERVAL_SECONDS = 600
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 86400
MAX_PAGE_SIZE = 100
MAX_RESPONSE_BYTES = 65536
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 5


def utc_now():
    return datetime.now(timezone.utc)


def validate_target_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("target_url must be a valid HTTP(S) URL no longer than 2048 characters")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise ValueError("target_url is invalid")
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("target_url must use http or https and include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("target_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("target_url must not contain query parameters or fragments")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("target_url port is invalid")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise ValueError("target_url hostname is not publicly routable")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("target_url must not point to a private or reserved address")
    return parsed


def _resolve_public_addresses(hostname, port):
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("target host could not be resolved")
    addresses = []
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise ValueError("target host resolves to a prohibited network address")
        if str(address) not in addresses:
            addresses.append(str(address))
    if not addresses:
        raise ValueError("target host has no usable public address")
    return addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname, address, port, timeout):
        super().__init__(hostname, port=port, timeout=timeout)
        self._pinned_address = address

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def __init__(self, hostname, address, port, timeout):
        super().__init__(hostname, address, port, timeout)
        self._context = ssl.create_default_context()

    def connect(self):
        raw = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def perform_http_check(target_url):
    """Perform one GET without redirects, caller-controlled headers, or unbounded reads."""
    parsed = validate_target_url(target_url)
    hostname = parsed.hostname
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = _resolve_public_addresses(hostname, port)
    except ValueError:
        return {"status": "down", "response_time_ms": 0, "status_code": None, "error_type": "target_rejected"}
    started = time.monotonic()
    connection = None
    try:
        connection_type = _PinnedHTTPSConnection if parsed.scheme.lower() == "https" else _PinnedHTTPConnection
        connection = connection_type(hostname, addresses[0], port, CONNECT_TIMEOUT_SECONDS)
        path = parsed.path or "/"
        connection.request("GET", path, headers={
            "Host": hostname if parsed.port is None else f"{hostname}:{port}",
            "User-Agent": "TX-RetailOS-Monitor/1.0",
            "Accept": "*/*",
            "Connection": "close",
        })
        connection.sock.settimeout(READ_TIMEOUT_SECONDS)
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        elapsed = int((time.monotonic() - started) * 1000)
        if len(body) > MAX_RESPONSE_BYTES or (response.length is not None and response.length > MAX_RESPONSE_BYTES):
            return {"status": "down", "response_time_ms": elapsed, "status_code": response.status, "error_type": "response_too_large"}
        if response.status >= 400:
            return {"status": "down", "response_time_ms": elapsed, "status_code": response.status, "error_type": "http_error"}
        return {"status": "up", "response_time_ms": elapsed, "status_code": response.status, "error_type": None}
    except (OSError, http.client.HTTPException, ssl.SSLError, ValueError):
        elapsed = int((time.monotonic() - started) * 1000)
        return {"status": "down", "response_time_ms": elapsed, "status_code": None, "error_type": "connection_failed"}
    finally:
        if connection is not None:
            connection.close()


def _page_args():
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", "20"))
    except (TypeError, ValueError):
        return None, None, (Response.error_response("VALIDATION_ERROR", "page and per_page must be integers", {}), 400)
    if page < 1 or per_page < 1 or per_page > MAX_PAGE_SIZE:
        return None, None, (Response.error_response("VALIDATION_ERROR", "page must be positive and per_page must be between 1 and 100", {}), 400)
    return page, per_page, None


def _paged(query, page, per_page, serialize):
    total = query.order_by(None).count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [serialize(row) for row in rows],
        "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page,
            "has_next": page * per_page < total, "has_previous": page > 1,
        },
    }


def _monitor_dict(monitor):
    return {
        "id": monitor.id, "name": monitor.name, "target_url": monitor.target_url,
        "enabled": monitor.enabled, "state": "enabled" if monitor.enabled else "paused",
        "interval_seconds": monitor.interval_seconds, "current_status": monitor.current_status,
        "last_check_at": monitor.last_check_at.isoformat() if monitor.last_check_at else None,
        "next_check_at": monitor.next_check_at.isoformat() if monitor.next_check_at else None,
        "latest_response_ms": monitor.latest_response_ms,
        "latest_status_code": monitor.latest_status_code, "latest_error": monitor.latest_error,
        "consecutive_failures": monitor.consecutive_failures,
        "created_at": monitor.created_at.isoformat(), "updated_at": monitor.updated_at.isoformat(),
    }


def _audit(actor_id, action, monitor, details=None):
    db.session.add(PlatformAuditLog(
        actor_user_id=actor_id, action_type=action, target_type="monitor",
        target_id=str(monitor.id), details=details or {},
    ))


def _apply_check(monitor, result, checked_at=None):
    checked_at = checked_at or utc_now()
    monitor.last_check_at = checked_at
    monitor.latest_response_ms = result.get("response_time_ms")
    monitor.latest_status_code = result.get("status_code")
    monitor.latest_error = result.get("error_type")
    monitor.next_check_at = checked_at + timedelta(seconds=monitor.interval_seconds) if monitor.enabled else None
    check = MonitorCheck(
        monitor_id=monitor.id, status=result["status"], checked_at=checked_at,
        response_time_ms=result.get("response_time_ms"),
        status_code=result.get("status_code"), error_type=result.get("error_type"),
    )
    db.session.add(check)
    if result["status"] == "up":
        monitor.current_status = "up"
        monitor.consecutive_failures = 0
        incident = MonitorIncident.query.filter_by(monitor_id=monitor.id, status="open").order_by(MonitorIncident.opened_at.desc()).first()
        if incident:
            incident.status = "resolved"
            incident.resolved_at = checked_at
    else:
        monitor.current_status = "down"
        monitor.consecutive_failures += 1
        incident = MonitorIncident.query.filter_by(monitor_id=monitor.id, status="open").order_by(MonitorIncident.opened_at.desc()).first()
        if incident:
            incident.failure_count = monitor.consecutive_failures
            incident.latest_error = result.get("error_type")
        else:
            db.session.add(MonitorIncident(
                monitor_id=monitor.id, status="open", opened_at=checked_at,
                failure_count=monitor.consecutive_failures, latest_error=result.get("error_type"),
            ))
    return check


class MonitoringService:
    @staticmethod
    def summary():
        total = Monitor.query.count()
        enabled = Monitor.query.filter_by(enabled=True).count()
        paused = total - enabled
        up = Monitor.query.filter_by(current_status="up").count()
        down = Monitor.query.filter_by(current_status="down").count()
        open_incidents = MonitorIncident.query.filter_by(status="open").count()
        return Response.success_response({
            "monitors_total": total, "monitors_enabled": enabled, "monitors_paused": paused,
            "monitors_up": up, "monitors_down": down, "open_incidents": open_incidents,
            "generated_at": utc_now().isoformat(),
        }, "MONITORING_SUMMARY_RETRIEVED"), 200

    @staticmethod
    def list_monitors():
        page, per_page, error = _page_args()
        if error:
            return error
        query = Monitor.query.order_by(Monitor.created_at.desc(), Monitor.id.desc())
        state = request.args.get("state")
        if state == "enabled":
            query = query.filter_by(enabled=True)
        elif state == "paused":
            query = query.filter_by(enabled=False)
        elif state not in (None, ""):
            return Response.error_response("VALIDATION_ERROR", "state must be enabled or paused", {}), 400
        return Response.success_response(_paged(query, page, per_page, _monitor_dict), "MONITORS_RETRIEVED"), 200

    @staticmethod
    def create_monitor(actor_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return Response.error_response("VALIDATION_ERROR", "A JSON object is required", {}), 400
        name = payload.get("name")
        target_url = payload.get("target_url")
        interval = payload.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            return Response.error_response("VALIDATION_ERROR", "name must contain 1 to 120 characters", {}), 400
        try:
            validate_target_url(target_url)
        except ValueError as error:
            return Response.error_response("VALIDATION_ERROR", str(error), {}), 400
        if isinstance(interval, bool) or not isinstance(interval, int) or not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
            return Response.error_response("VALIDATION_ERROR", "interval_seconds must be between 60 and 86400", {}), 400
        if set(payload) - {"name", "target_url", "interval_seconds", "enabled"}:
            return Response.error_response("VALIDATION_ERROR", "Unsupported monitor fields", {}), 400
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            return Response.error_response("VALIDATION_ERROR", "enabled must be a boolean", {}), 400
        monitor = Monitor(
            name=name.strip(), target_url=target_url.strip(), enabled=enabled,
            interval_seconds=interval, current_status="unknown" if enabled else "paused",
            next_check_at=utc_now() if enabled else None,
        )
        db.session.add(monitor)
        db.session.flush()
        _audit(actor_id, "monitor.created", monitor, {"enabled": enabled, "interval_seconds": interval})
        db.session.commit()
        return Response.success_response({"monitor": _monitor_dict(monitor)}, "MONITOR_CREATED"), 201

    @staticmethod
    def update_monitor(actor_id, monitor_id):
        monitor = db.session.get(Monitor, monitor_id)
        if not monitor:
            return Response.error_response("MONITOR_NOT_FOUND", "Monitor not found", {}), 404
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not payload:
            return Response.error_response("VALIDATION_ERROR", "A non-empty JSON object is required", {}), 400
        allowed = {"name", "target_url", "interval_seconds", "enabled"}
        if set(payload) - allowed:
            return Response.error_response("VALIDATION_ERROR", "Unsupported monitor fields", {}), 400
        changes = {}
        if "name" in payload:
            value = payload["name"]
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120:
                return Response.error_response("VALIDATION_ERROR", "name must contain 1 to 120 characters", {}), 400
            if value.strip() != monitor.name:
                changes["name"] = [monitor.name, value.strip()]
                monitor.name = value.strip()
        if "target_url" in payload:
            value = payload["target_url"]
            try:
                validate_target_url(value)
            except ValueError as error:
                return Response.error_response("VALIDATION_ERROR", str(error), {}), 400
            if value.strip() != monitor.target_url:
                changes["target_url_changed"] = True
                monitor.target_url = value.strip()
                monitor.current_status = "unknown"
                monitor.next_check_at = utc_now() if monitor.enabled else None
        if "interval_seconds" in payload:
            value = payload["interval_seconds"]
            if isinstance(value, bool) or not isinstance(value, int) or not MIN_INTERVAL_SECONDS <= value <= MAX_INTERVAL_SECONDS:
                return Response.error_response("VALIDATION_ERROR", "interval_seconds must be between 60 and 86400", {}), 400
            if value != monitor.interval_seconds:
                changes["interval_seconds"] = [monitor.interval_seconds, value]
                monitor.interval_seconds = value
        if "enabled" in payload:
            value = payload["enabled"]
            if not isinstance(value, bool):
                return Response.error_response("VALIDATION_ERROR", "enabled must be a boolean", {}), 400
            if value != monitor.enabled:
                changes["enabled"] = [monitor.enabled, value]
                monitor.enabled = value
                monitor.current_status = "unknown" if value else "paused"
                monitor.next_check_at = utc_now() if value else None
        if changes:
            _audit(actor_id, "monitor.updated", monitor, changes)
        db.session.commit()
        return Response.success_response({"monitor": _monitor_dict(monitor)}, "MONITOR_UPDATED"), 200

    @staticmethod
    def manual_check(actor_id, monitor_id):
        monitor = db.session.get(Monitor, monitor_id)
        if not monitor:
            return Response.error_response("MONITOR_NOT_FOUND", "Monitor not found", {}), 404
        if not monitor.enabled:
            return Response.error_response("MONITOR_PAUSED", "A paused monitor cannot be checked", {}), 409
        result = perform_http_check(monitor.target_url)
        _apply_check(monitor, result)
        _audit(actor_id, "monitor.manual_check_requested", monitor, {"result": result["status"]})
        db.session.commit()
        return Response.success_response({
            "monitor": _monitor_dict(monitor),
            "check": {
                "status": result["status"], "response_time_ms": result["response_time_ms"],
                "status_code": result["status_code"], "error_type": result["error_type"],
            },
        }, "MONITOR_CHECK_COMPLETED"), 200

    @staticmethod
    def check_history(monitor_id):
        monitor = db.session.get(Monitor, monitor_id)
        if not monitor:
            return Response.error_response("MONITOR_NOT_FOUND", "Monitor not found", {}), 404
        page, per_page, error = _page_args()
        if error:
            return error
        query = MonitorCheck.query.filter_by(monitor_id=monitor_id).order_by(MonitorCheck.checked_at.desc(), MonitorCheck.id.desc())
        data = _paged(query, page, per_page, lambda row: {
            "id": row.id, "status": row.status, "checked_at": row.checked_at.isoformat(),
            "response_time_ms": row.response_time_ms, "status_code": row.status_code,
            "error_type": row.error_type,
        })
        return Response.success_response(data, "MONITOR_CHECKS_RETRIEVED"), 200

    @staticmethod
    def list_incidents():
        page, per_page, error = _page_args()
        if error:
            return error
        query = MonitorIncident.query.order_by(MonitorIncident.opened_at.desc(), MonitorIncident.id.desc())
        status = request.args.get("status")
        if status:
            if status not in ("open", "resolved"):
                return Response.error_response("VALIDATION_ERROR", "status must be open or resolved", {}), 400
            query = query.filter_by(status=status)
        data = _paged(query, page, per_page, lambda row: {
            "id": row.id, "monitor_id": row.monitor_id,
            "monitor_name": db.session.get(Monitor, row.monitor_id).name if db.session.get(Monitor, row.monitor_id) else None,
            "status": row.status, "opened_at": row.opened_at.isoformat(),
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "failure_count": row.failure_count, "latest_error": row.latest_error,
        })
        return Response.success_response(data, "MONITOR_INCIDENTS_RETRIEVED"), 200
