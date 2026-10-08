# RetailOS — Database Design (V1)

## Overview

The V1 database stores the core records required to operate a retail store.

The design prioritizes:

- data integrity
- store isolation
- traceable inventory changes
- reliable sales records
- simple queries for dashboard and customer history

The current backend uses SQLAlchemy models, SQLite for tests/local development, and PostgreSQL for production.

## 1. Users

Stores authenticated user accounts.

Core fields:

- id
- username
- email
- password_hash
- created_at

Rules:

- email is unique
- passwords are stored as hashes
- users authenticate through server-side sessions

## 2. Stores

Represents a retail business owned by a user.

Core fields:

- id
- user_id
- name
- created_at

Relationship:

- one user can own one or more stores
- each store has an owner

## 3. Products

Stores products and current inventory state.

Core fields:

- id
- store_id
- name
- base_unit
- price
- stock_quantity
- low_stock_threshold
- created_at
- updated_at

Rules:

- product belongs to one store
- price cannot be negative
- stock quantity cannot be negative
- all stock quantities are stored in the product base unit
- the default base unit is `piece`
- low-stock threshold is expressed in the same base unit

## 4. Customers

Stores customer information for a store.

Core fields:

- id
- store_id
- name
- contact
- created_at

Relationship:

- one store has many customers
- a customer can be associated with many sales

## 5. Sales

Stores the transaction header.

Core fields:

- id
- store_id
- customer_id, nullable
- client_transaction_id, nullable and unique
- total_amount
- created_at

Rules:

- sale belongs to one store
- customer may be absent
- client transaction ID supports idempotent submissions
- total amount is calculated by the backend

## 6. Sale Items

Stores the products included in a sale.

Core fields:

- id
- sale_id
- product_id
- quantity
- price
- total

Relationship:

- one sale has many sale items
- one product can appear in many sale items

The sale item stores the sale-time price so historical transactions remain correct if the product's current price changes.

## 7. Inventory Movements

Provides a history of stock changes.

Core fields:

- id
- store_id
- product_id
- user_id
- movement_type
- quantity_change
- previous_quantity
- new_quantity
- reason
- created_at

V1 usage:

- manual stock adjustments
- sale-driven stock reductions

This makes important stock changes traceable.

## 8. Alerts

Stores operational alerts.

Core fields:

- id
- store_id
- product_id, nullable
- type
- message
- is_resolved
- created_at

Current alert types:

- low_stock
- sales_drop
- no_sales

The current V1 alert workflow actively generates low-stock alerts.

## 9. Relationships

- Users → Stores: 1:N
- Stores → Products: 1:N
- Stores → Customers: 1:N
- Stores → Sales: 1:N
- Customers → Sales: 1:N
- Sales → Sale Items: 1:N
- Products → Sale Items: 1:N
- Stores → Inventory Movements: 1:N
- Products → Inventory Movements: 1:N
- Users → Inventory Movements: 1:N
- Stores → Alerts: 1:N
- Products → Alerts: 1:N

## 10. Core Transaction: Sale

Creating a sale is treated as one business transaction.

The backend:

1. validates the sale
2. verifies store access
3. checks customer/store consistency
4. locks or reads the required product rows
5. checks available stock
6. creates the sale
7. creates sale items
8. reduces inventory
9. creates inventory movement records
10. commits

If an error occurs, the transaction is rolled back.

## 11. Inventory Adjustment

For a manual adjustment:

1. load the product
2. calculate the new quantity
3. reject a negative result
4. store the previous quantity
5. update the product quantity
6. create an inventory movement
7. commit

## 12. Derived Data

The following are calculated from stored records:

- today's sales count
- today's sales total
- daily sales summary
- low-stock count
- open-alert count
- customer purchase history

These values do not require separate summary tables in V1.

## 13. Data Integrity Rules

- store-owned resources must remain store-scoped
- product stock cannot be negative
- product price cannot be negative
- sales totals are server-calculated
- sale inventory changes are transactional
- duplicate client transaction IDs do not create duplicate sales
- inventory movements preserve previous and resulting quantities
- inventory quantities and sale quantities use the product base unit

## 14. Future Extensions

Possible later tables include:

- payments
- expenses
- suppliers
- purchase orders
- audit logs
- subscriptions
- store memberships and roles

These are intentionally outside the current V1 schema.
