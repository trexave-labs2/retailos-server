# RetailOS Server

Flask API backend for RetailOS.

## Development

Set:

    export SECRET_KEY="your-secret"
    export FRONTEND_URL="http://localhost:3000"
    export SESSION_COOKIE_SECURE="false"
    export SESSION_COOKIE_SAMESITE="Lax"
    export CORS_ORIGINS="http://localhost:3000"

For PostgreSQL:

    export DATABASE_URL="postgresql+psycopg://username:password@localhost:5432/retailos"

Install:

    pip install -r server/requirements.txt

## Separate Client and platform-admin authentication

RetailOS Client and RetailOS Admin use independent credentials and sessions, even when both accounts use the same email address.

- Client: `POST /auth/login`, `GET /auth/me`, and `POST /auth/logout`. Uses `users.password_hash` and the standard Flask `session` cookie.
- Admin: `POST /admin/auth/login`, `GET /admin/auth/me`, `POST /admin/auth/logout`, and `POST /admin/auth/change-password`. Uses `users.admin_password_hash` and the independent HttpOnly `retailos_admin_session` cookie.
- Admin session revocation and password changes update `admin_session_version`; they do not invalidate the Client session. Client password changes update the Client session version and do not change the Admin password.
- The `025_admin_credentials` migration copies the former shared password hash for existing platform administrators so they can continue signing in. Change the Admin password in Admin Settings to make it different from the Client password.
- The bootstrap command uses `PLATFORM_ADMIN_PASSWORD` for the Admin credential. If the email already belongs to a retail user, its Client password is preserved. If the account is newly created for platform administration, a random unusable Client password is stored so the Admin password cannot sign in to Client.
- When a super administrator grants Admin access to an existing retail user, the initial Admin password is copied from that user's current Client password for compatibility. The new administrator should change it immediately in Admin Settings.

All `/admin/*` endpoints require the Admin cookie and a server-assigned platform role. A Client session alone is never accepted for platform administration.

## Production login rate limiting

Login requests use per-IP and per-IP/email limits. The default `memory://` storage is suitable only for local development and single-process testing; it is not shared between Gunicorn workers or Render instances.

Configure the Render service environment variable `RATELIMIT_STORAGE_URI` with the URL of a shared Redis instance, for example `redis://:<password>@<host>:<port>/0`. Use the Redis provider's actual TLS URL when required, keep credentials in Render environment settings, and never commit them to this repository. Verify that the configured Redis service is reachable from Render before deploying.

The API currently uses session-cookie authentication and does not validate `Authorization` or `X-CSRF-Token` headers. CORS therefore advertises only `Content-Type`; allowing a header is not a substitute for implementing and validating it.

## Testing

Run the full test suite:

    pytest server/tests

The test suite covers:

- Authentication and session handling
- Password reset token issuance, expiry, single-use enforcement, rate limiting and session invalidation
- Production configuration requirements
- Product creation, retrieval, updates and deletion
- Product validation and store authorization
- Stock adjustment and negative-stock protection
- Inventory movement history
- Sales and sale-item processing
- Transactional inventory updates and rollback
- Sale idempotency using client transaction IDs
- Receipt data and printable receipt text
- Customer creation and purchase history
- Customer validation and store authorization
- Low-stock alert generation, listing and resolution
- Alert store authorization
- Dashboard metrics, daily summaries and daily business brief
- Migration 003 upgrade/downgrade symmetry
- Password reset migration upgrade/downgrade symmetry
- Password length validation at the bcrypt byte limit
- Owner-authorized account export, export limits, secret exclusion and cross-store isolation
- Account/store deletion recovery, session invalidation, cross-account isolation, finalization, and rollback

## Subscription billing readiness

Paid Pro activation is not configured. `POST /subscriptions/change` creates a pending request only; it does not confirm payment or grant paid entitlements. The active subscription and any pending request are returned separately by `GET /subscriptions`. Client-supplied payment or plan-status fields are not trusted. See [docs/subscriptions.md](docs/subscriptions.md) for plan limits, request states, trial behavior, and the billing-provider requirements that must be completed before paid launch.

## Frontend API Guide

This section documents the V1 API for frontend development.

### Base URL

Local development:

    http://127.0.0.1:5000

All protected endpoints require the user to be logged in. The frontend should keep the authenticated session cookie returned by the login request.

### Checkout error handling

**POST** `/sales/checkout`

Checkout requires a stable, client-generated `client_transaction_id`. Reuse the same identifier and unchanged payload when retrying a request whose result is uncertain. A repeated successful request returns the original sale rather than deducting stock or recording payment twice.

Each cart line must include `expected_unit_price` to detect catalog price changes. The server compares it with the current catalog price. A mismatch returns `409 STALE_PRICE`, includes the current unit price for recovery, and makes no sale or inventory changes. Refresh the cart price and submit a new checkout attempt with a new transaction ID after the user confirms the changed price.

