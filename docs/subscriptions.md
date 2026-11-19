# TX RetailOS subscription plans

## Authoritative plan catalog

`PLAN_CATALOG` and `BILLING_INTERVALS` in `server/app/services/subscription_service.py` are the single server-side source for plan entitlements, prices, and limits. Client requests cannot set prices or activate paid subscriptions.

| Feature | Free | Pro |
|---|---:|---:|
| Price | ₦0 | ₦4,999/month |
| Products | 100 | Unlimited |
| Customers | 100 | Unlimited |
| Sales per month | 500 | Unlimited |
| Inventory, checkout and dashboard | Included | Included |
| Advanced reporting | Not included | Included |
| Excel/CSV export entitlement | Not included | Included |
| Staff accounts | Owner only | Up to 5 staff |
| Support | Standard | Priority WhatsApp |
| Pro trial | One 14-day trial per store | Not applicable |

### Billing options

- Monthly: ₦4,999
- Quarterly: ₦13,500
- Yearly: ₦49,990

Billing options are saved on a plan-change request with the selected interval and a price snapshot. The backend does not yet collect payment or renew a subscription. A pending request is not a successful upgrade.

### Reporting scope

The Free dashboard includes today's and this month's sales and current stock levels.

Pro reporting is intended to include profit per product, top and slow sellers, sales over a custom date range, low-stock alerts, and staff sales performance. The plan catalog describes these entitlements. The staff-account system and all corresponding Pro feature gates must be implemented before those features are marketed as available to paying customers. Existing V1 endpoints must not be treated as safely gated merely because the catalog says a feature is Pro-only.

## Subscription request and activation states

A plan-change request is not a subscription activation. The current API has no configured payment provider, so paid Pro cannot be activated through `POST /subscriptions/change`.

- A successful Pro change request is stored with `status: pending` and returns HTTP `202`, `activation: not_activated`, and `billing.status: not_configured`.
- The response's `current_subscription` reports the plan and status that are active now. It remains Free unless the store has a valid Pro trial or a separately verified activation.
- `GET /subscriptions?store_id=...` reports the active plan in `subscription.plan` and a separate `subscription.pending_change`. The pending request includes its requested interval, price snapshot, payment requirement, and `activation: not_activated`.
- Repeating the same plan and billing interval returns the existing pending request; it does not create another request or activate the plan.
- A request for a different target plan or billing interval is rejected while another change is pending. Cancel the pending request before submitting a different one.
- Client-supplied payment flags, prices, plan fields, or subscription statuses are ignored. Only server-controlled trial logic or a future verified billing workflow may change entitlements.

A future provider integration is separate work. It must verify webhook signatures, authenticate payment events, process provider event IDs idempotently, validate the store and expected amount/currency/interval against the server-side request, and update the subscription only after confirmed payment. It must also define renewal, expiry, refund, chargeback, failed-payment, retry, and cancellation transitions. A browser redirect or client success callback is not proof of payment.

## Trial

- `POST /subscriptions/trial` starts a 14-day Pro trial for an authenticated store owner.
- A store can start the trial only once. The server persists the trial-used timestamp.
- Trial expiry is checked server-side when subscription state or plan limits are evaluated. Expiry returns the store to the Free plan; data is not deleted.
- A trial does not require payment-provider confirmation. A paid Pro plan does.

## Endpoints

- `GET /subscriptions/plans`: public plan catalog with prices, billing options, limits, feature metadata, reporting scope, support tier, and trial terms.
- `GET /subscriptions?store_id=1`: authenticated current plan, status, billing period end, usage, 80% warning thresholds, trial-used state, and pending plan change.
- `POST /subscriptions/trial`: authenticated one-time 14-day Pro trial. JSON field: `store_id`.
- `POST /subscriptions/change`: authenticated plan-change request. JSON fields: `store_id`, `plan` (`free` or `pro`), and optional `billing_interval` (`month`, `quarter`, or `year`).
- `POST /subscriptions/change/cancel`: authenticated cancellation of a pending request. JSON fields: `store_id` and `change_id`.

## Limit behaviour

- Usage is returned by the subscription endpoint. It warns when product, customer, or monthly sales usage reaches 80% of the Free limit: 80 products, 80 customers, or 400 sales.
- Product and customer creation are blocked server-side at their Free limits.
- Checkout receives a 24-hour grace period after the 500-sales monthly cap is first encountered, so reaching the cap does not immediately interrupt sales. After the grace period, further sales are blocked until the next Africa/Lagos calendar month or a plan change.
- Sales usage is measured using Africa/Lagos calendar-month boundaries converted to UTC.
- Existing records are not deleted when a trial expires or a subscription changes. New product/customer creation remains subject to Free limits. A complete read-only policy for existing records beyond Free limits still needs explicit route-by-route enforcement before production launch.
- “Unlimited” is subject to fair-use review at exceptionally high volumes.

## Security and remaining launch work

The server currently enforces the Free product, customer, and monthly sales limits, and does not let a client request directly activate paid Pro. Trial state is stored server-side and can be used only once per store.

Before the paid plan is launch-ready, integrate a billing provider with server-verified payment confirmation, signature-verified webhooks, idempotent event processing, renewals, refunds/failed-payment handling, and consistent pending/successful/failed transitions. Do not accept a client-supplied payment-success flag or directly set a store to Pro.

Additional work before claiming every listed Pro feature is available:
- Enforce the advanced-reporting entitlement on relevant reporting endpoints.
- Implement dedicated, server-enforced export authorization.
- Implement staff accounts and the five-staff Pro limit, including role and store authorization.
- Implement the full read-only policy for data beyond Free limits after Pro ends.
- Configure and operationally staff the priority WhatsApp support channel.

These gaps are intentionally documented rather than pretending a metadata flag implements a feature.
