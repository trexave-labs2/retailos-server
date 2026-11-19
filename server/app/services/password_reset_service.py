import hashlib
import secrets
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from urllib.parse import quote

from flask import current_app, request, session
from sqlalchemy import update

from server.app.extensions import db
from server.app.models.password_reset_token import PasswordResetToken
from server.app.models.user import User
from server.app.utils.response import Response
from server.app.utils.security import hash_password
from server.app.utils.time import now_utc
from server.app.utils.validators import validate_email, validate_password


GENERIC_REQUEST_RESPONSE = {
    "success": True,
    "message": "PASSWORD_RESET_REQUEST_ACCEPTED",
    "data": {
        "message": "If an account exists for that email, password reset instructions will be sent."
    },
}


class PasswordResetService:
    TOKEN_TTL_MINUTES = 30

    @staticmethod
    def _token_digest(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def request_reset(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR", "A JSON object is required", {"payload": "Invalid JSON object"}
            ), 400

        email = data.get("email")
        if validate_email(email):
            return Response.error_response(
                "VALIDATION_ERROR", "A valid email address is required", {"email": "Invalid email format"}
            ), 400

        # Keep the public response identical for known and unknown addresses.
        user = User.query.filter_by(email=email.strip().lower()).first()
        if user is None:
            return GENERIC_REQUEST_RESPONSE, 202

        raw_token = secrets.token_urlsafe(32)
        now = now_utc().replace(tzinfo=None)
        ttl_minutes = int(current_app.config.get("PASSWORD_RESET_TOKEN_TTL_MINUTES", self.TOKEN_TTL_MINUTES))

        # Only one outstanding reset token is useful per account. Mark older
        # tokens consumed before persisting the replacement.
        PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update(
            {"used_at": now}, synchronize_session=False
        )
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=self._token_digest(raw_token),
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        db.session.add(reset_token)
        db.session.commit()

        try:
            self._deliver_reset_email(user.email, raw_token)
        except Exception as exc:
            # Do not expose delivery state or account existence in the public
            # response. Revoke the undelivered token and log only the exception
            # type, never the address, URL, or raw token.
            reset_token.used_at = now_utc().replace(tzinfo=None)
            db.session.commit()
            current_app.logger.warning(
                "Password reset email delivery failed (error_type=%s)",
                type(exc).__name__,
            )

        return GENERIC_REQUEST_RESPONSE, 202

    def complete_reset(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR", "A JSON object is required", {"payload": "Invalid JSON object"}
            ), 400

        token = data.get("token")
        new_password = data.get("new_password")
        fields = {}
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            fields["token"] = "A valid reset token is required"
        password_error = validate_password(new_password)
        if password_error:
            fields["new_password"] = password_error
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        now = now_utc().replace(tzinfo=None)
        digest = self._token_digest(token)

        # Conditional update makes consumption single-use even when two reset
        # requests race. Password update and token consumption commit together.
        consumed = db.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.token_hash == digest,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
            .values(used_at=now)
        )
        if consumed.rowcount != 1:
            db.session.rollback()
            return self._invalid_token_response()

        reset_record = PasswordResetToken.query.filter_by(token_hash=digest).first()
        user = db.session.get(User, reset_record.user_id) if reset_record else None
        if user is None:
            db.session.rollback()
            return self._invalid_token_response()

        user.password_hash = hash_password(new_password)
        user.session_version += 1
        db.session.commit()
        session.clear()
        return Response.success_response(
            {}, "PASSWORD_RESET_SUCCESSFUL_LOGIN_AGAIN"
        ), 200

    @staticmethod
    def _invalid_token_response():
        return Response.error_response(
            "PASSWORD_RESET_INVALID_OR_EXPIRED",
            "This password reset link is invalid or has expired. Request a new one.",
            {"retryable": False},
        ), 400

    @staticmethod
    def _deliver_reset_email(recipient, raw_token):
        host = current_app.config.get("PASSWORD_RESET_SMTP_HOST", "").strip()
        sender = current_app.config.get("PASSWORD_RESET_FROM_EMAIL", "").strip()
        username = current_app.config.get("PASSWORD_RESET_SMTP_USERNAME", "")
        password = current_app.config.get("PASSWORD_RESET_SMTP_PASSWORD", "")
        if not host or not sender:
            raise RuntimeError("Password reset email delivery is not configured")

        port = int(current_app.config.get("PASSWORD_RESET_SMTP_PORT", 587))
        timeout = float(current_app.config.get("PASSWORD_RESET_SMTP_TIMEOUT_SECONDS", 10))
        use_tls = current_app.config.get("PASSWORD_RESET_SMTP_USE_TLS", True)
        frontend_url = current_app.config.get("FRONTEND_URL", "").rstrip("/")
        if not frontend_url:
            raise RuntimeError("Password reset frontend URL is not configured")

        reset_url = f"{frontend_url}/reset-password?token={quote(raw_token, safe='')}"
        message = EmailMessage()
        message["Subject"] = "Reset your RetailOS password"
        message["From"] = sender
        message["To"] = recipient
        message.set_content(
            "We received a request to reset your RetailOS password.\n\n"
            f"Use this link within {current_app.config.get('PASSWORD_RESET_TOKEN_TTL_MINUTES', 30)} minutes:\n"
            f"{reset_url}\n\n"
            "If you did not request this change, you can ignore this email. "
            "Your password will not change unless the link is used."
        )

        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