Checkout errors use the standard `success: false` / `error` structure with an error `code`, safe human-readable `message`, `fields`, and a `retryable` boolean. Validation, authorization, insufficient stock, stale price, and idempotency-key reuse are permanent for the submitted payload. Server failures and unexpected conflicts may be retried with the same transaction ID. Never create a new transaction ID just because a network response was lost.

The server rolls back the entire transaction on a failed checkout. Client error handling must not treat a queued request as a completed sale unless it receives a successful response.

### Standard response format

Successful responses generally look like:

    {
        "success": true,
        "message": "MESSAGE_CODE",
        "data": {}
    }

Error responses generally look like:

    {
        "success": false,
        "error": {
            "code": "ERROR_CODE",
            "message": "Human readable message",
            "details": {}
        }
    }

---

## 1. Authentication

### Register

**POST** `/auth/register`

Request:

    {
        "username": "John Doe",
        "email": "john@example.com",
        "password": "password123"
    }

Password validation requires at least 6 characters and no more than 72 UTF-8 bytes because bcrypt cannot safely process longer passwords.

Save the returned `store_id` for later requests.

### Login

**POST** `/auth/login`

Request:

    {
        "email": "john@example.com",
        "password": "password123"
    }

Login creates the authenticated session.

### Logout

**POST** `/auth/logout`

No request body is required.

### Request account deletion

**POST** `/auth/delete-account`

Requires an authenticated session, the current password, and the exact confirmation phrase:

```json
{
  "current_password": "your-current-password",
  "confirmation": "DELETE MY ACCOUNT"
}
```

The server immediately blocks normal account access, invalidates existing sessions, and marks every owned store as pending deletion. The response is HTTP 202 and includes the recovery deadline. The account and stores remain recoverable for 30 days.

### Cancel account deletion

**POST** `/auth/cancel-account-deletion`

This recovery endpoint does not require an existing session because the deletion request invalidates sessions. It requires the account email and current password:

```json
{
  "email": "owner@example.com",
  "password": "your-current-password"
}
```

Cancellation is accepted only before the 30-day deadline. It restores the account and stores to active status and increments the session version; the owner must log in again. Requests after the deadline return `410 RECOVERY_PERIOD_EXPIRED`.

The self-service flow is unavailable to platform administrator accounts.

### Request deletion of one store

**POST** `/settings/store/delete`

Requires an authenticated owner session, the current password, a positive `store_id` owned by that account, and the exact confirmation phrase `DELETE STORE`. The selected store becomes inaccessible immediately; the account and other owned stores remain active.

### Cancel one store deletion

**POST** `/settings/store/cancel-deletion`

Requires the active owner session, the store ID, and the current password. It restores only that store and is accepted only before its 30-day deadline. A store whose recovery period has expired cannot be restored.

### Scheduled deletion finalization

The recovery period is enforced by the backend finalizer, not by a database TTL. Configure a daily deployment scheduler to run:

```bash
flask --app server.wsgi:application account-deletion finalize-due
```

The command finalizes due account-level and store-level requests, removes customer contact details, deletes categories/products without historical references, and anonymizes catalog labels where transaction, inventory, or audit references require the row to remain. It preserves sales, sale items, payments, refunds, inventory movements, and immutable audit rows. The store and user database rows remain as anonymized tombstones because audit and financial relationships may reference them.

The repository cannot control database-provider backups, hosting logs, SMTP logs, or other provider-held copies. Their retention must be configured and verified separately.

### Password reset

**POST** `/auth/password-reset/request` accepts an email address and always returns the same generic `202 PASSWORD_RESET_REQUEST_ACCEPTED` response for valid email syntax, whether or not the account exists. Requests are limited to 5 per client IP per hour. If the account exists, the server emails a one-time reset link.

**POST** `/auth/password-reset/complete` accepts `token` and `new_password`. Tokens expire after 30 minutes by default, are single-use, and only their SHA-256 digest is stored. Successful completion increments the user's session version, revoking all existing sessions. Invalid, expired, and reused tokens share the same error response.

See [docs/PASSWORD_RESET_API.md](docs/PASSWORD_RESET_API.md) for the full request/response contract and required SMTP configuration. Configure `PASSWORD_RESET_SMTP_HOST`, `PASSWORD_RESET_FROM_EMAIL`, and any required SMTP credentials in the deployment environment. Never commit them.

---

## Store settings

### Update store name

**PATCH** `/settings/store`

Requires an authenticated store owner. Request fields: `store_id` and `name`. The server trims and validates the name, persists it, records a safe audit entry, and returns the updated store information. The current V1 data model has no staff-membership or staff-role system, so this operation is owner-only; staff access must not be inferred from a client-supplied role. PIN-based mobile access is not implemented and must remain a separate authentication mechanism from account passwords if introduced later.

## 2. Products

### Create product

