"""Integration tests for the translation pipeline.

Tests the full flow from receiving a Telegram message through to the
Gemini API call, response formatting, and database state changes.

Tests:
- Text translation full pipeline (quota decrement + usage logging)
- Image translation full pipeline (download + OCR)
- Image with caption structured response parsing
- Quota refund on API error (zero tokens returned)
- Quota exhausted for free user (upgrade prompt, no API call)
- Usage logging to api_token_usage table
"""

from __future__ import annotations



from unittest.mock import AsyncMock, MagicMock, PropertyMock

import database
from handlers.translation import translate_message
from tests.integration.conftest import make_gemini_response, make_gemini_stream_chunks, _fake_stream_iterator
from tests.integration.helpers import make_text_update, make_photo_update


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_streaming_response(patch_gemini, text, total_tokens, input_tokens, output_tokens):
    """Configure both generate_content_stream and generate_content with the same response."""
    chunks = make_gemini_stream_chunks(
        text=text, total_tokens=total_tokens,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )

    async def stream_effect(*a, **kw):
        return _fake_stream_iterator(chunks)

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect
    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text=text, total_tokens=total_tokens,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )


def _get_subscription(user_id: int) -> dict | None:
    """Read the user_subscriptions row for *user_id* from the temp DB."""
    return database.get_user_subscription(user_id)


def _count_usage_rows(user_id: int) -> int:
    """Return the number of rows in api_token_usage for *user_id*."""
    db = database.DatabaseManager()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM api_token_usage WHERE user_id = ?",
            (user_id,),
        )
        return cursor.fetchone()[0]


def _get_usage_row(user_id: int) -> dict | None:
    """Return the most recent api_token_usage row for *user_id* as a dict."""
    db = database.DatabaseManager()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM api_token_usage WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        # sqlite3.Row supports dict() conversion via keys()
        return dict(row)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_text_translation_full_pipeline(tmp_db, patch_gemini):
    """Text message -> handler -> Gemini -> formatted response.

    Verifies:
    - DB quota decremented (translation_remaining 10 -> 9)
    - Usage logged in api_token_usage table
    """
    user_id = 1

    # Set up user with 10 translations
    database.ensure_free_user_subscription(
        user_id=user_id, translations=10,
    )

    # Configure Gemini mock (streaming + non-streaming) to return a translated text
    _set_streaming_response(patch_gemini, "Salom dunyo", 100, 50, 50)

    update, context = make_text_update(text="Hello world", user_id=user_id, chat_id=user_id)

    await translate_message(update, context)

    # The handler should have edited the status message with the translation
    context.bot.edit_message_text.assert_called()
    # The final edit should contain the translated text
    last_call_kwargs = context.bot.edit_message_text.call_args
    assert "Salom dunyo" in str(last_call_kwargs)

    # Verify quota decremented: 10 -> 9
    sub = _get_subscription(user_id)
    assert sub is not None
    assert sub["translation_remaining"] == 9

    # Verify usage was logged
    assert _count_usage_rows(user_id) == 1
    usage = _get_usage_row(user_id)
    assert usage["token_count"] == 100
    assert usage["is_translation_related"] == 1
    assert usage["content_type"] == "text"


async def test_image_translation_full_pipeline(tmp_db, patch_gemini):
    """Photo message -> download -> Gemini OCR -> response with image label.

    Verifies the image download flow and that the response is formatted
    with the image translation label.
    """
    user_id = 2

    database.ensure_free_user_subscription(
        user_id=user_id, translations=10,
    )

    _set_streaming_response(patch_gemini, "Rasmdan tarjima qilingan matn", 200, 150, 50)

    update, context = make_photo_update(user_id=user_id, chat_id=user_id)

    await translate_message(update, context)

    # Verify bot.get_file was called to download the image
    context.bot.get_file.assert_awaited_once()

    # The final edit should contain the image label and translated text
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert "Rasmdan tarjima qilingan matn" in call_text

    # Quota should be decremented
    sub = _get_subscription(user_id)
    assert sub["translation_remaining"] == 9


async def test_image_with_caption_translation(tmp_db, patch_gemini):
    """Photo + caption -> structured response parsing (IMAGE_TEXT / CAPTION_TEXT).

    When the Gemini response contains IMAGE_TEXT and CAPTION_TEXT markers,
    the handler should parse them into separate labelled sections.
    """
    user_id = 3

    database.ensure_free_user_subscription(
        user_id=user_id, translations=10,
    )

    structured_response = (
        "IMAGE_TEXT: Rasmda yozilgan matn\nCAPTION_TEXT: Izoh tarjimasi"
    )
    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text=structured_response,
        total_tokens=180,
        input_tokens=120,
        output_tokens=60,
    )

    update, context = make_photo_update(
        user_id=user_id, chat_id=user_id, caption="Some caption text",
    )

    await translate_message(update, context)

    # The final edit should contain both the image and caption sections
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert "Rasmda yozilgan matn" in call_text
    assert "Izoh tarjimasi" in call_text

    # Quota should be decremented
    sub = _get_subscription(user_id)
    assert sub["translation_remaining"] == 9


