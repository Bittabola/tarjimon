"""Integration tests for feedback flow.

Tests:
1. /aloqa command enters feedback mode when configured
2. /aloqa disabled when FEEDBACK_BOT_TOKEN / FEEDBACK_ADMIN_ID not configured
3. Feedback message saved to DB and forwarded to admin
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers import feedback
from handlers.feedback import (
    aloqa,
    handle_feedback_message,
    is_user_pending_feedback,
    _feedback_pending_users,
)
from tests.integration.helpers import make_command_update, make_text_update


# ------------------------------------------------------------------
# 1. /aloqa enters feedback mode when configured
# ------------------------------------------------------------------


async def test_aloqa_command_enters_feedback_mode(tmp_db, monkeypatch):
    """/aloqa with valid config adds user to pending set and replies."""
    user_id = 500
    monkeypatch.setattr(feedback, "FEEDBACK_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setattr(feedback, "FEEDBACK_ADMIN_ID", "123456")

    update, ctx = make_command_update(command="/aloqa", user_id=user_id)

    await aloqa(update, ctx)

    assert is_user_pending_feedback(user_id) is True
    update.message.reply_text.assert_awaited_once()


# ------------------------------------------------------------------
# 2. /aloqa disabled when not configured
# ------------------------------------------------------------------


async def test_aloqa_disabled_when_not_configured(tmp_db, monkeypatch):
    """/aloqa with missing config does NOT enter feedback mode."""
    user_id = 501
    monkeypatch.setattr(feedback, "FEEDBACK_BOT_TOKEN", None)
    monkeypatch.setattr(feedback, "FEEDBACK_ADMIN_ID", None)

    update, ctx = make_command_update(command="/aloqa", user_id=user_id)

    await aloqa(update, ctx)

    assert is_user_pending_feedback(user_id) is False
    # Still replies (with "not available" message)
    update.message.reply_text.assert_awaited_once()


# ------------------------------------------------------------------
# 3. Feedback message saved to DB
# ------------------------------------------------------------------


async def test_feedback_message_saved_to_db(tmp_db, monkeypatch):
    """Pending user's text message is saved to DB and handler returns True."""
    user_id = 502
    feedback_text = "Great bot, thanks!"

    # Put user in feedback mode directly
    _feedback_pending_users.add(user_id)

    # Ensure feedback config is set (needed for the Telegram API URL)
    monkeypatch.setattr(feedback, "FEEDBACK_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setattr(feedback, "FEEDBACK_ADMIN_ID", "123456")

    update, ctx = make_text_update(text=feedback_text, user_id=user_id)

    # Mock httpx.AsyncClient so no real HTTP call is made.
    # The handler does: async with httpx.AsyncClient() as client: ...
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "ok": True,
        "result": {"message_id": 9999},
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        result = await handle_feedback_message(update, ctx)

    assert result is True

    # User should no longer be in pending set
    assert is_user_pending_feedback(user_id) is False

    # Verify the feedback row exists in the database
    db_manager = database.DatabaseManager()
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, message_text, username, first_name FROM feedback WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()

    assert row is not None
    assert row["user_id"] == user_id
    assert row["message_text"] == feedback_text
    assert row["username"] == "testuser"
    assert row["first_name"] == "Test"