**POST** `/product/create`

    {
        "store_id": 1,
        "name": "Samsung Galaxy A15",
        "product_type": "Physical",
        "base_unit": "piece",
        "price": 250000,
        "opening_stock": 10,
        "low_stock_threshold": 3
    }

Product types are Physical and Service. Existing products default to Physical. `base_unit` identifies the unit in which inventory is stored and changed (for example, `piece`, `kg`, or a store-defined unit such as `feed sack`). Standard and custom unit names must start with a letter and contain only letters, numbers, spaces, underscores, or hyphens, up to 20 characters. The base unit is always configured with a conversion quantity of 1.

Stock quantities, low-stock thresholds and inventory movement quantities are always expressed in the product's base unit. Product-specific selling and receiving units are configured in `units`, each with a positive integer `base_quantity` that explicitly states how many base units one selected unit represents. For example, a particular feed product may configure one `feed sack` as 25 kg. RetailOS never assumes that a bag, carton, or pack has a universal conversion. Use the correct conversion for each product. The selected unit name and conversion are snapshotted in sale and receiving history so later unit edits do not rewrite historical records.

Existing products are migrated with `piece`, and the product-unit migration seeds a matching unit with conversion quantity 1 without changing existing stock quantities. Once stock or inventory history exists, changing a product's base unit is rejected because it would reinterpret saved quantities. Service products can be sold without stock deduction, so their stock quantity is ignored during sale processing.

### Import products and services from CSV

**POST** `/product/import` (authenticated multipart form upload)

Send `store_id` and a CSV file in the multipart fields `store_id` and `file`. Files must be UTF-8 CSV, no larger than 2 MiB, with at most 1,000 data rows.

Supported columns:

- `name` (required): non-empty product or service name.
- `price` (required): non-negative amount with at most two decimal places.
- `product_type` (optional): `Physical` or `Service`; defaults to `Physical`.
- `category_id` (optional): category ID belonging to the same store and matching the row's item type.
- `base_unit` (optional): base inventory/selling unit; defaults to `piece`.
- `opening_stock` (optional): non-negative integer; defaults to zero and must be zero for services.
- `low_stock_threshold` (optional): positive integer for physical products only.

Unknown or duplicate columns, missing required columns, malformed CSV, invalid encoding, unsupported file types, and oversized files are rejected before processing. Invalid data rows are reported with their CSV row number and reason while valid rows are imported. The response includes `created_count`, `failed_count`, created record details, and row errors. Each valid row is committed independently, so a bad row does not roll back other successful rows. Services are saved with zero stock and no low-stock threshold.

### Get one product

**GET** `/product/get?id=1&store_id=1`

No request body.

### List products

**GET** `/product/list?store_id=1`

No request body.

### Update product

**PATCH** `/product/update`

    {
        "id": 1,
        "store_id": 1,
        "name": "Samsung Galaxy A15 5G",
        "price": 275000,
        "stock_quantity": 15,
        "base_unit": "piece",
        "low_stock_threshold": 5
    }

Partial update example:

    {
        "id": 1,
        "store_id": 1,
        "price": 280000
    }

### Delete product

**DELETE** `/product/delete`

    {
        "id": 1,
        "store_id": 1
    }

### Adjust stock

**POST** `/product/adjust`

Add stock:

    {
        "id": 1,
        "store_id": 1,
        "quantity_change": 10,
        "reason": "New stock received"
    }

Remove stock:

    {
        "id": 1,
        "store_id": 1,
        "quantity_change": -3,
        "reason": "Damaged products"
    }

### Get inventory movements

**GET** `/product/movements?store_id=1`

Optional product filter:

**GET** `/product/movements?store_id=1&product_id=1`

### Receive stock

**POST** `/product/receivings`

    {
        "store_id": 1,
        "product_id": 1,
        "unit_id": 2,
        "quantity": 10,
        "unit_cost": "5000.00",
        "total_cost": "50000.00",
        "supplier_or_source": "Market supplier",
        "client_transaction_id": "RECEIVE-0001",
        "receiving_date": "2026-10-20T09:30:00+01:00"
    }

The server records stock receipts separately from the product's selling price. It calculates total acquisition cost as quantity multiplied by unit cost and rejects a supplied total that does not match. Quantity is expressed in the selected configured unit; inventory increases by quantity multiplied by that unit's base quantity. For example, 10 bags with a conversion of 10 pieces per bag increase base-unit stock by 100 pieces. Unit name and conversion are snapshotted in the receipt history.

A unique `client_transaction_id` is required. Reusing it with the same receiving data returns the original receipt without increasing stock again; reusing it for different data returns a conflict. Supplier/source and receiving date are optional. Dates are stored as UTC; future receiving dates are rejected.

### List stock receiving history

**GET** `/product/receivings?store_id=1`

Optional product filter:

**GET** `/product/receivings?store_id=1&product_id=1`

Returns the saved receiving records, including quantity, selected unit, conversion snapshot, acquisition unit cost, total cost, supplier/source, receiving date, and idempotency key.