async def test_translation_refund_on_api_error(tmp_db, patch_gemini):
    """Gemini returns 0 tokens -> quota refunded (stays at 10).

    When the API returns zero tokens, execute_translation detects failure
    and calls increment_translation_limit to refund the reserved credit.
    """
    user_id = 4

    database.ensure_free_user_subscription(
        user_id=user_id, translations=10,
    )

    # Return a response with empty text and 0 tokens to trigger refund.
    # Streaming path: empty text + 0 tokens → no token estimation → refund.
    _set_streaming_response(patch_gemini, "", 0, 0, 0)

    update, context = make_text_update(
        text="Hello world", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    # Quota should remain at 10 (decremented then refunded)
    sub = _get_subscription(user_id)
    assert sub is not None
    assert sub["translation_remaining"] == 10

    # No usage should be logged for failed translations
    assert _count_usage_rows(user_id) == 0


async def test_translation_quota_exhausted_free_user(tmp_db, patch_gemini):
    """User with 0 translations remaining -> upgrade prompt, no API call.

    The handler should detect zero remaining translations and display
    the subscription upgrade prompt without calling the Gemini API.
    """
    user_id = 5

    # Set up user with 0 translations remaining
    database.ensure_free_user_subscription(
        user_id=user_id, translations=0,
    )

    update, context = make_text_update(
        text="Hello world", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    # Gemini should NOT have been called
    patch_gemini.aio.models.generate_content.assert_not_awaited()

    # The response should contain the subscription upgrade prompt
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    # The prompt contains the free limit exceeded message
    assert "limit" in call_text.lower() or "Obuna" in call_text


async def test_translation_usage_logged_to_db(tmp_db, patch_gemini):
    """Successful translation -> row in api_token_usage table.

    Verifies that all relevant fields (service_name, token counts,
    content_type, content_preview) are correctly recorded.
    """
    user_id = 6

    database.ensure_free_user_subscription(
        user_id=user_id, translations=10,
    )

    _set_streaming_response(patch_gemini, "Tarjima natijasi", 120, 70, 50)

    update, context = make_text_update(
        text="Text to translate", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    # Verify exactly one usage row was created
    assert _count_usage_rows(user_id) == 1

    usage = _get_usage_row(user_id)
    assert usage is not None
    assert usage["user_id"] == user_id
    assert usage["service_name"] == "gemini"
    assert usage["token_count"] == 120
    assert usage["input_tokens"] == 70
    assert usage["output_tokens"] == 50
    assert usage["is_translation_related"] == 1
    assert usage["content_type"] == "text"
    assert usage["content_preview"] is not None
    assert "Text to translate" in usage["content_preview"]


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


async def test_text_translation_uses_streaming(tmp_db, patch_gemini):
    """Text-only translation uses streaming API and edits message progressively."""
    user_id = 30
    database.ensure_free_user_subscription(user_id=user_id, translations=10)

    chunks = make_gemini_stream_chunks(
        text="Salom dunyo tarjimasi", total_tokens=100, input_tokens=50, output_tokens=50,
    )

    async def stream_effect(*a, **kw):
        return _fake_stream_iterator(chunks)

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Hello world", user_id=user_id, chat_id=user_id)
    await translate_message(update, context)

    # Streaming endpoint was called
    patch_gemini.aio.models.generate_content_stream.assert_awaited_once()
    # Non-streaming was NOT called
    patch_gemini.aio.models.generate_content.assert_not_awaited()
    # Final message contains translated text
    last_edit = context.bot.edit_message_text.call_args
    assert "Salom dunyo tarjimasi" in str(last_edit)
    # Quota decremented
    sub = database.get_user_subscription(user_id)
    assert sub["translation_remaining"] == 9


async def test_image_with_caption_skips_streaming(tmp_db, patch_gemini):
    """Image + caption uses non-streaming path for structured response parsing."""
    user_id = 31
    database.ensure_free_user_subscription(user_id=user_id, translations=10)

    structured = "IMAGE_TEXT: Rasm matni\nCAPTION_TEXT: Izoh tarjimasi"
    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text=structured, total_tokens=150, input_tokens=100, output_tokens=50,
    )

    update, context = make_photo_update(user_id=user_id, chat_id=user_id, caption="Some caption")
    await translate_message(update, context)

    # Non-streaming was used
    patch_gemini.aio.models.generate_content.assert_awaited()
    # Streaming was NOT used
    patch_gemini.aio.models.generate_content_stream.assert_not_awaited()


async def test_streaming_falls_back_to_non_streaming_after_retries(tmp_db, patch_gemini):
    """When streaming retries exhaust, falls back to non-streaming API."""
    user_id = 32
    database.ensure_free_user_subscription(user_id=user_id, translations=10)

    # Make streaming always fail with a retryable error.
    # Use TimeoutError (Python builtin) to avoid class identity issues when
    # the smoke test conftest restores real google.genai modules.
    async def failing_stream(*a, **kw):
        raise TimeoutError("stream timed out")

    patch_gemini.aio.models.generate_content_stream.side_effect = failing_stream

    # Non-streaming should succeed as fallback
    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text="Fallback tarjima", total_tokens=80, input_tokens=40, output_tokens=40,
    )

    update, context = make_text_update(text="Hello fallback", user_id=user_id, chat_id=user_id)
    await translate_message(update, context)

    # Non-streaming was called as fallback
    patch_gemini.aio.models.generate_content.assert_awaited()
    # Final message contains the fallback translation
    last_edit = context.bot.edit_message_text.call_args
    assert "Fallback tarjima" in str(last_edit)


async def test_streaming_chunk_valueerror_skipped(tmp_db, patch_gemini):
    """Chunks that raise ValueError on .text are silently skipped."""
    user_id = 33
    database.ensure_free_user_subscription(user_id=user_id, translations=10)

    good_chunk = MagicMock()
    good_chunk.text = "Salom dunyo"
    good_chunk.usage_metadata.total_token_count = 50
    good_chunk.usage_metadata.prompt_token_count = 25
    good_chunk.usage_metadata.candidates_token_count = 25

    bad_chunk = MagicMock()
    type(bad_chunk).text = PropertyMock(side_effect=ValueError("blocked"))
    bad_chunk.usage_metadata = None

    async def stream_effect(*a, **kw):
        return _fake_stream_iterator([bad_chunk, good_chunk])

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Test bad chunk", user_id=user_id, chat_id=user_id)
    await translate_message(update, context)

    last_edit = context.bot.edit_message_text.call_args
    assert "Salom dunyo" in str(last_edit)


async def test_streaming_stops_edits_on_deleted_message(tmp_db, patch_gemini):
    """When status message is deleted mid-stream, edits stop without log spam."""
    from telegram.error import BadRequest

    user_id = 34
    database.ensure_free_user_subscription(user_id=user_id, translations=10)

    chunks = make_gemini_stream_chunks(
        text="A" * 200,  # Long enough to trigger multiple edits
        total_tokens=100, input_tokens=50, output_tokens=50,
        num_chunks=10,
    )

    async def stream_effect(*a, **kw):
        return _fake_stream_iterator(chunks)

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Test deleted msg", user_id=user_id, chat_id=user_id)

    # Make edit_message_text raise "message to edit not found" on streaming edits.
    # Streaming edits contain the cursor indicator (▌); status updates and the
    # final formatted edit do not.
    original_edit = context.bot.edit_message_text

    async def edit_side_effect(*args, **kwargs):
        text = kwargs.get("text", "")
        if "\u258c" in str(text):
            raise BadRequest("Message to edit not found")
        return await original_edit(*args, **kwargs)

    context.bot.edit_message_text = AsyncMock(side_effect=edit_side_effect)

    await translate_message(update, context)

    # Translation should still complete (streaming edits are non-fatal)
    # The function should not have called edit many times after the failure
    assert context.bot.edit_message_text.call_count < 6  # Would be ~10+ without the stop


async def test_streaming_estimates_tokens_when_metadata_missing(tmp_db, patch_gemini):
    """When stream has no usage_metadata, tokens are estimated from text length."""
    user_id = 35
    database.ensure_free_user_subscription(user_id=user_id, translations=10)

    # All chunks have usage_metadata=None (simulates abnormal stream end)
    chunk = MagicMock()
    chunk.text = "Tarjima matni"  # 13 chars → estimated ~3 tokens
    chunk.usage_metadata = None

    async def stream_effect(*a, **kw):
        return _fake_stream_iterator([chunk])

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Estimate tokens", user_id=user_id, chat_id=user_id)
    await translate_message(update, context)

    # Should NOT have refunded quota (token estimate > 0 means success)
    sub = database.get_user_subscription(user_id)
    assert sub["translation_remaining"] == 9  # decremented, not refunded
