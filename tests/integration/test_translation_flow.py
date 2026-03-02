"""Integration tests for the translation pipeline.

Tests the full flow from receiving a Telegram message through to the
Gemini API call, response formatting, and database state changes.

Tests:
- Text translation full pipeline (usage logging with output_messages)
- Image translation full pipeline (download + OCR)
- Image with caption structured response parsing
- Translation failure on API error (zero tokens)
- Daily limit exceeded for free user (upgrade prompt, no API call)
- Usage logging to api_token_usage table with output_messages
- Multi-message output counting (long text -> 2+ parts)
"""

from __future__ import annotations



from unittest.mock import AsyncMock, MagicMock, PropertyMock

import database
from constants import RATE_LIMITS
from handlers.translation import translate_message
from tests.integration.conftest import make_gemini_response, make_gemini_stream_chunks, fake_stream_iterator
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
        return fake_stream_iterator(chunks)

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect
    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text=text, total_tokens=total_tokens,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )


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
        return dict(row)


def _get_daily_output_messages(user_id: int) -> int:
    """Return the daily output message count for *user_id*."""
    return database.get_user_daily_output_messages(user_id)


def _prepopulate_usage(user_id: int, count: int) -> None:
    """Insert *count* usage rows for *user_id* to simulate prior usage today."""
    for _ in range(count):
        database.log_token_usage_to_db(
            user_id=user_id,
            service_name="gemini",
            tokens_this_call=100,
            is_translation=True,
            output_messages=1,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_text_translation_full_pipeline(tmp_db, patch_gemini):
    """Text message -> handler -> Gemini -> formatted response.

    Verifies:
    - Usage logged in api_token_usage table with output_messages=1
    """
    user_id = 1

    _set_streaming_response(patch_gemini, "Salom dunyo", 100, 50, 50)

    update, context = make_text_update(text="Hello world", user_id=user_id, chat_id=user_id)

    await translate_message(update, context)

    # The handler should have edited the status message with the translation
    context.bot.edit_message_text.assert_called()
    last_call_kwargs = context.bot.edit_message_text.call_args
    assert "Salom dunyo" in str(last_call_kwargs)

    # Verify usage was logged with output_messages
    assert _count_usage_rows(user_id) == 1
    usage = _get_usage_row(user_id)
    assert usage["token_count"] == 100
    assert usage["is_translation_related"] == 1
    assert usage["content_type"] == "text"
    assert usage["output_messages"] == 1


async def test_image_translation_full_pipeline(tmp_db, patch_gemini):
    """Photo message -> download -> Gemini OCR -> response with image label."""
    user_id = 2

    _set_streaming_response(patch_gemini, "Rasmdan tarjima qilingan matn", 200, 150, 50)

    update, context = make_photo_update(user_id=user_id, chat_id=user_id)

    await translate_message(update, context)

    context.bot.get_file.assert_awaited_once()

    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert "Rasmdan tarjima qilingan matn" in call_text

    # Verify usage was logged
    assert _count_usage_rows(user_id) == 1


async def test_image_with_caption_translation(tmp_db, patch_gemini):
    """Photo + caption -> structured response parsing (IMAGE_TEXT / CAPTION_TEXT)."""
    user_id = 3

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

    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert "Rasmda yozilgan matn" in call_text
    assert "Izoh tarjimasi" in call_text


async def test_translation_failure_on_api_error(tmp_db, patch_gemini):
    """Gemini returns 0 tokens -> failure (no refund needed in new system)."""
    user_id = 4

    _set_streaming_response(patch_gemini, "", 0, 0, 0)

    update, context = make_text_update(
        text="Hello world", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    # No usage should be logged for failed translations
    assert _count_usage_rows(user_id) == 0


async def test_daily_limit_exceeded_free_user(tmp_db, patch_gemini):
    """User with daily limit exhausted -> upgrade prompt, no API call."""
    user_id = 5

    # Pre-populate usage rows up to free limit
    _prepopulate_usage(user_id, RATE_LIMITS.DAILY_MESSAGES_FREE)

    update, context = make_text_update(
        text="Hello world", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    # Gemini should NOT have been called
    patch_gemini.aio.models.generate_content.assert_not_awaited()
    patch_gemini.aio.models.generate_content_stream.assert_not_awaited()

    # The response should contain the limit exceeded message
    last_call_kwargs = context.bot.edit_message_text.call_args
    call_text = str(last_call_kwargs)
    assert "limit" in call_text.lower() or "Obuna" in call_text


async def test_translation_usage_logged_to_db(tmp_db, patch_gemini):
    """Successful translation -> row in api_token_usage table with output_messages."""
    user_id = 6

    _set_streaming_response(patch_gemini, "Tarjima natijasi", 120, 70, 50)

    update, context = make_text_update(
        text="Text to translate", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

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
    assert usage["output_messages"] == 1


async def test_multi_message_output_counting(tmp_db, patch_gemini):
    """Long translation that splits into 2+ parts logs output_messages=len(parts)."""
    user_id = 7

    # Generate text longer than 4096 chars to trigger split_message
    long_text = "Tarjima natijasi. " * 300  # ~5400 chars

    _set_streaming_response(patch_gemini, long_text, 500, 200, 300)

    update, context = make_text_update(
        text="Long text to translate", user_id=user_id, chat_id=user_id,
    )

    await translate_message(update, context)

    usage = _get_usage_row(user_id)
    assert usage is not None
    # The output should have been split into multiple messages
    assert usage["output_messages"] >= 2

    # Daily output messages should reflect the split count
    daily = _get_daily_output_messages(user_id)
    assert daily >= 2


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


async def test_text_translation_uses_streaming(tmp_db, patch_gemini):
    """Text-only translation uses streaming API and edits message progressively."""
    user_id = 30

    chunks = make_gemini_stream_chunks(
        text="Salom dunyo tarjimasi", total_tokens=100, input_tokens=50, output_tokens=50,
    )

    async def stream_effect(*a, **kw):
        return fake_stream_iterator(chunks)

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
    # Usage logged
    assert _count_usage_rows(user_id) == 1


async def test_image_with_caption_skips_streaming(tmp_db, patch_gemini):
    """Image + caption uses non-streaming path for structured response parsing."""
    user_id = 31

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

    async def failing_stream(*a, **kw):
        raise TimeoutError("stream timed out")

    patch_gemini.aio.models.generate_content_stream.side_effect = failing_stream

    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text="Fallback tarjima", total_tokens=80, input_tokens=40, output_tokens=40,
    )

    update, context = make_text_update(text="Hello fallback", user_id=user_id, chat_id=user_id)
    await translate_message(update, context)

    patch_gemini.aio.models.generate_content.assert_awaited()
    last_edit = context.bot.edit_message_text.call_args
    assert "Fallback tarjima" in str(last_edit)


async def test_streaming_chunk_valueerror_skipped(tmp_db, patch_gemini):
    """Chunks that raise ValueError on .text are silently skipped."""
    user_id = 33

    good_chunk = MagicMock()
    good_chunk.text = "Salom dunyo"
    good_chunk.usage_metadata.total_token_count = 50
    good_chunk.usage_metadata.prompt_token_count = 25
    good_chunk.usage_metadata.candidates_token_count = 25

    bad_chunk = MagicMock()
    type(bad_chunk).text = PropertyMock(side_effect=ValueError("blocked"))
    bad_chunk.usage_metadata = None

    async def stream_effect(*a, **kw):
        return fake_stream_iterator([bad_chunk, good_chunk])

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Test bad chunk", user_id=user_id, chat_id=user_id)
    await translate_message(update, context)

    last_edit = context.bot.edit_message_text.call_args
    assert "Salom dunyo" in str(last_edit)


async def test_streaming_stops_edits_on_deleted_message(tmp_db, patch_gemini):
    """When status message is deleted mid-stream, edits stop without log spam."""
    from telegram.error import BadRequest

    user_id = 34

    chunks = make_gemini_stream_chunks(
        text="A" * 200,
        total_tokens=100, input_tokens=50, output_tokens=50,
        num_chunks=10,
    )

    async def stream_effect(*a, **kw):
        return fake_stream_iterator(chunks)

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Test deleted msg", user_id=user_id, chat_id=user_id)

    original_edit = context.bot.edit_message_text

    async def edit_side_effect(*args, **kwargs):
        text = kwargs.get("text", "")
        if "\u258c" in str(text):
            raise BadRequest("Message to edit not found")
        return await original_edit(*args, **kwargs)

    context.bot.edit_message_text = AsyncMock(side_effect=edit_side_effect)

    await translate_message(update, context)

    assert context.bot.edit_message_text.call_count < 6


async def test_streaming_estimates_tokens_when_metadata_missing(tmp_db, patch_gemini):
    """When stream has no usage_metadata, tokens are estimated from text length."""
    user_id = 35

    chunk = MagicMock()
    chunk.text = "Tarjima matni"
    chunk.usage_metadata = None

    async def stream_effect(*a, **kw):
        return fake_stream_iterator([chunk])

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Estimate tokens", user_id=user_id, chat_id=user_id)
    await translate_message(update, context)

    # Should have logged usage (token estimate > 0 means success)
    assert _count_usage_rows(user_id) == 1


async def test_streaming_message_too_long_sends_continuation(tmp_db, patch_gemini):
    """When streamed text exceeds 4096 chars, edits stop and a continuation message is sent."""
    from telegram.error import BadRequest

    user_id = 36

    long_text = "A" * 5000
    chunks = make_gemini_stream_chunks(
        text=long_text, total_tokens=200, input_tokens=100, output_tokens=100,
        num_chunks=1,
    )

    async def stream_effect(*a, **kw):
        return fake_stream_iterator(chunks)

    patch_gemini.aio.models.generate_content_stream.side_effect = stream_effect

    update, context = make_text_update(text="Long text", user_id=user_id, chat_id=user_id)

    original_edit = context.bot.edit_message_text
    edit_call_count = 0

    async def edit_side_effect(*args, **kwargs):
        nonlocal edit_call_count
        edit_call_count += 1
        text = kwargs.get("text", "")
        if len(str(text)) > 4096:
            raise BadRequest("Message_too_long")
        return await original_edit(*args, **kwargs)

    context.bot.edit_message_text = AsyncMock(side_effect=edit_side_effect)

    continuation_msg = MagicMock()
    continuation_msg.message_id = 999
    context.bot.send_message = AsyncMock(return_value=continuation_msg)

    await translate_message(update, context)

    send_calls = context.bot.send_message.call_args_list
    continuation_texts = [str(c) for c in send_calls]
    assert any("davom etmoqda" in t for t in continuation_texts)

    context.bot.delete_message.assert_called_once_with(
        chat_id=user_id,
        message_id=999,
    )
