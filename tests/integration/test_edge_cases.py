"""Integration tests for edge cases in the translation handler.

Tests:
- Rate limit exceeded: user exceeds RATE_LIMITS.REQUESTS_PER_MINUTE, Gemini NOT called.
- Oversized image rejected: photo exceeds IMAGE_LIMITS.MAX_IMAGE_SIZE_MB, Gemini NOT called.
- Empty message rejected: message with no text/image content, Gemini NOT called.
"""

from __future__ import annotations


import database
from constants import RATE_LIMITS, IMAGE_LIMITS
from handlers.translation import translate_message
from user_management import user_manager
from tests.integration.helpers import make_text_update, make_photo_update


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_rate_limit_exceeded(tmp_db, patch_gemini):
    """Exceeding rate limit -> error message, Gemini NOT called.

    Simulates a user sending more than REQUESTS_PER_MINUTE requests
    within a 60-second window.  The handler should return an error
    message without ever calling the Gemini API.
    """
    user_id = 1

    database.ensure_free_user_subscription(
        user_id=user_id, youtube_minutes=10, translations=10,
    )

    # Exhaust the rate limit by calling check_rate_limit many times.
    # Each call appends a timestamp to the session's request_timestamps deque.
    # We need REQUESTS_PER_MINUTE + 1 calls so the next check (inside the
    # handler) will see the limit exceeded.
    for _ in range(RATE_LIMITS.REQUESTS_PER_MINUTE + 1):
        user_manager.check_rate_limit(user_id)

    # Now call translate_message -- it should hit the rate limit guard
    update, context = make_text_update(
        text="Hello world", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    # Gemini should NOT have been called
    patch_gemini.aio.models.generate_content.assert_not_awaited()

    # The bot should have sent an error message about too many requests
    context.bot.edit_message_text.assert_called()
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    # The rate limit error contains the limit number or a recognisable phrase
    assert (
        str(RATE_LIMITS.REQUESTS_PER_MINUTE) in call_text
        or "so'rov" in call_text.lower()
    )


async def test_oversized_image_rejected(tmp_db, patch_gemini):
    """Image exceeding MAX_IMAGE_SIZE_MB -> error, Gemini NOT called.

    Sends a photo update whose file_size is larger than the configured
    maximum.  The handler should respond with an image-too-large message
    and never touch the Gemini API.
    """
    user_id = 2

    database.ensure_free_user_subscription(
        user_id=user_id, youtube_minutes=10, translations=10,
    )

    # Create a photo that exceeds the size limit
    oversized_bytes = IMAGE_LIMITS.MAX_IMAGE_SIZE_MB * 1024 * 1024 + 1

    update, context = make_photo_update(
        user_id=user_id,
        chat_id=user_id,
        file_size=oversized_bytes,
    )

    await translate_message(update, context)

    # Gemini should NOT have been called
    patch_gemini.aio.models.generate_content.assert_not_awaited()

    # The bot should have sent an error about image size
    context.bot.edit_message_text.assert_called()
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert (
        str(IMAGE_LIMITS.MAX_IMAGE_SIZE_MB) in call_text
        or "hajm" in call_text.lower()
    )


async def test_empty_message_rejected(tmp_db, patch_gemini):
    """Message with no text and no image -> error, Gemini NOT called.

    Sends a text update but sets message.text to None and message.photo
    to None, simulating an empty/unsupported message type.  The handler
    should respond with an appropriate error and skip the Gemini call.
    """
    user_id = 3

    database.ensure_free_user_subscription(
        user_id=user_id, youtube_minutes=10, translations=10,
    )

    # Build a text update then strip away the content
    update, context = make_text_update(
        text="placeholder", user_id=user_id, chat_id=user_id,
    )
    # Remove text so there is nothing to translate
    update.message.text = None
    update.message.photo = None
    update.message.caption = None
    update.message.document = None

    await translate_message(update, context)

    # Gemini should NOT have been called
    patch_gemini.aio.models.generate_content.assert_not_awaited()

    # The bot should have sent an error about missing content
    context.bot.edit_message_text.assert_called()
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    # The error message should be SEND_TEXT_OR_IMAGE or similar
    assert (
        "matn" in call_text.lower()
        or "rasm" in call_text.lower()
    )
