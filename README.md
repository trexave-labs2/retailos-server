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

## Testing

Run the full test suite:

    pytest server/tests

The test suite covers:

- Authentication and session handling
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
- Password length validation at the bcrypt byte limit

## Frontend API Guide

This section documents the V1 API for frontend development.

### Base URL

Local development:

    http://127.0.0.1:5000

All protected endpoints require the user to be logged in. The frontend should keep the authenticated session cookie returned by the login request.

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

---

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

Product types are Physical and Service. Existing products default to Physical. `base_unit` identifies the smallest unit in which inventory is stored and changed (for example, `piece`, `gram`, or `milliliter`). Stock quantities, sale quantities, thresholds and inventory movement quantities are always expressed in that base unit. Existing products are migrated with `piece`. Service products can be sold without stock deduction, so their stock quantity is ignored during sale processing.

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

---

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

### List sales

**GET** `/sales?store_id=1`

Optional payment-method filter:

**GET** `/sales?store_id=1&payment_method=Cash`

Supported payment methods are `Cash`, `Transfer`, and `POS`.

For reporting, sales can be grouped by payment method:

**GET** `/sales?store_id=1&group_by=payment_method`

The grouped response contains `sales_by_payment_method` with the sales count and total sales for each payment method present in the result.

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
| POST | `/product/create` | Create product |
| GET | `/product/get?id=1&store_id=1` | Get one product |
| GET | `/product/list?store_id=1` | List products |
| PATCH | `/product/update` | Update product |
| DELETE | `/product/delete` | Delete product |
| POST | `/product/adjust` | Adjust stock in the product base unit |
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
