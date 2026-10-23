import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr

from flask import current_app, request
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.models.feedback_submission import FeedbackSubmission
from server.app.utils.response import Response
from server.app.utils.time import now_utc


class FeedbackService:
    CATEGORIES = {"bug", "suggestion", "question", "other"}
    MAX_MESSAGE_LENGTH = 5000

    def submit(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "A JSON object is required", {}), 400

        submission_id = data.get("submission_id")
        category = data.get("category")
        message = data.get("message")
        contact_name = data.get("contact_name")
        contact_email = data.get("contact_email")

        if not isinstance(submission_id, str) or not submission_id.strip() or len(submission_id.strip()) > 100:
            return Response.error_response("VALIDATION_ERROR", "submission_id must contain 1 to 100 characters", {"submission_id": "invalid"}), 400
        submission_id = submission_id.strip()

        if not isinstance(category, str) or category.strip().lower() not in self.CATEGORIES:
            return Response.error_response("VALIDATION_ERROR", "category must be bug, suggestion, question, or other", {"category": "invalid"}), 400
        category = category.strip().lower()

        if not isinstance(message, str) or not 10 <= len(message.strip()) <= self.MAX_MESSAGE_LENGTH:
            return Response.error_response("VALIDATION_ERROR", "message must contain 10 to 5000 characters", {"message": "invalid"}), 400
        message = message.strip()

        if contact_name is not None:
            if not isinstance(contact_name, str) or len(contact_name.strip()) > 100:
                return Response.error_response("VALIDATION_ERROR", "contact_name must be at most 100 characters", {"contact_name": "invalid"}), 400
            contact_name = contact_name.strip() or None

        if contact_email is not None:
            if not isinstance(contact_email, str) or len(contact_email.strip()) > 254:
                return Response.error_response("VALIDATION_ERROR", "contact_email must be a valid email address", {"contact_email": "invalid"}), 400
            contact_email = contact_email.strip().lower()
            parsed_name, parsed_address = parseaddr(contact_email)
            if not parsed_address or parsed_address != contact_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact_email):
                return Response.error_response("VALIDATION_ERROR", "contact_email must be a valid email address", {"contact_email": "invalid"}), 400

        existing = FeedbackSubmission.query.filter_by(submission_id=submission_id).first()
        if existing:
            # Idempotency only applies when the key is reused with the same payload.
            # Check payload equality before returning success for an already-sent record.
            if (
                existing.category != category
                or existing.message != message
                or existing.contact_name != contact_name
                or existing.contact_email != contact_email
            ):
                return Response.error_response(
                    "DUPLICATE_SUBMISSION",
                    "submission_id has already been used for different feedback",
                    {},
                ), 409
            if existing.status == "sent":
                return Response.success_response(
                    {"submission_id": existing.submission_id, "status": "accepted"},
                    "FEEDBACK_ACCEPTED",
                ), 200
            record = existing
        else:
            record = FeedbackSubmission(
                submission_id=submission_id,
                category=category,
                message=message,
                contact_name=contact_name,
                contact_email=contact_email,
                status="pending",
            )
            db.session.add(record)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                existing = FeedbackSubmission.query.filter_by(submission_id=submission_id).first()
                if existing and existing.status == "sent":
                    return Response.success_response({"submission_id": existing.submission_id, "status": "accepted"}, "FEEDBACK_ACCEPTED"), 200
                return Response.error_response("DUPLICATE_SUBMISSION", "This feedback submission is already being processed", {}), 409

        try:
            self._deliver(record)
            record.status = "sent"
            record.delivery_error = None
            record.delivered_at = now_utc()
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            failed = FeedbackSubmission.query.filter_by(submission_id=submission_id).first()
            if failed:
                failed.status = "failed"
                failed.delivery_error = type(exc).__name__[:80]
                db.session.commit()
            current_app.logger.warning("Feedback delivery failed (submission_id=%s, error_type=%s)", submission_id, type(exc).__name__)
            return Response.error_response(
                "FEEDBACK_DELIVERY_FAILED",
                "Feedback could not be submitted right now. Please try again later.",
                {"retryable": True},
            ), 503

        return Response.success_response(
            {"submission_id": submission_id, "status": "accepted"},
            "FEEDBACK_ACCEPTED",
        ), 202

    @staticmethod
    def _deliver(record):
        host = os.environ.get("FEEDBACK_SMTP_HOST", "").strip()
        recipient = os.environ.get("FEEDBACK_TO_EMAIL", "").strip()
        sender = os.environ.get("FEEDBACK_FROM_EMAIL", "").strip()
        username = os.environ.get("FEEDBACK_SMTP_USERNAME", "")
        password = os.environ.get("FEEDBACK_SMTP_PASSWORD", "")
        if not host or not recipient or not sender:
            raise RuntimeError("Feedback email delivery is not configured")

        try:
            port = int(os.environ.get("FEEDBACK_SMTP_PORT", "587"))
            timeout = float(os.environ.get("FEEDBACK_SMTP_TIMEOUT_SECONDS", "10"))
        except ValueError as exc:
            raise RuntimeError("Invalid feedback SMTP configuration") from exc

        email = EmailMessage()
        email["Subject"] = f"RetailOS feedback: {record.category}"
        email["From"] = sender
        email["To"] = recipient
        if record.contact_email:
            email["Reply-To"] = record.contact_email
        email.set_content(
            "A new RetailOS feedback submission was received.\n\n"
            f"Submission ID: {record.submission_id}\n"
            f"Category: {record.category}\n"
            f"Contact name: {record.contact_name or 'Not provided'}\n"
            f"Contact email: {record.contact_email or 'Not provided'}\n\n"
            f"Message:\n{record.message}\n"
        )

        use_tls = os.environ.get("FEEDBACK_SMTP_USE_TLS", "true").strip().lower() == "true"
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(email)