---

## Product and service categories

Categories are store-scoped and separated by item type: `Physical` categories apply only to physical products, while `Service` categories apply only to services. Names are trimmed and unique without regard to case within a store and item type. Existing items remain valid with no category until assigned.

- **GET** `/categories?store_id=1` lists categories. Optional `item_type=Physical` or `item_type=Service` filters by type.
- **POST** `/categories` creates a category with `store_id`, `name`, and `item_type`.
- **PATCH** `/categories` renames a category using `store_id`, `id`, and `name`. The category ID stays stable, so assigned items retain their category.
- **DELETE** `/categories` deletes an unused category using `store_id` and `id`. Deletion is rejected with `409 CATEGORY_IN_USE` while items are assigned.
- Product creation and updates accept `category_id`; send `null` on update to remove an assignment. Category ownership and item type are validated.
- **GET** `/product/list?store_id=1&category_id=2` returns only items assigned to that category.

## 3. Customers

### Create customer

**POST** `/customers`

    {
        "store_id": 1,
        "name": "John Customer",
        "contact": "08012345678"
    }

### List customers

**GET** `/customers?store_id=1`

Optional name/contact search:

**GET** `/customers?store_id=1&search=john`

The search is case-insensitive and matches customer name or contact.

### Update customer

**PATCH** `/customers`

    {
        "id": 1,
        "store_id": 1,
        "name": "John Updated",
        "contact": "08111111111"
    }

The update is partial. Send at least one of `name` or `contact`.

### Delete customer

**DELETE** `/customers`

    {
        "id": 1,
        "store_id": 1
    }

A customer with no linked sales can be deleted. If any sale is linked to the customer, deletion is rejected with `409 CUSTOMER_HAS_SALES` so purchase history is not destroyed. RetailOS V1 does not currently model an outstanding customer balance separately, so there is no balance check yet.

### Who owes me

**GET** `/customers/owes?store_id=1`

Returns only customers whose outstanding balance is above zero, plus the store's total outstanding amount.

### Customer purchase history

**GET** `/customers/history?id=1&store_id=1`

---

## 4. Sales

### Offline sale synchronization

**POST** `/sales/sync` accepts a locally queued sale after the client reconnects. It uses the same authenticated, store-authorized, transactional sale workflow as `POST /sales`; the server recalculates totals from current catalog prices, validates configured units and current stock, and writes sale, payment, inventory movement, and audit records in the same database transaction. Client-supplied totals and stock values are not trusted.

The client must send a stable, unique `client_transaction_id` for each queued sale. Keep the same identifier and same sale payload for every retry. IDs must contain 1 to 128 characters. The server returns:
- HTTP 201 with `data.sync_status = "accepted"` when the server creates the sale.
- HTTP 200 with `data.sync_status = "already_processed"` when the same sale identifier and payload are submitted again. The existing receipt is returned and stock is not deducted again.
- HTTP 409 with `IDEMPOTENCY_KEY_REUSED` and `error.fields.sync_status = "rejected"` when the identifier is reused for a different sale.
- A validation, authorization, stock, or other error with `error.fields.sync_status = "rejected"` for a rejected synchronization. A rejected sale is not a confirmed server sale.

Only treat a locally queued sale as confirmed after receiving `accepted` or `already_processed`. If the network fails before a response arrives, retry with the same identifier and unchanged payload. A key reuse conflict requires client reconciliation; do not generate a new key just to bypass it. Stock and prices are checked when the server receives the queued sale, not when it was created offline, so the server can reject a sale that is no longer valid.

Example request fields: `store_id`, `client_transaction_id`, `payment_method`, `items` (product ID, quantity, and optional configured unit ID), and optional `customer_id` / `amount_paid`. The endpoint requires an authenticated session and store access.


### Create sale

**POST** `/sales`

Basic sale:

    {
        "store_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 2
            }
        ]
    }

Sale for a customer:

    {
        "store_id": 1,
        "customer_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 2
            }
        ]
    }

Recommended sale request:

    {
        "store_id": 1,
        "customer_id": 1,
        "client_transaction_id": "SALE-0001",
        "items": [
            {
                "product_id": 1,
                "quantity": 2
            }
        ]
    }

The `client_transaction_id` helps prevent duplicate sales when a frontend request is retried.
The backend calculates the sale total. Do not send a total amount. Sale `quantity` values are always quantities in the product base unit; the backend does not store cartons, boxes or other pack quantities as stock. `amount_paid` is optional; when omitted, it defaults to the full sale total. If provided, it cannot exceed the total. The sale stores both `amount_paid` and `balance`. Sale quantities are always expressed in the product base unit.

A sale with a balance above zero must include a valid `customer_id`. The customer's `outstanding_balance` is increased by the sale balance as part of the same transaction.

Each sale line stores its actual unit price. For service products, the frontend may send an optional `unit_price` at checkout to override the catalog price for that sale only. If omitted, the product price is used. Unit-price overrides are rejected for physical products.

