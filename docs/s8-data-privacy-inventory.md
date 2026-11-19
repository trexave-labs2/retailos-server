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

The account/store closure workflow defines a 30-day recovery period before application-level anonymization and deletion of eligible catalog data. The repository does not define a general retention schedule for feedback, audit history, provider logs, or database backups.

The account/store finalization command is scheduled externally and is not an in-process scheduler. Feedback records marked sent or failed remain in the database unless removed through database administration or other infrastructure-level action. Audit records are explicitly protected against normal ORM deletion and have no retention job in this repository.

Flask's default signed-cookie sessions are not stored in a server-side session table. Session invalidation is supported through logout, password changes, and the session-version check in authentication middleware; cookie lifetime behavior should be confirmed against the deployed Flask configuration and browser behavior.

Database backups, hosting logs, SMTP provider logs, and Redis persistence/expiry are controlled by the deployed providers and settings. This repository does not establish their retention periods or deletion behavior.

## Deletion and account closure behavior

- An authenticated owner can request closure of one owned store through `POST /settings/store/delete`; only that store becomes inaccessible. The owner can cancel through `POST /settings/store/cancel-deletion` before the 30-day deadline.
- An authenticated owner can request account deletion through `POST /auth/delete-account`. The request requires the current password and the exact confirmation phrase `DELETE MY ACCOUNT`.
- The server immediately changes the account to `pending_deletion`, increments the session version, clears the current session, and marks every owned active store as `pending_deletion`. Normal login and protected API access are blocked while deletion is pending.
- The owner can cancel during the 30-day recovery period through `POST /auth/cancel-account-deletion`, using the account email and current password. Cancellation restores the account and stores and requires a fresh login. Platform administrator accounts cannot use this self-service flow.
- A scheduled backend command, `flask --app server.wsgi:application account-deletion finalize-due`, finalizes due account-level and store-level requests after their recovery deadlines. Production must configure a daily scheduler for this command; the application does not create its own scheduler.
- Finalization anonymizes the account name/email/password credentials and store name. Customer names and contact values are anonymized while outstanding balances and transaction links remain.
- Categories and products without transaction, inventory, or audit references are deleted. Referenced categories are retained with anonymized labels. Products that are still referenced are retained as anonymized catalog placeholders so financial and inventory history remains joinable.
- Sales, sale items, payments, refunds, stock receiving records, inventory movements, and audit history are retained. Store and user rows remain as anonymized tombstones where foreign-key relationships require them; this is not a physical deletion of every database row.
- Audit logs are immutable and may contain historical values captured before closure. They are retained to preserve the audit trail and are not rewritten by the finalizer.
- Password-reset tokens for the account are removed during finalization. Feedback submissions are separate records and are not automatically associated with or deleted by account closure.
- Database backups, hosting logs, SMTP provider logs, and other infrastructure-held copies are outside this application workflow. Their retention and expiration must be verified and configured with the relevant providers.

## Privacy-page wording guardrails

The Privacy page can accurately say, after confirming the deployed configuration, that RetailOS processes account details, store/inventory/sales records, customer details entered by the store, feedback submissions, and security/operational data needed to run and protect the service.

It should explain that feedback may be emailed to the configured support recipient and that database, hosting, email, and rate-limiting infrastructure may process data as service providers. Name specific vendors, countries, or transfer destinations only after checking the production deployment and provider agreements.

Do not claim any of the following based only on this repository:

- That the 30-day application recovery period also governs provider-managed backups, hosting logs, SMTP logs, or other infrastructure copies.
- That every row or every historical value is physically erased. Immutable audit JSON and referenced catalog rows can remain.
- A particular production database, hosting, SMTP, or Redis vendor or data-residency location.
- That no technical logs, backups, or third-party processing exist.
- That all customer information entered by a store is anonymous or never shared outside the store's browser.

## Follow-up work needed for stronger privacy claims

1. Confirm the deployed frontend's analytics, cookies, error reporting, and third-party scripts.
2. Record the actual production hosting, database, SMTP, and Redis providers, regions, backup settings, and relevant subprocessors.
3. Decide and implement retention periods for feedback, audit history, operational logs, and backups, including a documented exception process where required.
4. Configure and monitor a daily production schedule for `account-deletion finalize-due`; the command is not self-scheduling.
5. Confirm and document provider backup, hosting-log, SMTP-log, and Redis retention independently of application data finalization.
6. Add frontend confirmation and recovery messaging only after the API is deployed and tested.
