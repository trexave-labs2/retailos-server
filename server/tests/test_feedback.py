from server.app.models.feedback_submission import FeedbackSubmission
from server.app.services.feedback_service import FeedbackService


def payload(**overrides):
    data = {
        "submission_id": "feedback-test-001",
        "category": "suggestion",
        "message": "Please make the daily sales report easier to read.",
        "contact_name": "Test User",
        "contact_email": "test@example.com",
    }
    data.update(overrides)
    return data


def test_valid_feedback_is_accepted_only_after_delivery(client, monkeypatch):
    delivered = []
    monkeypatch.setattr(FeedbackService, "_deliver", staticmethod(lambda record: delivered.append(record.submission_id)))

    response = client.post("/feedback", json=payload())

    assert response.status_code == 202
    assert response.json["success"] is True
    assert response.json["data"]["status"] == "accepted"
    assert delivered == ["feedback-test-001"]
    record = FeedbackSubmission.query.filter_by(submission_id="feedback-test-001").one()
    assert record.status == "sent"
    assert record.delivered_at is not None
    assert record.category == "suggestion"
    assert record.message == "Please make the daily sales report easier to read."


def test_failed_delivery_is_not_reported_as_success(client, monkeypatch):
    def fail_delivery(record):
        raise OSError("private SMTP detail must not reach the client")

    monkeypatch.setattr(FeedbackService, "_deliver", staticmethod(fail_delivery))
    response = client.post("/feedback", json=payload())

    assert response.status_code == 503
    assert response.json["success"] is False
    assert response.json["error"]["code"] == "FEEDBACK_DELIVERY_FAILED"
    assert "private SMTP detail" not in response.get_data(as_text=True)
    record = FeedbackSubmission.query.filter_by(submission_id="feedback-test-001").one()
    assert record.status == "failed"
    assert record.delivery_error == "OSError"


def test_same_submission_id_does_not_send_duplicate_email(client, monkeypatch):
    delivered = []
    monkeypatch.setattr(FeedbackService, "_deliver", staticmethod(lambda record: delivered.append(record.submission_id)))

    first = client.post("/feedback", json=payload())
    second = client.post("/feedback", json=payload())

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json["success"] is True
    assert delivered == ["feedback-test-001"]
    assert FeedbackSubmission.query.filter_by(submission_id="feedback-test-001").count() == 1


def test_reusing_submission_id_for_different_feedback_is_rejected(client, monkeypatch):
    monkeypatch.setattr(FeedbackService, "_deliver", staticmethod(lambda record: None))
    assert client.post("/feedback", json=payload()).status_code == 202

    response = client.post("/feedback", json=payload(message="A different message that is valid."))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "DUPLICATE_SUBMISSION"


def test_invalid_category_and_contact_email_are_rejected(client):
    bad_category = client.post("/feedback", json=payload(category="urgent"))
    bad_email = client.post("/feedback", json=payload(contact_email="not-an-email"))

    assert bad_category.status_code == 400
    assert bad_category.json["error"]["code"] == "VALIDATION_ERROR"
    assert bad_email.status_code == 400
    assert bad_email.json["error"]["code"] == "VALIDATION_ERROR"
    assert FeedbackSubmission.query.count() == 0