For service products, the sale quantity is still recorded and priced normally, but stock is not checked, deducted, or recorded as an inventory movement.

### List, filter, and export sales

**GET** /sales?store_id=1

The sales endpoint supports combined filters. Every filter is optional. Custom date ranges (`start_date` / `end_date`) and grouped payment-method reporting (`group_by=payment_method`) require an active Pro entitlement; ordinary sales history and standalone payment-method/category filters remain available on Free:

- start_date=2026-10-01 includes sales from the start of that Africa/Lagos calendar date.
- end_date=2026-10-22 includes sales through the end of that Africa/Lagos calendar date.
- category_id=2 includes a sale when at least one item on that sale belongs to the selected store category.
- payment_method=Cash filters by payment method. Supported values remain Cash, Transfer, and POS.

Example combined query:

**GET** /sales?store_id=1&start_date=2026-10-01&end_date=2026-10-22&category_id=2&payment_method=Transfer

Dates must use YYYY-MM-DD; a start date after the end date is rejected. Date boundaries are interpreted in Africa/Lagos and converted to UTC for querying.

A sale containing items from multiple categories appears once if any line matches the selected category. Its transaction count and totals represent the full sale, not just the matching lines. This keeps transaction totals consistent with the transaction screen. The response includes a summary with transaction_count, total_sales, total_amount_paid, and total_balance.

Each sale includes transaction identifiers, customer details, total/paid/balance amounts, payment method, timestamp, and item-level export fields: product ID/name, category ID/name, quantity, unit, unit conversion, sale price, and line total. The list endpoint returns the same filtered records and fields for the transaction screen and for client-generated CSV or PDF exports; it does not generate a PDF file itself.

For reporting, sales can be grouped by payment method:

**GET** /sales?store_id=1&group_by=payment_method

The grouped response contains sales_by_payment_method with the sales count and total sales for each payment method present in the filtered result. Date, category, and payment-method filters can be combined with this grouping.

### Record an additional payment

**POST** `/sales/payments`

    {
        "store_id": 1,
        "sale_id": 1,
        "amount": "500.00",
        "payment_method": "Transfer",
        "client_transaction_id": "PAYMENT-0001"
    }

The `client_transaction_id` is required for additional payments. Generate a unique value for each intended payment and reuse the exact same value when retrying that request. The server records the payment once; a retry with the same key, sale, amount, and payment method returns the existing result without changing balances again. Reusing a key for a different payment returns `409 IDEMPOTENCY_KEY_REUSED`. Keys must contain 1 to 128 characters.

Payment amounts must be finite, greater than zero, have no more than two decimal places, fit the supported monetary range, and not exceed the outstanding balance. Supported payment methods are `Cash`, `Transfer`, and `POS`. The payment record, sale totals, and customer outstanding balance are updated in one transaction.

### Retrieve sale payment history

**GET** `/sales/payments?store_id=1&sale_id=1`

Returns the sale's current `total_amount`, `amount_paid`, and `balance`, plus each recorded payment ordered from oldest to newest. Each payment includes its ID, amount, payment method, and timestamp. The customer purchase-history response also includes a `payments` array for every sale.

### Get one sale

**GET** `/sales/get?id=1&store_id=1`

### Generate receipt

**POST** `/sales/receipt`

Request:

    {
        "store_id": 1,
        "id": 1
    }

The response contains the sale ID, customer ID, total amount, amount paid, balance, date, receipt items and `printable_text`.

---

## 5. Low-stock alerts

V1 currently supports low-stock alerts only.

### Generate low-stock alerts

**POST** `/alerts/generate-low-stock?store_id=1`

No request body is required.

Example response when no new alerts are created:

    {
        "success": true,
        "message": "LOW_STOCK_ALERTS_GENERATED",
        "data": {
            "created_alerts": []
        }
    }

### List alerts

**GET** `/alerts?store_id=1`

### Resolve alert

**POST** `/alerts/resolve?store_id=1&id=1`

No request body is required.

---

## 6. Dashboard

### Dashboard metrics

**GET** `/dashboard?store_id=1`

Returns product count, low-stock count, open alerts, today's sales count and today's sales total.

### Daily business brief

**GET** `/dashboard/daily-brief?store_id=1`

Returns today's sales compared with yesterday, sales status, open alerts, restock recommendations and the highest-selling product.

### Daily summary

**GET** `/dashboard/daily-summary?store_id=1`

Returns the date, sales count, total sales and the day's sales list.

### Product and service rankings

**GET** `/dashboard/rankings?store_id=1`

Optional period filters must be supplied together:

**GET** `/dashboard/rankings?store_id=1&start_date=2026-10-01&end_date=2026-10-22`

