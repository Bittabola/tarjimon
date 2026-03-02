"""Integration tests for edge cases in the translation handler.

Tests:
- Rate limit exceeded: user exceeds RATE_LIMITS.REQUESTS_PER_MINUTE, Gemini NOT called.
- Oversized image rejected: photo exceeds IMAGE_LIMITS.MAX_IMAGE_SIZE_MB, Gemini NOT called.
- Empty message rejected: message with no text/image content, Gemini NOT called.
"""

from __future__ import annotations


from constants import RATE_LIMITS, IMAGE_LIMITS
from handlers.translation import translate_message
from user_management import user_manager
from tests.integration.helpers import make_text_update, make_photo_update


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_rate_limit_exceeded(tmp_db, patch_gemini):
    """Exceeding rate limit -> error message, Gemini NOT called."""
    user_id = 1

    # Exhaust the rate limit
    for _ in range(RATE_LIMITS.REQUESTS_PER_MINUTE + 1):
        user_manager.check_rate_limit(user_id)

    update, context = make_text_update(
        text="Hello world", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    # Gemini should NOT have been called
    patch_gemini.aio.models.generate_content.assert_not_awaited()

    context.bot.edit_message_text.assert_called()
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert (
        str(RATE_LIMITS.REQUESTS_PER_MINUTE) in call_text
        or "so'rov" in call_text.lower()
    )


async def test_oversized_image_rejected(tmp_db, patch_gemini):
    """Image exceeding MAX_IMAGE_SIZE_MB -> error, Gemini NOT called."""
    user_id = 2

    oversized_bytes = IMAGE_LIMITS.MAX_IMAGE_SIZE_MB * 1024 * 1024 + 1

    update, context = make_photo_update(
        user_id=user_id,
        chat_id=user_id,
        file_size=oversized_bytes,
    )

    await translate_message(update, context)

    patch_gemini.aio.models.generate_content.assert_not_awaited()

    context.bot.edit_message_text.assert_called()
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert (
        str(IMAGE_LIMITS.MAX_IMAGE_SIZE_MB) in call_text
        or "hajm" in call_text.lower()
    )


async def test_empty_message_rejected(tmp_db, patch_gemini):
    """Message with no text and no image -> error, Gemini NOT called."""
    user_id = 3

    update, context = make_text_update(
        text="placeholder", user_id=user_id, chat_id=user_id,
    )
    update.message.text = None
    update.message.photo = None
    update.message.caption = None
    update.message.document = None

    await translate_message(update, context)

    patch_gemini.aio.models.generate_content.assert_not_awaited()

    context.bot.edit_message_text.assert_called()
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert (
        "matn" in call_text.lower()
        or "rasm" in call_text.lower()
    )
