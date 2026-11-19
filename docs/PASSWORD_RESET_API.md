# Password reset API

Password recovery uses a one-time email link. The API never confirms whether an
email address is registered.

## Request a reset

**POST** `/auth/password-reset/request`

Request:

    {
        "email": "owner@example.com"
    }

The endpoint is limited to **5 requests per client IP per hour**.

Every syntactically valid email receives the same HTTP 202 response, whether
or not an account exists:

    {
        "success": true,
        "message": "PASSWORD_RESET_REQUEST_ACCEPTED",
        "data": {
            "message": "If an account exists for that email, password reset instructions will be sent."
        }
    }

If the account exists, the server emails a link to
`{FRONTEND_URL}/reset-password?token=...`. The token expires after 30 minutes
by default. Requesting another link invalidates any previously issued,
unused reset token for that account. SMTP delivery failure does not change the
public response; the undelivered token is revoked and only the exception type
is logged.

## Complete a reset

**POST** `/auth/password-reset/complete`

Request:

    {
        "token": "token-from-the-email-link",
        "new_password": "a-new-password"
    }

The new password uses the same validation rules as registration and password
change: at least 6 characters and no more than 72 UTF-8 bytes.

A successful reset returns HTTP 200 with
`PASSWORD_RESET_SUCCESSFUL_LOGIN_AGAIN`. The token is consumed in the same
database transaction as the password update. The user's `session_version`
is incremented, invalidating all existing sessions. The browser must sign in
again with the new password.

Invalid, expired, and previously used tokens all return the same HTTP 400
error code: `PASSWORD_RESET_INVALID_OR_EXPIRED`. The endpoint is limited to
10 attempts per client IP per hour. Invalid password input returns the
standard `VALIDATION_ERROR` response without consuming a valid token.

## Email configuration

Configure these environment variables on the backend host. Never commit
credentials to source control.

- `PASSWORD_RESET_SMTP_HOST`: SMTP hostname.
- `PASSWORD_RESET_SMTP_PORT`: SMTP port, default 587.
- `PASSWORD_RESET_SMTP_USE_TLS`: enable STARTTLS, default true.
- `PASSWORD_RESET_SMTP_USERNAME`: SMTP username, if required.
- `PASSWORD_RESET_SMTP_PASSWORD`: SMTP password, if required.
- `PASSWORD_RESET_SMTP_TIMEOUT_SECONDS`: SMTP timeout, default 10 seconds.
- `PASSWORD_RESET_FROM_EMAIL`: verified sender address.
- `PASSWORD_RESET_TOKEN_TTL_MINUTES`: token lifetime, default 30 minutes.
- `FRONTEND_URL`: frontend origin used to build the reset link.

If SMTP is not configured or delivery fails, reset requests still return the
generic 202 response. Configure SMTP before enabling password recovery in a
production frontend.

## Storage and security

The database stores only a SHA-256 digest of the cryptographically random
token, never the raw token. Tokens have an expiry and a consumed timestamp.
The reset-token table is separate from user/session data and is removed by
the reversible migration downgrade. Tokens are not written to application
logs or API responses.