Both dates use `YYYY-MM-DD` and Africa/Lagos calendar-day boundaries. With no date filters, the endpoint ranks the current Africa/Lagos day. A start date after the end date is rejected. The response contains separate `top_selling_products` and `top_services` lists, each limited to ten results and sorted by its ranking metric, then by name for stable ties.

- Product rankings include physical products only and rank net base units sold. Recorded returned quantities are subtracted using the return quantity and unit-conversion snapshot.
- Service rankings include service products only and count distinct persisted sales containing each service. A sale counts once per service even if it has multiple lines for that service.
- A partial refund does not erase a service order. A fully refunded, fully paid sale is excluded from service order counts.
- A financial refund without recorded returned quantities does not reduce physical units sold. Product rankings measure units retained as sold, not refund money.
- Empty periods return empty arrays, not null values.
- V1 does not have an explicit cancellation or void status. The endpoint cannot identify a persisted sale as cancelled or voided, so void-aware rankings require a future sale-status feature.

---

## Frontend request flow

1. Register or log in.
2. Save the returned `store_id`.
3. Load products.
4. Load customers.
5. Create sales.
6. Refresh inventory after a sale.
7. Generate the receipt with `POST /sales/receipt`.
8. Load alerts.
9. Load dashboard data.
10. Log out.

### Important frontend notes

- Use the `store_id` returned by authentication.
- Do not calculate or send sale totals.
- `amount_paid` is optional for a fully paid sale; send it when recording a partial payment.
- A sale with `amount_paid < total_amount` must include `customer_id`.
- `amount_paid` cannot exceed the server-calculated sale total.
- Use a unique `client_transaction_id` for each sale when possible.
- Send `Content-Type: application/json` for JSON request bodies.
- Protected endpoints require the authenticated session.
- Receipt generation is **POST**, not GET.
- Receipt generation expects `store_id` and sale `id` in the JSON body.
- Alert generation is **POST** and takes `store_id` in the query string.
- GET requests do not require JSON request bodies.

