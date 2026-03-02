"""Security and reliability regression tests."""

from __future__ import annotations

import importlib
from pathlib import Path


def _reload_config():
    import config

    return importlib.reload(config)


def test_validate_config_requires_feedback_webhook_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GEMINI_MODEL_NAME", "gemini-model")
    monkeypatch.setenv("WEBHOOK_SECRET", "main-webhook-secret")
    monkeypatch.setenv("FEEDBACK_BOT_TOKEN", "feedback-token")
    monkeypatch.setenv("FEEDBACK_ADMIN_ID", "12345")
    monkeypatch.delenv("FEEDBACK_WEBHOOK_SECRET", raising=False)

    config = _reload_config()
    assert (
        config.validate_config(check_prompts=False, require_webhook_secret=False)
        is False
    )

    monkeypatch.setenv("FEEDBACK_WEBHOOK_SECRET", "feedback-webhook-secret")
    config = _reload_config()
    assert (
        config.validate_config(check_prompts=False, require_webhook_secret=False)
        is True
    )


def test_get_user_daily_output_messages_round_trip(tmp_path) -> None:
    """Verify get_user_daily_output_messages correctly sums output_messages."""
    import database

    original_database_file = database.DATABASE_FILE
    try:
        database.DatabaseManager._instance = None
        database.DATABASE_FILE = str(tmp_path / "test_tracking_data.db")

        assert database.init_db() is True

        user_id = 123

        # No usage yet
        assert database.get_user_daily_output_messages(user_id) == 0

        # Log a single-message translation
        database.log_token_usage_to_db(
            user_id=user_id,
            service_name="gemini",
            tokens_this_call=100,
            is_translation=True,
            output_messages=1,
        )
        assert database.get_user_daily_output_messages(user_id) == 1

        # Log a multi-message translation (2 parts)
        database.log_token_usage_to_db(
            user_id=user_id,
            service_name="gemini",
            tokens_this_call=200,
            is_translation=True,
            output_messages=2,
        )
        assert database.get_user_daily_output_messages(user_id) == 3
    finally:
        database.DATABASE_FILE = original_database_file
        database.DatabaseManager._instance = None


def test_webhook_source_contains_secret_validation() -> None:
    webhook_source = Path("webhook.py").read_text(encoding="utf-8")
    assert "if not WEBHOOK_SECRET" in webhook_source
    assert "if not FEEDBACK_WEBHOOK_SECRET" in webhook_source
    assert "if secret_header != FEEDBACK_WEBHOOK_SECRET" in webhook_source
