"""Integration tests for webhook security (secret token validation).

Verifies that both ``/webhook`` and ``/feedback_webhook`` endpoints correctly
reject requests that lack or carry an incorrect
``X-Telegram-Bot-Api-Secret-Token`` header, and accept requests with the
correct secret.
"""

from __future__ import annotations

import httpx
import pytest


# ---------------------------------------------------------------------------
# Fixture: async HTTP client wired to the FastAPI app
# ---------------------------------------------------------------------------

@pytest.fixture()
async def client(tmp_db, monkeypatch):
    """Create an ``httpx.AsyncClient`` connected to the webhook FastAPI app.

    Monkeypatches the webhook secrets on both the ``config`` and ``webhook``
    modules so that the endpoint guards use known test values.

    Depends on ``tmp_db`` to ensure the database is initialised before the
    app processes any request.
    """
    import config
    import webhook

    # Set secrets on the config module (source of truth)
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "test-secret-123")
    monkeypatch.setattr(config, "FEEDBACK_WEBHOOK_SECRET", "feedback-secret-456")
    monkeypatch.setattr(config, "FEEDBACK_BOT_TOKEN", "fake-feedback-token")
    monkeypatch.setattr(config, "FEEDBACK_ADMIN_ID", 99999)

    # Patch the local references that webhook.py imported at module level
    monkeypatch.setattr(webhook, "WEBHOOK_SECRET", "test-secret-123")
    monkeypatch.setattr(webhook, "FEEDBACK_WEBHOOK_SECRET", "feedback-secret-456")
    monkeypatch.setattr(webhook, "FEEDBACK_BOT_TOKEN", "fake-feedback-token")
    monkeypatch.setattr(webhook, "FEEDBACK_ADMIN_ID", 99999)

    transport = httpx.ASGITransport(app=webhook.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# /webhook endpoint tests
# ---------------------------------------------------------------------------

class TestWebhookSecurity:
    """Tests for the main ``/webhook`` endpoint secret validation."""

    async def test_webhook_no_secret_returns_403(self, client: httpx.AsyncClient):
        """POST /webhook without the secret header must return 403."""
        response = await client.post("/webhook", json={"update_id": 1})
        assert response.status_code == 403

    async def test_webhook_wrong_secret_returns_403(self, client: httpx.AsyncClient):
        """POST /webhook with an incorrect secret must return 403."""
        response = await client.post(
            "/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert response.status_code == 403

    async def test_webhook_correct_secret_accepted(self, client: httpx.AsyncClient):
        """POST /webhook with the correct secret must NOT return 403.

        The downstream handler may still fail (e.g. 500) because the Telegram
        update payload is minimal, but 403 means the secret check itself
        rejected the request — which should not happen here.
        """
        response = await client.post(
            "/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret-123"},
        )
        assert response.status_code != 403


# ---------------------------------------------------------------------------
# /feedback_webhook endpoint tests
# ---------------------------------------------------------------------------

class TestFeedbackWebhookSecurity:
    """Tests for the ``/feedback_webhook`` endpoint secret validation."""

    async def test_feedback_webhook_no_secret_returns_403_or_503(
        self, client: httpx.AsyncClient
    ):
        """POST /feedback_webhook without the secret header must return 403 or 503.

        If ``FEEDBACK_WEBHOOK_SECRET`` is empty/None the endpoint returns 503;
        if the header simply doesn't match it returns 403.  Both indicate
        rejection.
        """
        response = await client.post("/feedback_webhook", json={"update_id": 1})
        assert response.status_code in (403, 503)

    async def test_feedback_webhook_wrong_secret_returns_403(
        self, client: httpx.AsyncClient
    ):
        """POST /feedback_webhook with an incorrect secret must return 403."""
        response = await client.post(
            "/feedback_webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert response.status_code == 403

    async def test_feedback_webhook_correct_secret_returns_200(
        self, client: httpx.AsyncClient
    ):
        """POST /feedback_webhook with the correct secret and a non-reply message
        must return 200 (the handler ignores non-reply messages gracefully).
        """
        response = await client.post(
            "/feedback_webhook",
            json={
                "update_id": 2,
                "message": {
                    "message_id": 10,
                    "from": {"id": 99999, "is_bot": False, "first_name": "Admin"},
                    "chat": {"id": 99999, "type": "private"},
                    "date": 1700000000,
                    "text": "hello",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "feedback-secret-456"},
        )
        assert response.status_code == 200