## API endpoint summary

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/auth/register` | Register user and create store |
| POST | `/auth/login` | Log in and create session |
| POST | `/auth/logout` | Log out |
| POST | `/auth/change-password` | Change password and invalidate sessions |
| POST | `/auth/password-reset/request` | Request a one-time password reset email |
| POST | `/auth/password-reset/complete` | Complete password reset and revoke sessions |
| PATCH | `/settings/store` | Update the authenticated owner’s store name |
| POST | `/product/create` | Create product |
| GET | `/product/get?id=1&store_id=1` | Get one product |
| GET | `/product/list?store_id=1` | List products |
| PATCH | `/product/update` | Update product |
| DELETE | `/product/delete` | Delete product |
| POST | `/product/adjust` | Adjust stock in the product base unit |
| GET/POST/PATCH/DELETE | `/categories` | List, create, rename, or delete item categories |
| POST | `/product/receivings` | Record received stock and acquisition cost |
| GET | `/product/receivings?store_id=1` | Retrieve stock receiving history |
| GET | `/product/movements?store_id=1` | List inventory movements |
| GET | `/product/movements?store_id=1&product_id=1` | Product movement history |
| POST | `/customers` | Create customer |
| GET | `/customers?store_id=1` | List customers |
| GET | `/customers?store_id=1&search=john` | Search customers by name or contact |
| PATCH | `/customers` | Update customer |
| DELETE | `/customers` | Delete customer when no sales are linked |
| GET | `/customers/owes?store_id=1` | Customers with outstanding balances |
| GET | `/customers/history?id=1&store_id=1` | Customer purchase history |
| POST | `/sales` | Create sale |
| POST | `/sales/payments` | Record an additional payment against an existing sale |
| GET | `/sales/payments?store_id=1&sale_id=1` | Retrieve payment history and current sale balance |
| GET | `/sales?store_id=1` | List sales |
| GET | `/sales?store_id=1&payment_method=Cash` | Filter sales by payment method |
| GET | `/sales?store_id=1&group_by=payment_method` | Group sales for payment-method reporting |
| GET | `/sales/get?id=1&store_id=1` | Get one sale |
| POST | `/sales/receipt` | Generate receipt |
| GET | `/alerts?store_id=1` | List alerts |
| POST | `/alerts/generate-low-stock?store_id=1` | Generate low-stock alerts |
| POST | `/alerts/resolve?store_id=1&id=1` | Resolve alert |
| DELETE | `/alerts/delete?store_id=1&id=1` | Delete alert |
| GET | `/dashboard?store_id=1` | Dashboard metrics |
| GET | `/dashboard/daily-summary?store_id=1` | Daily sales summary |
| GET | `/dashboard/daily-brief?store_id=1` | Daily business brief |
| GET | `/dashboard/rankings?store_id=1` | Top physical products and services for a selected Africa/Lagos date range |
| GET | `/health` | Lightweight server health/readiness check |

---
## Health check

`GET /health` is public and does not require authentication or a database query. A successful `200` response means the Flask application has finished startup and is ready to accept normal API requests. A frontend or deployment monitor can poll this endpoint while waiting for the server to wake up.

## Database migrations

Initialize or upgrade the database:

    flask --app server.app db upgrade

Create a new migration after model changes:

    flask --app server.app db migrate -m "describe the change"

The initial schema migration is stored in migrations/versions/001_initial_retailos.py.

The V1 alert scope migration is stored in migrations/versions/002_v1_alert_scope.py.

The product type migration is stored in migrations/versions/004_product_type.py.

The sale payment and customer balance migration is stored in migrations/versions/006_customer_sale_balances.py.

For a fresh PostgreSQL database, set DATABASE_URL before running the migration.

## API areas

The V1 backend currently exposes these main areas:

- /auth — registration, login and logout
- /product — product and inventory management
- /product/movements — inventory movement history
- /customers — customer management and purchase history
- /sales — sales creation, listing, retrieval and receipts
- /alerts — low-stock alerts and alert resolution
- /dashboard — dashboard metrics and daily business summary

Protected store-level endpoints require an authenticated session and verify that the user has access to the requested store.

## V1 alert scope

V1 currently implements low-stock alerts.

Sales-drop and no-sales alerts are not part of the current V1 behavior. They can be added later as separate features instead of exposing enum values for features that are not implemented.

## Receipts

The receipt endpoint returns structured receipt data and printable receipt text.

It does not claim to generate a PDF. A PDF or printer-specific format can be added later without changing the sale transaction logic.

## Production

Run the application with Gunicorn:

    FRONTEND_URL="https://your-client.example"
    SESSION_COOKIE_SECURE="true"
    SESSION_COOKIE_SAMESITE="None"
    SESSION_COOKIE_PARTITIONED="true"
    gunicorn "server.wsgi:application"

The frontend uses credentialed cross-origin requests and the Flask HttpOnly session cookie. CORS is controlled by the comma-separated CORS_ORIGINS allowlist. Do not use a wildcard origin with credentialed requests. For production, serve both applications over HTTPS and keep SECRET_KEY private.

The current API contract covers authentication, stores, products, inventory adjustments, sales, customers, alerts and dashboard insights.


## Refund processing and refund receipts

Refunds are recorded separately from the original sale. The original sale and payment history are preserved. Both partial and full refunds are supported, and refund amounts cannot exceed the sale's remaining refundable paid amount. A refund is classified as `Full` only when cumulative refunds equal the original sale total and the sale was fully paid; otherwise it is `Partial`.

- **POST** `/sales/refunds` records a refund. Required fields: `store_id`, `sale_id`, `amount`, `reason`, `payment_method`, and a unique `client_transaction_id`. Refund payment methods are `Cash`, `Transfer`, and `POS`.
- **GET** `/sales/refunds?store_id=1` lists refunds for the authorized store. Add `sale_id=1` to limit results to one original sale.
- **GET** `/sales/refunds/receipt?store_id=1&refund_id=1` returns refund receipt data, including the unique reference, original sale, amount, reason, refund method, timestamp, and printable text.

Each refund receives a server-generated unique reference. Repeating a request with the same idempotency key and same data returns the existing refund; reusing the key for different data is rejected. The original sale is locked while eligibility and remaining refundable amount are checked.

Returned goods are restored to inventory only when the request explicitly includes `returned_items`, each containing a `sale_item_id` and a positive returned quantity. The item must belong to the original sale, must be a physical product, and cumulative returned quantities cannot exceed the quantity sold. Stock changes and inventory movement records are committed atomically with the refund. Refunds without returned-item entries do not restore stock.

Sales-list summaries retain gross totals and add `total_refunds`, `net_sales`, and `net_amount_paid`. Per-sale export data includes `refunded_amount` and `net_amount`. Dashboard sales totals are net of refunds processed during the selected Africa/Lagos business day. Refunds do not rewrite the original sale amount or payment history.


## Audit history

Authenticated store owners can query audit records with `GET /audit?store_id=1`. Optional filters are `action_type`, `entity_type`, `entity_id`, `user_id`, `start_date`, `end_date`, and `limit` (1–500). Date filters accept ISO-8601 timestamps; date-only values cover the full UTC calendar date. Results are newest first.

The server records product price changes, completed sales, recorded sale payments, processed refunds, stock adjustments, and stock receiving. Each record includes the store, actor when available, timestamp, action, affected entity, and safe before/after values or details. Audit rows are added to the same database transaction as the business operation, so a rollback also rolls back the audit entry.

Audit records have no API update or delete operations, and ORM-level update/delete attempts are rejected. Audit payloads exclude password and credential-like fields. Do not use audit details to store secrets, full request bodies, session data, or other unnecessary personal information. Settings changes should be added when the application introduces persistent settings that can be changed.


## Feedback submission

**POST** `/feedback` is a public, server-controlled feedback endpoint. It accepts a JSON object containing a required unique `submission_id`, `category` (`bug`, `suggestion`, `question`, or `other`), and a 10–5000 character `message`. Optional `contact_name` and `contact_email` are validated server-side. Generate one stable submission ID per user submission and reuse it for retries; do not generate a new ID just because a response was lost.

The endpoint is limited to **5 submissions per client IP per hour**. Production rate limits use the shared Redis store configured by `RATELIMIT_STORAGE_URI`. The database keeps a structured feedback record with category, message, optional contact details, submission ID, delivery status, timestamps, and a safe delivery error type for future administrative review.

Delivery is synchronous through the configured SMTP server. A successful response (`202 FEEDBACK_ACCEPTED`) is returned only after the SMTP server accepts the message and the record is marked as sent. If configuration is missing or delivery fails, the endpoint returns `503 FEEDBACK_DELIVERY_FAILED`; the response does not expose SMTP credentials or internal error details. A previously accepted submission ID returns the existing accepted result without sending another email. Reusing an ID for different feedback returns `409 DUPLICATE_SUBMISSION`.

Configure these environment variables in the deployment dashboard, never in source control:

- `FEEDBACK_SMTP_HOST`: SMTP server hostname.
- `FEEDBACK_SMTP_PORT`: SMTP port, default 587.
- `FEEDBACK_SMTP_USE_TLS`: use STARTTLS, default true.
- `FEEDBACK_SMTP_USERNAME` and `FEEDBACK_SMTP_PASSWORD`: SMTP credentials, if required.
- `FEEDBACK_SMTP_TIMEOUT_SECONDS`: connection timeout, default 10 seconds.
- `FEEDBACK_FROM_EMAIL`: sender address approved by the SMTP provider.
- `FEEDBACK_TO_EMAIL`: company inbox that receives feedback.

A mail provider accepting the message means it has accepted responsibility for delivery, not that a person has read it. Keep the destination mailbox monitored and configure SPF/DKIM/DMARC with the email provider as appropriate.


## Platform administration

Platform-level read APIs are documented in [docs/PLATFORM_ADMIN_API.md](docs/PLATFORM_ADMIN_API.md). They provide the admin overview, paginated user and store lists, and individual user/store details. All routes require the server-provisioned platform-admin role and return aggregate store activity only; they do not expose store operational records.

- `GET /admin/overview`
- `GET /admin/users` and `GET /admin/users/{id}`
- `GET /admin/stores` and `GET /admin/stores/{id}`

The frontend must use server-side pagination and filters. Expired subscription periods are excluded from active-premium metrics.


### Feedback support and platform audit administration

Platform administrators can use the feedback and platform audit endpoints
documented in `docs/PLATFORM_ADMIN_API.md`:

- `GET /admin/feedback` and `GET /admin/feedback/{id}`
- `PATCH /admin/feedback/{id}/status`
- `GET /admin/audit-logs`

Feedback email-delivery status is separate from its support workflow status.
Administrative status changes are recorded in the append-only platform audit
history in the same transaction as the feedback update.


## Store account data export

Subscription-gated reporting and export endpoints, effective Pro/trial rules, and the standard denial response are documented in [Pro entitlement enforcement](docs/PRO_ENTITLEMENTS.md).

**GET** `/settings/export?store_id=1`

Requires a valid authenticated session and verifies that the signed-in user owns the requested store. The endpoint returns a downloadable JSON attachment named `retailos-store-<store-id>-export-<date>.json`. The frontend must send the authenticated session cookie and the owner's store ID.

The version 1 export includes:
- Store profile and available store settings, plus the owner's public name and email.
- Product and service records, categories, and configured product units.
- Customers and their outstanding balances.
- Sales, sale line items, payment history, refunds, and refund line items.
- Inventory movements and stock receiving history.
- Store alerts, including their resolution/read/dismissal state.

Records are store-scoped on the server. Related records such as sale items and payments are selected through their parent sale's store ID; product units are selected through their parent product. Monetary values are represented as decimal strings and timestamps as ISO 8601 strings so clients do not lose decimal precision or timestamp information.

The export has a hard limit of **25,000 records across all exported collections**. If the store exceeds that limit, the endpoint returns HTTP 413 with the standard API error envelope and `EXPORT_TOO_LARGE`; no partial export is returned. Contact support for an assisted export above the limit.

The export intentionally excludes password hashes, session identifiers and versions, password-reset tokens, secrets, platform-admin records, and internal authentication data. Responses use `Cache-Control: no-store` and are marked as attachments. The endpoint does not accept a store ID from a request body or trust client-supplied ownership claims.


### Platform monitoring management

The platform-admin monitoring control-plane API is documented in [docs/PLATFORM_MONITORING_API.md](docs/PLATFORM_MONITORING_API.md). It provides monitor configuration, manual checks, check history, incidents, and a persistent database contract for an independently operated scheduler. The Flask API does not run a recurring scheduler; external scheduler deployment and least-privilege database access are separate operational requirements.
