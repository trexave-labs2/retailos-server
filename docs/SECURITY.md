# Security

## SECRET_KEY rotation

A compromised SECRET_KEY must be treated as invalid.

The application no longer contains the old hard-coded secret. Production now requires SECRET_KEY to be supplied through the environment.

Generate a new secret:

    python -c "import secrets; print(secrets.token_hex(32))"

Then replace the production SECRET_KEY in the deployment environment.

Do not place the new value in:

- Git
- README files
- .env.example
- source code
- issue comments
- pull request comments

Restart the production application after changing the secret.

## Git history

Removing a secret from the latest source file does not remove it from old Git commits.

Because the old secret was previously committed, it must not be trusted again even if it is no longer present in the current source.

If repository history must also be cleaned, use a controlled Git history rewrite and coordinate it with every clone and deployment.

## Database

Production must provide DATABASE_URL.

The application now refuses to start with ProductionConfig when DATABASE_URL is missing.

Use PostgreSQL for production.

## Cookies

Production should use:

    SESSION_COOKIE_SECURE=True

when the application is served over HTTPS.

## Platform administrator provisioning

Platform-administrator access is separate from store ownership. It is stored in the server-managed users.platform_role column and is never accepted from registration or other client request data.

The first platform administrator must first create a normal RetailOS account. Provisioning then requires all of the following:

1. Set PLATFORM_ADMIN_BOOTSTRAP_EMAIL to that account's email.
2. Generate a one-time bootstrap secret with at least 32 characters and set it as PLATFORM_ADMIN_BOOTSTRAP_TOKEN in the server environment.
3. Deploy the migration before running the command.
4. Run flask --app server.app platform-admin bootstrap admin@example.com from a trusted operator environment. The command prompts for the token without echoing it.
5. Remove both bootstrap environment variables from the deployment environment after successful provisioning.

The command only provisions the configured email, refuses to run after a bootstrap audit event exists, and writes the initial event to the append-only platform audit log. Do not put the token in shell arguments, source control, issue comments, or pull-request comments.

Protected platform routes must use require_platform_admin. It first validates the existing server session and then reads the platform role from the database. Store owners receive 403; unauthenticated requests receive 401.
