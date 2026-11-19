# Platform monitoring API

These endpoints are platform-admin-only. They use the existing signed session
cookie and the server-managed `users.platform_role` value. A missing/invalid
session returns HTTP 401; an authenticated non-admin returns HTTP 403.
Store ownership does not grant access.

## Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/admin/monitoring/summary` | Overall monitoring counts |
| GET | `/admin/monitors` | Paginated monitor list |
| POST | `/admin/monitors` | Create a monitor |
| PATCH | `/admin/monitors/{id}` | Update or pause/resume |
| POST | `/admin/monitors/{id}/checks` | Run a manual GET check |
| GET | `/admin/monitors/{id}/checks` | Paginated check history |
| GET | `/admin/incidents` | Paginated incident history |

All successful responses use the standard `success/message/data` envelope.
Collection endpoints accept `page` (default 1) and `per_page` (default 20,
maximum 100).

## Create monitor

`POST /admin/monitors`

```json
{
  "name": "RetailOS API",
  "target_url": "https://api.example.com/health",
  "interval_seconds": 600,
  "enabled": true
}
```

The URL must use HTTP or HTTPS, have a hostname, contain no credentials,
query string, or fragment, and be no longer than 2048 characters. Loopback,
private, link-local, reserved, localhost, and local hostnames are prohibited.
Intervals are 60 to 86400 seconds; the default is 600 seconds.

## Update / pause / resume

`PATCH /admin/monitors/{id}` accepts one or more of `name`,
`target_url`, `interval_seconds`, and `enabled`. Set `enabled` to
false to pause or true to resume. Unsupported fields are rejected.

## Manual checks and SSRF protections

Manual checks use GET only. The server does not accept caller-provided
methods, headers, or bodies. Redirects are not followed. DNS answers are
checked and every resolved address must be globally routable; the connection
is then pinned to one of those validated IP addresses to prevent a second DNS
lookup from redirecting the connection to a private address. TLS certificate
verification remains enabled. Connect/read timeouts are bounded and response
reads are capped at 64 KiB. Errors expose only safe error categories, not
internal IPs or exception messages.

HTTP responses with status 400 or above count as failures. A successful
response below 400 is considered up.

## Checks, incidents, and audit

Every manual check persists a check row and updates the monitor summary.
A failed check opens an incident if none is open, or increments the current
incident's failure count. A successful check resolves the open incident.
Monitor creation and configuration changes, and manual check requests, write
append-only platform audit events in the same transaction as the state update.

## Independent recurring scheduler boundary

The Flask process does not run a recurring scheduler. The `monitors`,
`monitor_checks`, and `monitor_incidents` tables are the persistence
contract for a separate scheduler worker. A worker should query enabled
monitors whose `next_check_at` is due, perform the same restricted GET
check, and persist its result/check/incident state transactionally. The
worker must have its own least-privilege database identity or a separate
trusted persistence adapter; it must not depend on an HTTP request to the
monitored RetailOS API to save a result. Do not expose the database URL or
scheduler credentials to the admin browser.

This repository change defines the durable database boundary only. It does
not deploy a Cloudflare Worker, provision database credentials, or claim that
the external scheduler is running. Those infrastructure steps must be
completed separately before recurring monitoring is operational.
