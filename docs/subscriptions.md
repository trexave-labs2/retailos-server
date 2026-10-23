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
