# Pro entitlement enforcement

RetailOS treats the server-side subscription service and `PLAN_CATALOG` as the authority for feature access. Frontend visibility is not an authorization control.

## Protected endpoints

| Method and endpoint | Pro capability | Free-plan response |
| --- | --- | --- |
| `GET /dashboard/rankings` | Advanced reporting | `403 FEATURE_NOT_AVAILABLE` |
| `GET /dashboard/time-series` | Advanced reporting | `403 FEATURE_NOT_AVAILABLE` |
| `GET /sales` with `start_date` or `end_date` | Custom-date reporting | `403 FEATURE_NOT_AVAILABLE` |
| `GET /sales` with `group_by=payment_method` | Grouped sales reporting | `403 FEATURE_NOT_AVAILABLE` |
| `GET /settings/export` | Store account data export | `403 FEATURE_NOT_AVAILABLE` |

The ordinary dashboard, daily summary, daily brief, and unfiltered sales history remain available under their existing access rules. Payment-method and category filters on sales history do not by themselves enable grouped or custom-date reporting.

## Entitlement rules

- Free stores do not receive advanced reporting or account export.
- A Pro trial grants these capabilities only while its recorded trial period is active.
- A paid Pro subscription grants these capabilities only while its status is `active` and its subscription period has not expired.
- Expired or incomplete Pro periods are downgraded to the Free plan by the authoritative subscription resolver.
- Pending plan-change requests are records of intent, not proof of payment and not an entitlement. They never activate Pro.
- Unknown, cancelled, expired, or otherwise inactive Pro statuses do not grant Pro features, even if a stale store plan field still says `pro`.
- Every protected endpoint first verifies the authenticated user's access to the requested store. A feature entitlement does not bypass store isolation.
- The client must handle the standard error envelope and must not treat hidden UI controls as security.

## Error contract

A store without the required capability receives HTTP `403` with error code `FEATURE_NOT_AVAILABLE`. The response includes the feature identifier, effective plan, and whether an upgrade is available. It does not expose subscription secrets or internal billing details.

## Verification coverage

Backend regression tests cover Free, active trial, expired trial, active paid Pro, pending plan changes, unauthenticated requests, and cross-store isolation for the relevant endpoints. Run the full suite with:

```bash
pytest server/tests
```

Paid plan activation remains unavailable until a verified billing integration changes the store's subscription state. A client-submitted plan-change request must never be used as activation evidence.
