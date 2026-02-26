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



import database
from handlers.translation import translate_message
from tests.integration.conftest import make_gemini_response
from tests.integration.helpers import make_text_update, make_photo_update


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        user_id=user_id, youtube_minutes=10, translations=10,
    )

    # Configure Gemini mock to return a translated text
    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text="Salom dunyo",
        total_tokens=100,
        input_tokens=50,
        output_tokens=50,
    )

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
        user_id=user_id, youtube_minutes=10, translations=10,
    )

    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text="Rasmdan tarjima qilingan matn",
        total_tokens=200,
        input_tokens=150,
        output_tokens=50,
    )

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
        user_id=user_id, youtube_minutes=10, translations=10,
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
        user_id=user_id, youtube_minutes=10, translations=10,
    )

    # Return a response with 0 tokens to trigger refund
    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text="Xatolik yuz berdi.",
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
    )

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
        user_id=user_id, youtube_minutes=10, translations=0,
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
        user_id=user_id, youtube_minutes=10, translations=10,
    )

    patch_gemini.aio.models.generate_content.return_value = make_gemini_response(
        text="Tarjima natijasi",
        total_tokens=120,
        input_tokens=70,
        output_tokens=50,
    )

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
