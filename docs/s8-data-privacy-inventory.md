# S8: Actual Data Collection, Storage, Processing, Retention, and Deletion

## Purpose and scope

This document records behavior verified in the RetailOS server repository. It is an engineering inventory to help the Privacy page describe the product accurately; it is not legal advice and is not a substitute for reviewing the deployed frontend, hosting account, database provider, email provider, and operational procedures.

The repository does not establish every detail of the production environment. Where a provider, retention period, or operational practice is not configured or evidenced here, this document says so instead of inventing a promise.

## Data the server collects

### Account and authentication data

- Registration accepts a username, email address, and password.
- The email address is normalized to lowercase and stored with a uniqueness constraint.
- The password is stored as a password hash, not as the original password.
- The account record includes an internal user ID, display name, and creation timestamp.
- Login creates a Flask session containing the user ID and session version. The configured Flask session mechanism uses a signed browser cookie; this repository does not configure a database-backed server-side session store.
- Password changes replace the stored password hash, increment the session version, and clear the current session.

### Store and retail-operation data

The database models and API support store names and subscription state, products and categories, product units and stock levels, inventory movements, stock receiving records, sales and sale lines, payment records, refunds and refunded items, customer records, and low-stock alerts.

Customer records may include a name, contact value, outstanding balance, and creation timestamp. The model does not restrict the contact field to a particular kind of contact information.

Sales and related records may include product and quantity details, prices and totals, payment method (Cash, Transfer, or POS), amounts paid and outstanding balances, timestamps, customer association, and client-generated transaction identifiers. Refund records include amount, reason, payment method, reference, transaction identifier, and timestamp. Stock receiving records can include supplier/source text and cost information.

The application is designed for store-level access control, but the Privacy page should not imply that all retail data is anonymous: it can include information about identifiable customers when the store operator enters it.

### Audit history

The server records selected business changes in audit logs. Entries can include the store and acting user IDs, action and entity types, entity ID, timestamp, and JSON before/after values or details. The audit service filters keys that appear to contain passwords, tokens, secrets, or credentials, but other business values may be recorded. Audit records cannot be updated or deleted through the model's normal ORM events; the store foreign key also restricts deletion while audit rows remain.

### Feedback submissions

The unauthenticated feedback endpoint accepts a submission ID, category, message, and optional contact name and email. Categories are bug, suggestion, question, or other. Messages may contain up to 5,000 characters. The server persists submissions, their status, delivery error type, creation time, and delivery timestamp. The endpoint has a rate limit of five submissions per hour per remote address.

Feedback is sent through SMTP using environment-configured settings. The email includes the submission ID, category, contact name and email, and full message. If a contact email is provided, it is used as Reply-To. The SMTP host and recipient are configuration-dependent; the repository does not identify a specific email vendor. A failed delivery leaves a failed submission record in the database.

### Technical and security-related data

- Login endpoints are rate-limited using the request's remote address and, for one limit, the default limiter key.
- Production configuration requires a shared Redis URL for rate-limit storage. The Redis provider is not specified in the source.
- The application logs a one-time proxy diagnostic containing the request remote address when the process receives its first request.
- Unhandled exceptions are logged with exception details by Flask's application logger. Feedback delivery failures log the submission ID and exception type.
- The repository does not show a dedicated product analytics, advertising, or telemetry SDK in the server dependencies. This alone does not establish whether the deployed frontend, hosting platform, or other infrastructure collects additional data.

## Where data is stored and processed

- Application records are stored in the SQL database selected by `DATABASE_URL`. Production startup requires this setting; PostgreSQL is supported and the default development configuration uses SQLite. The repository does not identify the actual production database vendor, its region, backups, or access policies.
- Account session state is carried in a Flask-signed browser cookie by the default session implementation. Production configuration requires secure cookies and sets SameSite=None and Partitioned. The cookie is HttpOnly.
- Rate-limit counters use the configured Redis service in production. The service and its region are deployment decisions, not defined in this repository.
- Feedback content is processed by the server and transmitted to the configured SMTP recipient through the configured SMTP service.
- The frontend origin is controlled by configuration. The backend's CORS allowlist is not itself a list of data processors.
- Hosting and database infrastructure may create their own operational logs or backups. Their exact contents, locations, subprocessors, and retention periods cannot be confirmed from this repository.

## Retention behavior

The repository defines timestamps for account, store, retail, audit, feedback, and subscription records, but does not define a general data-retention schedule.

No scheduled cleanup or automatic expiration process for business records or feedback submissions was found in the server source tree. Feedback records marked sent or failed remain in the database unless removed through database administration or other infrastructure-level action. Audit records are explicitly protected against normal ORM deletion and have no retention job in this repository.

Flask's default signed-cookie sessions are not stored in a server-side session table. Session invalidation is supported through logout, password changes, and the session-version check in authentication middleware; cookie lifetime behavior should be confirmed against the deployed Flask configuration and browser behavior.

Database backups, hosting logs, SMTP provider logs, and Redis persistence/expiry are controlled by the deployed providers and settings. This repository does not establish their retention periods or deletion behavior.

## Deletion and account closure behavior

- Product and customer records have individual delete API operations, subject to service-level validation, authorization, and database constraints.
- The repository exposes no public account-deletion endpoint and no public store-deletion endpoint.
- The fact that some foreign keys specify cascading deletion does not mean users can request account deletion through the product. Other relationships can restrict deletion, including audit history and sale/refund relationships.
- Audit entries cannot be deleted through the model's normal ORM events.
- There is no documented user self-service workflow in this backend for deleting all account, store, transaction, feedback, audit, backup, or provider-held data.
- Logging out clears the browser session; it does not delete the account or store data.
- Deleting a customer or product is not equivalent to deleting all records that may relate to prior transactions.

Until an account deletion and retention process is implemented and verified, the public Privacy page must not promise that users can delete their entire account or that all personal data is automatically erased after a stated period.

## Privacy-page wording guardrails

The Privacy page can accurately say, after confirming the deployed configuration, that RetailOS processes account details, store/inventory/sales records, customer details entered by the store, feedback submissions, and security/operational data needed to run and protect the service.

It should explain that feedback may be emailed to the configured support recipient and that database, hosting, email, and rate-limiting infrastructure may process data as service providers. Name specific vendors, countries, or transfer destinations only after checking the production deployment and provider agreements.

Do not currently claim any of the following based only on this repository:

- A fixed retention period or automatic deletion deadline.
- A complete account deletion or data-erasure feature.
- A particular production database, hosting, SMTP, or Redis vendor or data-residency location.
- That no technical logs, backups, or third-party processing exist.
- That all customer information entered by a store is anonymous or never shared outside the store's browser.

## Follow-up work needed for stronger privacy claims

1. Confirm the deployed frontend's analytics, cookies, error reporting, and third-party scripts.
2. Record the actual production hosting, database, SMTP, and Redis providers, regions, backup settings, and relevant subprocessors.
3. Decide and implement retention periods for feedback, audit history, operational logs, and backups, including a documented exception process where required.
4. Design an authenticated account/store closure and data export/deletion process that preserves required financial/audit integrity and handles provider backups.
5. Add tests and operational verification for any new retention or deletion behavior before changing the public Privacy page to promise it.
