"""Telegram bot handlers for the Tarjimon bot."""

from __future__ import annotations

import io
import re
import asyncio
import json
import time
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from config import (
    logger,
    MAX_IMAGE_SIZE_MB,
    ERROR_LOG_CONTEXT_ENABLED,
    ERROR_LOG_MAX_TEXT_PREVIEW,
    DEBUG_INCLUDE_USER_CONTEXT,
    GEMINI_API_KEY,
    GEMINI_MODEL_NAME,
    YOUTUBE_MAX_OUTPUT_TOKENS,
    YOUTUBE_FOLLOWUP_MAX_TOKENS,
    YOUTUBE_TEMPERATURE,
    YOUTUBE_MAX_DURATION_MINUTES,
    YOUTUBE_NO_TRANSCRIPT_MULTIPLIER,
    YOUTUBE_CACHE_TTL_SECONDS,
    QUESTION_BUTTON_MAX_LENGTH,
    FREE_YOUTUBE_MINUTES_LIMIT,
    FREE_TRANSLATION_LIMIT,
    SUBSCRIPTION_PLAN,
    SUPADATA_API_KEY,
    format_date_uzbek,
    get_days_remaining,
    PROMPTS,
)
from database import (
    log_token_usage_to_db,
    log_error_to_db,
    is_user_premium,
    activate_premium,
    log_payment,
    get_payment_by_telegram_id,
    get_user_subscription,
    get_user_remaining_limits,
    decrement_youtube_minutes,
    decrement_translation_limit,
    ensure_free_user_subscription,
)
from user_management import user_manager
from utils import (
    safe_html,
    validate_youtube_url,
    extract_youtube_url as utils_extract_youtube_url,
    retry_sync,
)
import strings as S
from errors import (
    YOUTUBE_ERRORS,
    GENERAL_ERRORS,
    INPUT_VALIDATION_ERRORS,
    FORMATTING_LABELS,
)
from constants import (
    RETRY_CONSTANTS,
    API_TIMEOUTS,
)
from google import genai
from google.genai import types
import requests
from requests.exceptions import (
    RequestException,
    Timeout,
    ConnectionError as RequestsConnectionError,
)

# YouTube URL regex pattern - matches various YouTube URL formats
YOUTUBE_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})"
)

# Deduplication cache for YouTube requests (prevents duplicate processing on webhook retries)
# Format: {(user_id, video_id): timestamp}
_youtube_processing_cache: dict[tuple[int, str], float] = {}
_youtube_cache_lock = threading.Lock()


def _is_youtube_request_duplicate(user_id: int, video_id: str) -> bool:
    """Check if this YouTube request is a duplicate (already being processed)."""
    cache_key = (user_id, video_id)
    now = time.time()

    with _youtube_cache_lock:
        # Clean old entries
        expired_keys = [
            k
            for k, v in _youtube_processing_cache.items()
            if now - v > YOUTUBE_CACHE_TTL_SECONDS
        ]
        for k in expired_keys:
            del _youtube_processing_cache[k]

        # Check if already processing
        if cache_key in _youtube_processing_cache:
            return True

        # Mark as processing
        _youtube_processing_cache[cache_key] = now
        return False


def _clear_youtube_cache(user_id: int, video_id: str) -> None:
    """Clear the cache entry for a completed request."""
    cache_key = (user_id, video_id)
    with _youtube_cache_lock:
        _youtube_processing_cache.pop(cache_key, None)


def extract_youtube_video_id(url: str) -> str | None:
    """
    Extract video ID from YouTube URL.

    Args:
        url: YouTube URL

    Returns:
        Video ID if found, None otherwise
    """
    return validate_youtube_url(url)


def extract_youtube_url(text: str) -> str | None:
    """
    Extract YouTube URL from text if present.

    Args:
        text: Input text that may contain a YouTube URL

    Returns:
        Full YouTube URL if found, None otherwise
    """
    return utils_extract_youtube_url(text)


def _ensure_free_user_subscription(user_id: int) -> None:
    """
    Ensure a free user has a subscription record with initial limits.
    This is called when a free user uses the service for the first time.
    """
    ensure_free_user_subscription(
        user_id,
        youtube_minutes=FREE_YOUTUBE_MINUTES_LIMIT,
        translations=FREE_TRANSLATION_LIMIT,
    )


def _get_stats_button(user_id: int) -> InlineKeyboardMarkup:
    """
    Get the persistent stats/subscribe button based on user's subscription status.

    Args:
        user_id: Telegram user ID

    Returns:
        InlineKeyboardMarkup with appropriate button
    """
    is_premium = is_user_premium(user_id)

    if is_premium:
        button_text = FORMATTING_LABELS.STATS_BUTTON
    else:
        button_text = FORMATTING_LABELS.SUBSCRIBE_BUTTON

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(button_text, callback_data="stats_show")]]
    )


@retry_sync(
    max_attempts=RETRY_CONSTANTS.MAX_ATTEMPTS,
    delay_seconds=RETRY_CONSTANTS.INITIAL_DELAY_SECONDS,
    backoff_multiplier=RETRY_CONSTANTS.BACKOFF_MULTIPLIER,
    max_delay_seconds=RETRY_CONSTANTS.MAX_DELAY_SECONDS,
    exceptions=(Timeout, RequestsConnectionError),
)
def fetch_youtube_metadata(video_id: str) -> dict | None:
    """
    Fetch metadata for a YouTube video using Supadata API.
    Includes retry logic for transient failures.

    Args:
        video_id: YouTube video ID

    Returns:
        Dict with video metadata (id, title, duration, etc.) or None if unavailable
    """
    if not SUPADATA_API_KEY:
        logger.warning("SUPADATA_API_KEY not configured, skipping metadata fetch")
        return None

    try:
        response = requests.get(
            "https://api.supadata.ai/v1/youtube/video",
            params={"id": video_id},
            headers={"x-api-key": SUPADATA_API_KEY},
            timeout=API_TIMEOUTS.SUPADATA_METADATA,
        )

        if response.status_code == 200:
            data = response.json()
            logger.info(
                f"Supadata metadata [{video_id}]: duration={data.get('duration')}s, title={data.get('title', '')[:50]}"
            )
            return data
        else:
            logger.warning(
                f"Supadata metadata API error for {video_id}: {response.status_code} - {response.text[:200]}"
            )
            return None

    except (Timeout, RequestsConnectionError):
        # Let retry decorator handle these
        raise
    except RequestException as e:
        logger.warning(f"Supadata metadata API request error for {video_id}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Could not fetch metadata for {video_id}: {e}")
        return None


@retry_sync(
    max_attempts=RETRY_CONSTANTS.MAX_ATTEMPTS,
    delay_seconds=RETRY_CONSTANTS.INITIAL_DELAY_SECONDS,
    backoff_multiplier=RETRY_CONSTANTS.BACKOFF_MULTIPLIER,
    max_delay_seconds=RETRY_CONSTANTS.MAX_DELAY_SECONDS,
    exceptions=(Timeout, RequestsConnectionError),
)
def fetch_youtube_transcript(video_id: str) -> tuple[str | None, str | None]:
    """
    Fetch transcript for a YouTube video using Supadata API.
    Includes retry logic for transient failures.

    Args:
        video_id: YouTube video ID

    Returns:
        Tuple of (transcript_text, language_code) or (None, None) if unavailable
    """
    if not SUPADATA_API_KEY:
        logger.warning("SUPADATA_API_KEY not configured, skipping transcript fetch")
        return None, None

    try:
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"

        # Use mode=native to only fetch existing transcripts (1 credit)
        # This matches what youtube-transcript-api was doing
        response = requests.get(
            "https://api.supadata.ai/v1/transcript",
            params={
                "url": youtube_url,
                "mode": "native",  # Only fetch existing captions, don't generate
                "text": "true",  # Return plain text instead of timestamped chunks
            },
            headers={"x-api-key": SUPADATA_API_KEY},
            timeout=API_TIMEOUTS.SUPADATA_TRANSCRIPT,
        )

        # Handle different response codes
        if response.status_code == 200:
            data = response.json()
            transcript_text = data.get("content", "")
            lang_code = data.get("lang", "unknown")

            if transcript_text:
                logger.info(
                    f"Supadata transcript [{video_id}]: {len(transcript_text)} chars, lang={lang_code}"
                )
                return transcript_text, lang_code
            else:
                logger.debug(f"Supadata returned empty transcript for {video_id}")
                return None, None

        elif response.status_code == 206:
            # 206 = No transcript available (native mode)
            logger.debug(f"No native transcript available for {video_id}")
            return None, None

        elif response.status_code == 202:
            # 202 = Async job started (shouldn't happen with mode=native)
            logger.warning(
                f"Unexpected async response for native transcript {video_id}"
            )
            return None, None

        else:
            logger.warning(
                f"Supadata API error for {video_id}: {response.status_code} - {response.text[:200]}"
            )
            return None, None

    except (Timeout, RequestsConnectionError):
        # Let retry decorator handle these
        raise
    except RequestException as e:
        logger.warning(f"Supadata API request error for {video_id}: {e}")
        return None, None
    except Exception as e:
        logger.debug(f"Could not fetch transcript for {video_id}: {e}")
        return None, None


# Gemini client - lazy initialization to avoid crash if API key not set at import time
_gemini_client = None
_gemini_client_lock = threading.Lock()


def get_gemini_client() -> genai.Client:
    """Get or create the Gemini client (lazy initialization)."""
    global _gemini_client
    if _gemini_client is None:
        with _gemini_client_lock:
            if _gemini_client is None:
                _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _escape_html(text: str) -> str:
    """Escape HTML special characters in text to prevent injection."""
    return safe_html(text)


def _format_translation_output(
    translated_text: str, has_image: bool, has_caption: bool
) -> str:
    """
    Format translation output with appropriate titles, handling structured responses.

    Args:
        translated_text: The translation result from Gemini
        has_image: Whether the message contains an image
        has_caption: Whether the image has a caption

    Returns:
        Formatted string with appropriate titles and sections
    """
    logger.info(
        f"Formatting output: has_image={has_image}, has_caption={has_caption}, text_length={len(translated_text)}"
    )

    if not translated_text:
        return GENERAL_ERRORS.GENERIC_ERROR

    # Handle structured response for image + caption
    if has_image and has_caption:
        if "IMAGE_TEXT:" in translated_text and "CAPTION_TEXT:" in translated_text:
            logger.info("Detected structured response, parsing...")
            parsed_result = _parse_structured_response(translated_text)
            if (
                parsed_result and len(parsed_result.strip()) > 10
            ):  # Ensure we got meaningful content
                return parsed_result
            else:
                logger.warning(
                    "Structured parsing failed or returned minimal content, using fallback"
                )

        # Fallback: treat as combined content
        logger.info("Using fallback formatting for image+caption")
        return FORMATTING_LABELS.IMAGE_AND_TEXT_TRANSLATION + _escape_html(
            translated_text
        )

    # Handle single content types
    if has_image and not has_caption:
        # Image only (OCR extraction)
        if "allaqachon o'zbek tilida" in translated_text.lower():
            return FORMATTING_LABELS.IMAGE_RESULT + _escape_html(translated_text)
        elif "rasmda matn topilmadi" in translated_text.lower():
            return FORMATTING_LABELS.IMAGE_RESULT + _escape_html(translated_text)
        else:
            return FORMATTING_LABELS.IMAGE_TRANSLATION + _escape_html(translated_text)
    elif not has_image:
        # Text message only
        if "allaqachon o'zbek tilida" in translated_text.lower():
            return FORMATTING_LABELS.TEXT_RESULT + _escape_html(translated_text)
        else:
            return FORMATTING_LABELS.TEXT_TRANSLATION + _escape_html(translated_text)
    else:
        # Fallback for any other case
        return _escape_html(translated_text)


def _parse_structured_response(response: str) -> str:
    """
    Parse structured response with IMAGE_TEXT and CAPTION_TEXT sections.

    Args:
        response: Structured response from Gemini

    Returns:
        Formatted output with separate sections
    """
    try:
        logger.info(f"Parsing structured response: {response[:300]}...")

        # Use regex to extract content that might span multiple lines
        image_match = re.search(
            r"IMAGE_TEXT:\s*(.*?)(?=\n\s*CAPTION_TEXT:|$)", response, re.DOTALL
        )
        caption_match = re.search(r"CAPTION_TEXT:\s*(.*?)$", response, re.DOTALL)

        image_text = image_match.group(1).strip() if image_match else ""
        caption_text = caption_match.group(1).strip() if caption_match else ""

        logger.info(f"Extracted IMAGE_TEXT: {image_text[:100]}...")
        logger.info(f"Extracted CAPTION_TEXT: {caption_text[:100]}...")

        # Format the output with separate sections
        output_parts = []

        # Add image section (escape HTML in AI-generated content)
        if image_text:
            escaped_image_text = _escape_html(image_text)
            if "allaqachon o'zbek tilida" in image_text.lower():
                output_parts.append(FORMATTING_LABELS.IMAGE_RESULT + escaped_image_text)
            elif "rasmda matn topilmadi" in image_text.lower():
                output_parts.append(FORMATTING_LABELS.IMAGE_RESULT + escaped_image_text)
            else:
                output_parts.append(
                    FORMATTING_LABELS.IMAGE_TRANSLATION + escaped_image_text
                )

        # Add caption section (escape HTML in AI-generated content)
        if caption_text:
            escaped_caption_text = _escape_html(caption_text)
            if "allaqachon o'zbek tilida" in caption_text.lower():
                output_parts.append(
                    FORMATTING_LABELS.TEXT_RESULT + escaped_caption_text
                )
            else:
                output_parts.append(
                    FORMATTING_LABELS.TEXT_TRANSLATION + escaped_caption_text
                )

        # Join sections with double newline
        final_result = "\n\n".join(output_parts)
        logger.info(
            f"Structured parsing successful, final result length: {len(final_result)}"
        )
        return final_result

    except Exception as e:
        # Fallback to original response if parsing fails
        logger.error(f"Failed to parse structured response: {e}")
        logger.error(f"Original response was: {response[:500]}...")
        return FORMATTING_LABELS.TRANSLATION + _escape_html(response)


def _format_youtube_output(response: str) -> tuple[str, list[str]]:
    """
    Format YouTube summary output with HTML formatting.

    Args:
        response: Structured response from Gemini with SARLAVHA, XULOSA, ASOSIY FIKRLAR, SAVOLLAR

    Returns:
        Tuple of (HTML-formatted output string, list of questions)
    """
    try:
        # Parse structured sections
        title_match = re.search(
            r"SARLAVHA:\s*(.*?)(?=\n\s*XULOSA:|$)", response, re.DOTALL
        )
        summary_match = re.search(
            r"XULOSA:\s*(.*?)(?=\n\s*ASOSIY FIKRLAR:|$)", response, re.DOTALL
        )
        points_match = re.search(
            r"ASOSIY FIKRLAR:\s*(.*?)(?=\n\s*SAVOLLAR:|$)", response, re.DOTALL
        )
        questions_match = re.search(r"SAVOLLAR:\s*(.*?)$", response, re.DOTALL)

        title = title_match.group(1).strip() if title_match else ""
        summary = summary_match.group(1).strip() if summary_match else ""
        points = points_match.group(1).strip() if points_match else ""
        questions_text = questions_match.group(1).strip() if questions_match else ""

        # Parse questions into list
        questions = []
        if questions_text:
            for line in questions_text.split("\n"):
                line = line.strip()
                if line.startswith("-"):
                    question = line[1:].strip()
                    if question:
                        questions.append(question)

        output_parts = []

        # Add title section
        if title:
            output_parts.append(f"<b>{_escape_html(title)}</b>")

        # Add summary section
        if summary:
            output_parts.append(
                f"\n{FORMATTING_LABELS.SUMMARY_SECTION}{_escape_html(summary)}"
            )

        # Add key points section
        if points:
            output_parts.append(
                f"\n{FORMATTING_LABELS.KEY_POINTS_SECTION}{_escape_html(points)}"
            )

        if output_parts:
            return "\n".join(output_parts), questions

        # Fallback if parsing fails
        return FORMATTING_LABELS.VIDEO_SUMMARY + _escape_html(response), []

    except Exception as e:
        logger.error(f"Failed to format YouTube output: {e}")
        return FORMATTING_LABELS.VIDEO_SUMMARY + _escape_html(response), []


def log_error_with_context(
    error: Exception,
    context_info: dict = None,
    user_id: int = None,
    text_preview: str = None,
):
    """Enhanced error logging with contextual information. Also logs to database."""
    if not ERROR_LOG_CONTEXT_ENABLED:
        logger.error(f"Error: {error}")
        return

    error_details = {
        "error_type": type(error).__name__,
        "error_message": str(error),
    }

    if DEBUG_INCLUDE_USER_CONTEXT and user_id:
        error_details["user_id"] = user_id

    if text_preview and len(text_preview) > 0:
        preview = text_preview[:ERROR_LOG_MAX_TEXT_PREVIEW]
        if len(text_preview) > ERROR_LOG_MAX_TEXT_PREVIEW:
            preview += "..."
        error_details["text_preview"] = preview

    if context_info:
        error_details.update(context_info)

    log_parts = [
        f"Enhanced Error Log - {error_details['error_type']}: {error_details['error_message']}"
    ]
    for key, value in error_details.items():
        if key not in ["error_type", "error_message"]:
            log_parts.append(f"  {key}: {value}")
    logger.error("\n".join(log_parts))

    # Also log to database for admin dashboard
    import traceback

    stack_trace = traceback.format_exc()
    content_type = (
        context_info.get("operation", "unknown") if context_info else "unknown"
    )

    log_error_to_db(
        error_type=error_details["error_type"],
        error_message=error_details["error_message"],
        user_id=user_id,
        content_type=content_type,
        content_preview=text_preview[:500] if text_preview else None,
        stack_trace=stack_trace if stack_trace != "NoneType: None\n" else None,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    user_id = update.effective_user.id
    is_premium = is_user_premium(user_id)

    if is_premium:
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0) if subscription else 0
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0) if subscription else 0
        )

        formatted_date = format_date_uzbek(expires_at)

        status_text = (
            f"<b>Premium obuna:</b> {formatted_date}gacha\n"
            f"Qolgan limitlar: {youtube_minutes_remaining} daqiqa video, {translation_remaining} ta tarjima"
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Limitni oshirish", callback_data="subscribe_show")]]
        )
    else:
        # Get free user's remaining limits
        subscription = get_user_subscription(user_id)
        if subscription:
            youtube_minutes_remaining = subscription.get(
                "youtube_minutes_remaining", FREE_YOUTUBE_MINUTES_LIMIT
            )
            translation_remaining = subscription.get(
                "translation_remaining", FREE_TRANSLATION_LIMIT
            )
        else:
            youtube_minutes_remaining = FREE_YOUTUBE_MINUTES_LIMIT
            translation_remaining = FREE_TRANSLATION_LIMIT

        status_text = (
            f"<b>Bepul rejim</b>\n"
            f"Qolgan limitlar: {youtube_minutes_remaining} daqiqa video, {translation_remaining} ta tarjima"
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(S.BTN_SUBSCRIBE, callback_data="subscribe_show")]]
        )

    await update.message.reply_text(
        S.WELCOME_MESSAGE.format(
            status_text=status_text,
            free_youtube_minutes=FREE_YOUTUBE_MINUTES_LIMIT,
            free_translations=FREE_TRANSLATION_LIMIT,
            premium_youtube_minutes=SUBSCRIPTION_PLAN["youtube_minutes_limit"],
            premium_translations=SUBSCRIPTION_PLAN["translation_limit"],
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /subscribe command - show subscription options."""
    user_id = update.effective_user.id
    is_premium = is_user_premium(user_id)

    # Create subscription button
    plan = SUBSCRIPTION_PLAN
    button_text = f"{plan['title']} - {plan['stars']} Yulduz"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
    )

    if is_premium:
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0) if subscription else 0
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0) if subscription else 0
        )

        formatted_date = format_date_uzbek(expires_at)
        days_remaining = get_days_remaining(expires_at)

        await update.message.reply_text(
            S.SUBSCRIBE_PREMIUM_USER_INFO.format(
                days_remaining=days_remaining,
                date=formatted_date,
                youtube_minutes=youtube_minutes_remaining,
                translations=translation_remaining,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        limits_text = S.SUBSCRIBE_FREE_USER_INFO.format(
            free_youtube_minutes=FREE_YOUTUBE_MINUTES_LIMIT,
            free_translations=FREE_TRANSLATION_LIMIT,
            stars=plan["stars"],
            premium_youtube_minutes=plan["youtube_minutes_limit"],
            premium_translations=plan["translation_limit"],
            days=plan["days"],
        )

        await update.message.reply_text(
            f"{S.SUBSCRIBE_HEADING}\n\n{limits_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def handle_subscribe_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle subscription button clicks - send invoice."""
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("subscribe_"):
        return

    # Handle "show subscription options" callback
    if query.data == "subscribe_show":
        user_id = update.effective_user.id
        is_premium = is_user_premium(user_id)

        plan = SUBSCRIPTION_PLAN
        button_text = f"{plan['title']} - {plan['stars']} Yulduz"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
        )

        if is_premium:
            subscription = get_user_subscription(user_id)
            youtube_minutes_remaining = (
                subscription.get("youtube_minutes_remaining", 0) if subscription else 0
            )
            translation_remaining = (
                subscription.get("translation_remaining", 0) if subscription else 0
            )

            await query.message.reply_text(
                S.SUBSCRIBE_PREMIUM_USER_INFO.format(
                    days_remaining="?",
                    date="N/A",
                    youtube_minutes=youtube_minutes_remaining,
                    translations=translation_remaining,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        else:
            limits_text = S.SUBSCRIBE_FREE_USER_INFO.format(
                free_youtube_minutes=FREE_YOUTUBE_MINUTES_LIMIT,
                free_translations=FREE_TRANSLATION_LIMIT,
                stars=plan["stars"],
                premium_youtube_minutes=plan["youtube_minutes_limit"],
                premium_translations=plan["translation_limit"],
                days=plan["days"],
            )

            await query.message.reply_text(
                f"{S.SUBSCRIBE_HEADING}\n\n{limits_text}",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        return

    # Handle "buy" callback
    if query.data == "subscribe_buy":
        plan = SUBSCRIPTION_PLAN

        # Send invoice using Telegram Yulduz
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=plan["title"],
            description=plan["description"],
            payload="premium_30_days",
            provider_token="",  # Empty string for Telegram Yulduz
            currency="XTR",  # XTR = Telegram Yulduz
            prices=[LabeledPrice(plan["title"], plan["stars"])],
        )


async def handle_stats_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle stats button clicks - show user stats and subscription options."""
    query = update.callback_query
    await query.answer()

    if query.data != "stats_show":
        return

    user_id = update.effective_user.id
    is_premium = is_user_premium(user_id)
    plan = SUBSCRIPTION_PLAN

    if is_premium:
        # Premium user: show remaining limits and option to buy more
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0) if subscription else 0
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0) if subscription else 0
        )

        formatted_date = format_date_uzbek(expires_at)
        days_remaining = get_days_remaining(expires_at)

        button_text = f"{S.BTN_INCREASE_LIMIT} - {plan['stars']} Yulduz"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
        )

        await query.message.reply_text(
            S.STATS_PREMIUM.format(
                days_remaining=days_remaining,
                date=formatted_date,
                youtube_minutes=youtube_minutes_remaining,
                translations=translation_remaining,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        # Free user: show remaining limits
        subscription = get_user_subscription(user_id)

        if subscription:
            youtube_minutes_remaining = subscription.get("youtube_minutes_remaining", 0)
            translation_remaining = subscription.get("translation_remaining", 0)
        else:
            # New user - show full free limits
            youtube_minutes_remaining = FREE_YOUTUBE_MINUTES_LIMIT
            translation_remaining = FREE_TRANSLATION_LIMIT

        button_text = f"{S.BTN_SUBSCRIBE} - {plan['stars']} Yulduz"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
        )

        await query.message.reply_text(
            S.STATS_FREE.format(
                youtube_minutes=youtube_minutes_remaining,
                free_youtube_minutes=FREE_YOUTUBE_MINUTES_LIMIT,
                translations=translation_remaining,
                free_translations=FREE_TRANSLATION_LIMIT,
                stars=plan["stars"],
                premium_youtube_minutes=plan["youtube_minutes_limit"],
                premium_translations=plan["translation_limit"],
                days=plan["days"],
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def pre_checkout_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle pre-checkout query - validate the purchase."""
    query = update.pre_checkout_query

    # Validate the plan exists (we only have one plan now)
    plan_id = query.invoice_payload
    if plan_id != "premium_30_days":
        await query.answer(ok=False, error_message=S.INVALID_PLAN)
        return

    # All good, approve the purchase
    await query.answer(ok=True)


async def successful_payment_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle successful payment - activate subscription."""
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    telegram_payment_id = payment.telegram_payment_charge_id

    # Idempotency check: verify this payment hasn't been processed already
    existing_payment = get_payment_by_telegram_id(telegram_payment_id)
    if existing_payment:
        logger.warning(
            f"Duplicate payment webhook ignored: {telegram_payment_id} for user {user_id}"
        )
        # Still send a success message since the user expects confirmation
        await update.message.reply_text(
            "To'lov allaqachon qayta ishlangan. Obunangiz faol.",
            parse_mode=ParseMode.HTML,
        )
        return

    plan = SUBSCRIPTION_PLAN
    days = plan["days"]
    youtube_minutes_limit = plan["youtube_minutes_limit"]
    translation_limit = plan["translation_limit"]

    # Log the payment first (this creates the record for idempotency)
    if not log_payment(
        user_id=user_id,
        telegram_payment_id=telegram_payment_id,
        amount_stars=payment.total_amount,
        plan="premium_30_days",
        days=days,
    ):
        logger.error(f"Failed to log payment {telegram_payment_id} for user {user_id}")
        await update.message.reply_text(S.PAYMENT_LOG_ERROR)
        return

    # Activate premium with limits
    if activate_premium(user_id, days, youtube_minutes_limit, translation_limit):
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0)
            if subscription
            else youtube_minutes_limit
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0)
            if subscription
            else translation_limit
        )

        formatted_date = format_date_uzbek(expires_at)

        await update.message.reply_text(
            S.PAYMENT_SUCCESS_TITLE
            + S.PAYMENT_SUBSCRIPTION_ACTIVATED
            + S.PAYMENT_EXPIRES_AT.format(date=formatted_date)
            + S.PAYMENT_YOUR_LIMITS
            + S.PAYMENT_YOUTUBE_MINUTES_FORMAT.format(minutes=youtube_minutes_remaining)
            + S.PAYMENT_TRANSLATIONS_FORMAT.format(count=translation_remaining)
            + S.PAYMENT_THANK_YOU,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(S.ACTIVATION_ERROR)


async def _perform_single_model_translation(
    user_id: int,
    text_input: str = None,
    image_data: bytes = None,
    mime_type: str = "image/jpeg",
) -> tuple[str, int, int, int]:
    """
    Performs OCR, language detection, and translation using a single Gemini model call.

    Returns:
        Tuple of (translated_text, total_token_count, input_tokens, output_tokens)
    """
    # Build prompt based on what content we have
    if image_data and text_input:
        system_prompt = PROMPTS["translation"]["text_with_image"]
    elif image_data:
        system_prompt = PROMPTS["translation"]["image_only"]
    else:
        system_prompt = PROMPTS["translation"]["text_only"]

    content = []
    if image_data:
        # Use the new API to create image part from bytes
        image_part = types.Part.from_bytes(data=image_data, mime_type=mime_type)
        content.append(image_part)

    prompt_with_text = system_prompt
    if text_input:
        prompt_with_text += f'\n\nHere is the text input to use: """{text_input}"""'
    else:
        prompt_with_text += (
            "\n\nThere is no separate text input, process the image only."
        )
    content.append(prompt_with_text)

    try:
        # Use asyncio.to_thread (Python 3.9+) instead of deprecated get_event_loop()
        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=GEMINI_MODEL_NAME,
            contents=content,
        )

        # Extract token counts from response metadata
        token_count = 0
        input_tokens = 0
        output_tokens = 0
        if response.usage_metadata:
            token_count = response.usage_metadata.total_token_count or 0
            input_tokens = response.usage_metadata.prompt_token_count or 0
            output_tokens = response.usage_metadata.candidates_token_count or 0

        # Debug logging for response analysis
        logger.debug(
            f"Gemini response length: {len(response.text)} chars, tokens: {token_count} (in:{input_tokens}/out:{output_tokens})"
        )
        logger.debug(f"Response preview: {response.text[:200]}...")

        return response.text.strip(), token_count, input_tokens, output_tokens

    except Exception as e:
        log_error_with_context(
            e, context_info={"operation": "single_model_translation"}, user_id=user_id
        )
        return S.GENERIC_ERROR, 0, 0, 0


async def translate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle message translation for various message types with rate limiting.
    (This version is simplified to use a single-model pipeline).
    """
    if not update.effective_user:
        logger.warning("Received update without effective_user, skipping translation")
        return

    user_id = update.effective_user.id
    message = update.message

    # Show status message immediately so user knows bot is processing
    status_message = await message.reply_text(GENERAL_ERRORS.PROCESSING)

    allowed, error_message = user_manager.check_rate_limit(user_id)
    if not allowed:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=error_message,
        )
        return

    # Check limits based on subscription tier
    is_premium = is_user_premium(user_id)

    if is_premium:
        # Premium users: check remaining total limits
        remaining = get_user_remaining_limits(user_id)
        if remaining["translation_remaining"] <= 0:
            plan = SUBSCRIPTION_PLAN
            button_text = f"{S.BTN_INCREASE_LIMIT} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.TRANSLATION_LIMIT_EXCEEDED_PREMIUM.format(
                    stars=plan["stars"],
                    translation_limit=plan["translation_limit"],
                    youtube_limit=plan["youtube_minutes_limit"],
                    days=plan["days"],
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
    else:
        # Free users: check remaining limits
        subscription = get_user_subscription(user_id)

        if subscription:
            translation_remaining = subscription.get("translation_remaining", 0)
        else:
            translation_remaining = FREE_TRANSLATION_LIMIT

        if translation_remaining <= 0:
            plan = SUBSCRIPTION_PLAN
            button_text = f"{S.BTN_SUBSCRIBE} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.TRANSLATION_LIMIT_EXCEEDED_FREE.format(
                    free_limit=FREE_TRANSLATION_LIMIT,
                    stars=plan["stars"],
                    translation_limit=plan["translation_limit"],
                    youtube_limit=plan["youtube_minutes_limit"],
                    days=plan["days"],
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return

    text_input = None
    image_data = None
    image_mime_type = "image/jpeg"  # Default for photos

    try:
        # Update status message to show we're now translating
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=GENERAL_ERRORS.TRANSLATING,
        )

        if message.photo or (
            message.document
            and message.document.mime_type
            and message.document.mime_type.startswith("image/")
        ):
            if message.photo:
                file_id = message.photo[-1].file_id
                file_size = message.photo[-1].file_size
                image_mime_type = "image/jpeg"  # Telegram photos are always JPEG
            else:
                file_id = message.document.file_id
                file_size = message.document.file_size
                image_mime_type = (
                    message.document.mime_type
                )  # Use actual MIME type from document

            if file_size and file_size > MAX_IMAGE_SIZE_MB * 1024 * 1024:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=status_message.message_id,
                    text=INPUT_VALIDATION_ERRORS.IMAGE_TOO_LARGE.format(
                        max_size=MAX_IMAGE_SIZE_MB
                    ),
                )
                return

            file = await context.bot.get_file(file_id)
            image_buffer = io.BytesIO()
            await file.download_to_memory(image_buffer)
            image_buffer.seek(0)
            image_data = image_buffer.getvalue()
            text_input = message.caption.strip() if message.caption else ""

        elif message.text:
            text_input = message.text
        elif message.caption and not image_data:
            text_input = message.caption
        else:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=INPUT_VALIDATION_ERRORS.SEND_TEXT_OR_IMAGE,
            )
            return

        if not text_input and not image_data:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=INPUT_VALIDATION_ERRORS.NO_CONTENT,
            )
            return

        (
            translated_text,
            token_count,
            input_tokens,
            output_tokens,
        ) = await _perform_single_model_translation(
            user_id, text_input, image_data, image_mime_type
        )

        # Determine content type for logging
        if image_data and text_input:
            content_type = "image_with_caption"
        elif image_data:
            content_type = "image"
        else:
            content_type = "text"

        # Log token usage to database and update user session
        if token_count > 0:
            log_token_usage_to_db(
                user_id,
                "gemini",
                token_count,
                is_translation=True,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                content_type=content_type,
                content_preview=text_input[:200] if text_input else None,
            )
            user_manager.record_token_usage(user_id, token_count)

        # Decrement translation limit
        if is_premium:
            decrement_translation_limit(user_id)
        else:
            # For free users, ensure they have a subscription record, then decrement
            _ensure_free_user_subscription(user_id)
            decrement_translation_limit(user_id)

        # Format output with appropriate title and separate sections
        formatted_output = _format_translation_output(
            translated_text,
            has_image=bool(image_data),
            has_caption=bool(text_input and image_data),
        )

        # Get stats/subscribe button
        stats_keyboard = _get_stats_button(user_id)

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=formatted_output or GENERAL_ERRORS.GENERIC_ERROR,
            parse_mode=ParseMode.HTML,
            reply_markup=stats_keyboard,
        )

    except Exception as e:
        log_error_with_context(
            e,
            context_info={"operation": "main_translation_handler_single_model"},
            user_id=user_id,
            text_preview=text_input,
        )
        if status_message:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=GENERAL_ERRORS.GENERIC_ERROR,
                parse_mode=ParseMode.HTML,
            )


async def _perform_youtube_summarization(
    user_id: int,
    youtube_url: str,
    transcript_text: str | None = None,
) -> tuple[str, int, int, int, str | None]:
    """
    Summarizes a YouTube video in Uzbek. Tries transcript first, falls back to video.

    Args:
        user_id: Telegram user ID for logging
        youtube_url: Full YouTube URL
        transcript_text: Pre-fetched transcript text (optional, will be fetched if not provided)

    Returns:
        Tuple of (summary_text, total_token_count, input_tokens, output_tokens, transcript_text or None)
    """
    video_id = extract_youtube_video_id(youtube_url)
    source_method = "video"

    # Use provided transcript or try to fetch it
    if transcript_text:
        source_method = "transcript"
        logger.info(
            f"YouTube [{video_id}]: Using pre-fetched transcript ({len(transcript_text)} chars)"
        )
    elif video_id:
        # Try to fetch transcript if not provided
        transcript_text, lang_code = await asyncio.to_thread(
            fetch_youtube_transcript, video_id
        )
        if transcript_text:
            source_method = "transcript"
            logger.info(
                f"YouTube [{video_id}]: Using transcript ({lang_code}, {len(transcript_text)} chars)"
            )

    # Build prompt based on source
    if source_method == "transcript":
        system_prompt = PROMPTS["youtube_summary"]["with_transcript"].format(
            transcript=transcript_text
        )
        content = system_prompt
    else:
        logger.info(f"YouTube [{video_id}]: No transcript available, using video")
        system_prompt = PROMPTS["youtube_summary"]["without_transcript"]
        content = types.Content(
            parts=[
                types.Part(file_data=types.FileData(file_uri=youtube_url)),
                types.Part(text=system_prompt),
            ]
        )

    try:
        # Configure generation
        generate_config = types.GenerateContentConfig(
            temperature=YOUTUBE_TEMPERATURE,
            max_output_tokens=YOUTUBE_MAX_OUTPUT_TOKENS,
        )

        # Use asyncio.to_thread for async execution
        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=GEMINI_MODEL_NAME,
            contents=content,
            config=generate_config,
        )

        # Extract token counts from response metadata
        token_count = 0
        input_tokens = 0
        output_tokens = 0
        if response.usage_metadata:
            token_count = response.usage_metadata.total_token_count or 0
            input_tokens = response.usage_metadata.prompt_token_count or 0
            output_tokens = response.usage_metadata.candidates_token_count or 0

        # Extract text from response, handling thinking models properly
        # Thinking models return parts with 'thought' attribute that should be skipped
        response_text = ""
        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            text_parts = []
            for part in response.candidates[0].content.parts:
                # Skip thought parts from thinking models
                if hasattr(part, "thought") and part.thought:
                    continue
                # Extract text parts only
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
            response_text = "\n".join(text_parts)

        # Fallback to response.text if no parts found
        if not response_text:
            try:
                response_text = response.text or ""
            except Exception:
                response_text = ""

        logger.info(
            f"YouTube summary [{source_method}]: {len(response_text)} chars, {token_count} tokens (in:{input_tokens}/out:{output_tokens})"
        )

        # Debug logging for empty responses
        if not response_text and response.candidates:
            candidate = response.candidates[0]
            logger.warning(
                f"Empty YouTube summary. Finish reason: {candidate.finish_reason}"
            )

        # For videos without transcript, extract the generated transcription from response
        # and remove it from the user-facing output
        extracted_transcript = None
        user_facing_response = response_text.strip()

        if source_method == "video" and "TRANSKRIPSIYA:" in response_text:
            # Parse out the transcription section
            parts = response_text.split("SARLAVHA:", 1)
            if len(parts) == 2:
                # Extract transcription (between TRANSKRIPSIYA: and SARLAVHA:)
                transcript_section = parts[0]
                if "TRANSKRIPSIYA:" in transcript_section:
                    extracted_transcript = transcript_section.split(
                        "TRANSKRIPSIYA:", 1
                    )[1].strip()
                    logger.info(
                        f"YouTube [{video_id}]: Extracted {len(extracted_transcript)} chars of generated transcript"
                    )

                # User-facing response starts from SARLAVHA:
                user_facing_response = "SARLAVHA:" + parts[1]
                user_facing_response = user_facing_response.strip()

            # Use extracted transcript for caching (fallback to None if extraction failed)
            transcript_text = extracted_transcript or transcript_text

        return (
            user_facing_response,
            token_count,
            input_tokens,
            output_tokens,
            transcript_text,
        )

    except Exception as e:
        log_error_with_context(
            e,
            context_info={
                "operation": "youtube_summarization",
                "url": youtube_url,
                "method": source_method,
            },
            user_id=user_id,
        )
        # Check for common errors and provide user-friendly messages
        error_str = str(e).lower()
        if "not found" in error_str or "unavailable" in error_str:
            return (
                YOUTUBE_ERRORS.VIDEO_NOT_FOUND,
                0,
                0,
                0,
                None,
            )
        elif "private" in error_str:
            return (
                YOUTUBE_ERRORS.PRIVATE_VIDEO,
                0,
                0,
                0,
                None,
            )
        elif "age" in error_str or "restricted" in error_str:
            return YOUTUBE_ERRORS.AGE_RESTRICTED, 0, 0, 0, None
        elif "token" in error_str or "1048576" in error_str or "exceeds" in error_str:
            return (
                YOUTUBE_ERRORS.VIDEO_TOO_LONG.format(max_minutes=60),
                0,
                0,
                0,
                None,
            )
        return YOUTUBE_ERRORS.SUMMARY_ERROR, 0, 0, 0, None


async def summarize_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle YouTube video summarization requests.
    Triggered when a message contains a YouTube URL.
    """
    if not update.effective_user:
        logger.warning(
            "Received update without effective_user, skipping YouTube summary"
        )
        return

    user_id = update.effective_user.id
    message = update.message
    text = message.text or message.caption or ""

    # Extract YouTube URL from message
    youtube_url = extract_youtube_url(text)
    if not youtube_url:
        # This shouldn't happen if handler filter is correct, but safety check
        logger.warning(f"No YouTube URL found in message: {text[:100]}")
        return

    # Extract video ID for deduplication
    video_id = extract_youtube_video_id(youtube_url)
    if not video_id:
        await message.reply_text(YOUTUBE_ERRORS.INVALID_URL)
        return

    # Check for duplicate request (webhook retry)
    if _is_youtube_request_duplicate(user_id, video_id):
        logger.info(
            f"Duplicate YouTube request ignored: user={user_id}, video={video_id}"
        )
        return

    # Show status message immediately so user knows bot is processing
    status_message = await message.reply_text(GENERAL_ERRORS.VIDEO_RECEIVED)

    # Check rate limit
    allowed, error_message = user_manager.check_rate_limit(user_id)
    if not allowed:
        _clear_youtube_cache(user_id, video_id)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=error_message,
        )
        return

    # Fetch video metadata to get duration
    metadata = await asyncio.to_thread(fetch_youtube_metadata, video_id)
    if not metadata or "duration" not in metadata:
        _clear_youtube_cache(user_id, video_id)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=YOUTUBE_ERRORS.METADATA_ERROR,
        )
        return

    video_duration_seconds = metadata.get("duration", 0)
    video_duration_minutes = (
        video_duration_seconds + 59
    ) // 60  # Round up to nearest minute

    # Check max duration limit (60 minutes)
    if video_duration_minutes > YOUTUBE_MAX_DURATION_MINUTES:
        _clear_youtube_cache(user_id, video_id)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=YOUTUBE_ERRORS.VIDEO_DURATION_EXCEEDED.format(
                duration=video_duration_minutes,
                max_minutes=YOUTUBE_MAX_DURATION_MINUTES,
            ),
        )
        return

    # Pre-fetch transcript to determine billable minutes
    # Videos without transcripts cost 3x more due to higher API usage
    transcript_text, transcript_lang = await asyncio.to_thread(
        fetch_youtube_transcript, video_id
    )
    has_transcript = transcript_text is not None

    if has_transcript:
        billable_minutes = video_duration_minutes
        logger.debug(
            f"YouTube [{video_id}]: Transcript available, billing {billable_minutes} min"
        )
    else:
        billable_minutes = video_duration_minutes * YOUTUBE_NO_TRANSCRIPT_MULTIPLIER
        logger.debug(
            f"YouTube [{video_id}]: No transcript, billing {billable_minutes} min (3x)"
        )

    # Check limits based on subscription tier
    is_premium = is_user_premium(user_id)
    plan = SUBSCRIPTION_PLAN

    if is_premium:
        # Premium users: check remaining minutes
        remaining = get_user_remaining_limits(user_id)
        youtube_minutes_remaining = remaining["youtube_minutes_remaining"]

        if youtube_minutes_remaining < billable_minutes:
            _clear_youtube_cache(user_id, video_id)

            subscription = get_user_subscription(user_id)
            days_remaining = (
                get_days_remaining(subscription.get("expires_at", ""))
                if subscription
                else "?"
            )

            # Build explanation for 3x cost
            cost_note = ""
            if not has_transcript:
                cost_note = S.NO_TRANSCRIPT_COST_NOTE.format(
                    multiplier=YOUTUBE_NO_TRANSCRIPT_MULTIPLIER
                )

            button_text = f"{S.BTN_INCREASE_LIMIT} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.YOUTUBE_LIMIT_EXCEEDED_PREMIUM.format(
                    duration=video_duration_minutes,
                    billable=billable_minutes,
                    cost_note=cost_note,
                    remaining=youtube_minutes_remaining,
                    days_left=days_remaining,
                    stars=plan["stars"],
                    youtube_limit=plan["youtube_minutes_limit"],
                    translation_limit=plan["translation_limit"],
                    days=plan["days"],
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
    else:
        # Free users: check remaining minutes from subscription (30 min/month)
        subscription = get_user_subscription(user_id)

        if subscription:
            youtube_minutes_remaining = subscription.get("youtube_minutes_remaining", 0)
        else:
            # New free user - give them the free limit
            youtube_minutes_remaining = FREE_YOUTUBE_MINUTES_LIMIT

        if youtube_minutes_remaining < billable_minutes:
            _clear_youtube_cache(user_id, video_id)

            days_remaining = (
                get_days_remaining(subscription.get("expires_at", ""))
                if subscription
                else 30
            )

            # Build explanation for 3x cost
            cost_note = ""
            if not has_transcript:
                cost_note = S.NO_TRANSCRIPT_COST_NOTE.format(
                    multiplier=YOUTUBE_NO_TRANSCRIPT_MULTIPLIER
                )

            button_text = f"{S.BTN_SUBSCRIBE} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.YOUTUBE_LIMIT_EXCEEDED_FREE.format(
                    duration=video_duration_minutes,
                    billable=billable_minutes,
                    cost_note=cost_note,
                    remaining=youtube_minutes_remaining,
                    days_left=days_remaining,
                    free_limit=FREE_YOUTUBE_MINUTES_LIMIT,
                    stars=plan["stars"],
                    youtube_limit=plan["youtube_minutes_limit"],
                    translation_limit=plan["translation_limit"],
                    days=plan["days"],
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return

    # Update status message to show we're now summarizing
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=status_message.message_id,
        text=GENERAL_ERRORS.PREPARING_SUMMARY,
    )

    try:
        # Pass the already-fetched transcript to avoid re-fetching
        # returned_transcript may contain extracted transcript for no-transcript videos
        (
            summary,
            token_count,
            input_tokens,
            output_tokens,
            returned_transcript,
        ) = await _perform_youtube_summarization(user_id, youtube_url, transcript_text)

        # Use returned transcript (may be extracted from Gemini response for no-transcript videos)
        transcript_text = returned_transcript or transcript_text

        # Log token usage and get the request ID for linking followups
        request_id = None
        if token_count > 0:
            request_id = log_token_usage_to_db(
                user_id,
                "gemini_youtube",
                token_count,
                is_translation=False,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                content_type="youtube",
                content_preview=youtube_url,
                video_duration_minutes=billable_minutes,
            )
            user_manager.record_token_usage(user_id, token_count)

        # Decrement billable minutes (includes 3x multiplier for no-transcript videos)
        if is_premium:
            decrement_youtube_minutes(user_id, billable_minutes)
        else:
            # For free users, we need to track their usage too
            # First ensure they have a subscription record, then decrement
            _ensure_free_user_subscription(user_id)
            decrement_youtube_minutes(user_id, billable_minutes)

        # Format output using HTML (same as translation output)
        formatted_output, questions = _format_youtube_output(summary)

        # Create inline keyboard with question buttons + stats button
        buttons = []
        if questions:
            for i, question in enumerate(questions[:3]):  # Max 3 questions
                # Store video URL and question index in callback data
                callback_data = json.dumps({"u": youtube_url, "q": i})
                # Truncate question text for button
                if len(question) > QUESTION_BUTTON_MAX_LENGTH:
                    button_text = question[: QUESTION_BUTTON_MAX_LENGTH - 3] + "..."
                else:
                    button_text = question
                buttons.append(
                    [InlineKeyboardButton(button_text, callback_data=callback_data)]
                )

        # Add stats/subscribe button at the bottom
        stats_button_text = (
            FORMATTING_LABELS.STATS_BUTTON
            if is_premium
            else FORMATTING_LABELS.SUBSCRIBE_BUTTON
        )
        buttons.append(
            [InlineKeyboardButton(stats_button_text, callback_data="stats_show")]
        )
        keyboard = InlineKeyboardMarkup(buttons)

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=formatted_output,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

        # Store questions, transcript, summary, and request ID in context for callback handler
        if questions:
            context.chat_data[f"yt_questions_{youtube_url}"] = questions
        if transcript_text:
            context.chat_data[f"yt_transcript_{youtube_url}"] = transcript_text
        # Always store summary for followups (especially useful for no-transcript videos)
        context.chat_data[f"yt_summary_{youtube_url}"] = summary
        if request_id:
            context.chat_data[f"yt_request_id_{youtube_url}"] = request_id

    except Exception as e:
        log_error_with_context(
            e,
            context_info={"operation": "youtube_handler", "url": youtube_url},
            user_id=user_id,
        )
        if status_message:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=YOUTUBE_ERRORS.SUMMARY_ERROR,
                parse_mode=ParseMode.HTML,
            )
    finally:
        # Clear deduplication cache
        _clear_youtube_cache(user_id, video_id)


async def _perform_youtube_followup(
    user_id: int,
    youtube_url: str,
    question: str,
    transcript_text: str | None = None,
    summary_text: str | None = None,
) -> tuple[str, int, int, int]:
    """
    Answer a follow-up question about a YouTube video.
    Uses transcript if available, falls back to summary, then to video.

    Args:
        user_id: Telegram user ID for logging
        youtube_url: Full YouTube URL
        question: The follow-up question to answer
        transcript_text: Optional transcript text (if available from summarization)
        summary_text: Optional summary text (cached from initial summarization)

    Returns:
        Tuple of (answer_text, total_token_count, input_tokens, output_tokens)
    """
    video_id = extract_youtube_video_id(youtube_url)
    source_method = "video"

    # Try to use transcript if provided, or fetch it
    if transcript_text:
        source_method = "transcript"
    elif video_id:
        # Try to fetch transcript if not provided
        transcript_text, _ = await asyncio.to_thread(fetch_youtube_transcript, video_id)
        if transcript_text:
            source_method = "transcript"

    # If no transcript, try to use cached summary instead of re-processing video
    if source_method == "video" and summary_text:
        source_method = "summary"

    # Build prompt based on source
    if source_method == "transcript":
        system_prompt = PROMPTS["youtube_followup"]["with_transcript"].format(
            question=question, transcript=transcript_text
        )
        content = system_prompt
        logger.info(f"YouTube follow-up [{video_id}]: Using transcript")
    elif source_method == "summary":
        system_prompt = PROMPTS["youtube_followup"]["with_summary"].format(
            question=question, summary=summary_text
        )
        content = system_prompt
        logger.info(f"YouTube follow-up [{video_id}]: Using cached summary")
    else:
        system_prompt = PROMPTS["youtube_followup"]["without_transcript"].format(
            question=question
        )
        content = types.Content(
            parts=[
                types.Part(file_data=types.FileData(file_uri=youtube_url)),
                types.Part(text=system_prompt),
            ]
        )
        logger.info(
            f"YouTube follow-up [{video_id}]: Using video (no transcript or summary)"
        )

    try:
        # Configure generation - use lower token limit for concise answers
        generate_config = types.GenerateContentConfig(
            temperature=YOUTUBE_TEMPERATURE,
            max_output_tokens=YOUTUBE_FOLLOWUP_MAX_TOKENS,
        )

        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=GEMINI_MODEL_NAME,
            contents=content,
            config=generate_config,
        )

        token_count = 0
        input_tokens = 0
        output_tokens = 0
        if response.usage_metadata:
            token_count = response.usage_metadata.total_token_count or 0
            input_tokens = response.usage_metadata.prompt_token_count or 0
            output_tokens = response.usage_metadata.candidates_token_count or 0

        # Extract text from response, handling thinking models properly
        # Thinking models return parts with 'thought' attribute that should be skipped
        response_text = ""
        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            text_parts = []
            for part in response.candidates[0].content.parts:
                # Skip thought parts from thinking models
                if hasattr(part, "thought") and part.thought:
                    continue
                # Extract text parts only
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
            response_text = "\n".join(text_parts)

        # Fallback to response.text if no parts found
        if not response_text:
            try:
                response_text = response.text or ""
            except Exception:
                response_text = ""

        # Log for debugging
        logger.info(
            f"YouTube follow-up [{source_method}]: {len(response_text)} chars, {token_count} tokens (in:{input_tokens}/out:{output_tokens})"
        )
        if not response_text and response.candidates:
            # Debug: log what parts we actually got
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                parts_info = []
                for part in candidate.content.parts:
                    part_type = type(part).__name__
                    has_text = hasattr(part, "text") and part.text
                    has_thought = hasattr(part, "thought") and part.thought
                    parts_info.append(
                        f"{part_type}(text={has_text}, thought={has_thought})"
                    )
                logger.warning(f"Empty response text. Parts received: {parts_info}")
            else:
                logger.warning(
                    f"Empty response text. Content or parts is None. "
                    f"Finish reason: {candidate.finish_reason}"
                )

        return response_text.strip(), token_count, input_tokens, output_tokens

    except Exception as e:
        log_error_with_context(
            e,
            context_info={
                "operation": "youtube_followup",
                "url": youtube_url,
                "method": source_method,
            },
            user_id=user_id,
        )
        return YOUTUBE_ERRORS.FOLLOWUP_ERROR, 0, 0, 0


async def handle_youtube_question_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle callback when user clicks a follow-up question button.
    """
    query = update.callback_query
    await query.answer()  # Acknowledge the callback

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    try:
        # Parse callback data
        data = json.loads(query.data)
        youtube_url = data.get("u")
        question_index = data.get("q")

        if not youtube_url or question_index is None:
            await query.message.reply_text(GENERAL_ERRORS.INVALID_CALLBACK_DATA)
            return

        # Get the stored question
        questions_key = f"yt_questions_{youtube_url}"
        questions = context.chat_data.get(questions_key, [])

        if question_index >= len(questions):
            await query.message.reply_text(YOUTUBE_ERRORS.QUESTION_NOT_FOUND)
            return

        # Check if this question was already answered
        answered_key = f"yt_answered_{youtube_url}"
        answered_questions = context.chat_data.get(answered_key, set())
        if question_index in answered_questions:
            await query.answer(YOUTUBE_ERRORS.QUESTION_ALREADY_ANSWERED)
            return

        question = questions[question_index]

        # Get the stored transcript, summary, and parent request ID (if available)
        transcript_key = f"yt_transcript_{youtube_url}"
        transcript_text = context.chat_data.get(transcript_key)
        summary_text = context.chat_data.get(f"yt_summary_{youtube_url}")
        parent_request_id = context.chat_data.get(f"yt_request_id_{youtube_url}")

        # Check rate limit
        allowed, error_message = user_manager.check_rate_limit(user_id)
        if not allowed:
            await query.message.reply_text(error_message)
            return

        # Send "thinking" message with the question
        status_message = await query.message.reply_text(
            f"<b>Savol:</b> {_escape_html(question)}\n\n<i>Javob tayyorlanmoqda...</i>",
            parse_mode=ParseMode.HTML,
        )

        # Get the answer (pass transcript and summary if available)
        (
            answer,
            token_count,
            input_tokens,
            output_tokens,
        ) = await _perform_youtube_followup(
            user_id, youtube_url, question, transcript_text, summary_text
        )

        # Log token usage with parent request ID for cost aggregation
        if token_count > 0:
            log_token_usage_to_db(
                user_id,
                "gemini_youtube",
                token_count,
                is_translation=False,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                content_type="youtube_followup",
                content_preview=youtube_url,
                parent_request_id=parent_request_id,
            )
            user_manager.record_token_usage(user_id, token_count)

        # Mark this question as answered
        answered_questions.add(question_index)
        context.chat_data[answered_key] = answered_questions

        # Update the original message keyboard to show checkmark on answered question
        new_buttons = []
        for i, q in enumerate(questions[:3]):
            callback_data = json.dumps({"u": youtube_url, "q": i})
            if i in answered_questions:
                # Add checkmark to answered questions
                if len(q) > QUESTION_BUTTON_MAX_LENGTH - 5:
                    button_text = "✓ " + q[: QUESTION_BUTTON_MAX_LENGTH - 5] + "..."
                else:
                    button_text = "✓ " + q
            else:
                if len(q) > QUESTION_BUTTON_MAX_LENGTH:
                    button_text = q[: QUESTION_BUTTON_MAX_LENGTH - 3] + "..."
                else:
                    button_text = q
            new_buttons.append(
                [InlineKeyboardButton(button_text, callback_data=callback_data)]
            )

        # Add stats/subscribe button at the bottom (same as original)
        is_premium = is_user_premium(user_id)
        stats_button_text = (
            FORMATTING_LABELS.STATS_BUTTON
            if is_premium
            else FORMATTING_LABELS.SUBSCRIBE_BUTTON
        )
        new_buttons.append(
            [InlineKeyboardButton(stats_button_text, callback_data="stats_show")]
        )
        new_keyboard = InlineKeyboardMarkup(new_buttons)

        # Update the original summary message with new keyboard
        try:
            await query.message.edit_reply_markup(reply_markup=new_keyboard)
        except Exception as e:
            logger.debug(f"Could not update keyboard: {e}")

        # Format and send response
        formatted_output = f"<b>Savol:</b> {_escape_html(question)}\n\n<b>Javob:</b>\n{_escape_html(answer)}"

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=formatted_output,
            parse_mode=ParseMode.HTML,
        )

    except json.JSONDecodeError:
        logger.error(f"Invalid callback data: {query.data}")
        await query.message.reply_text(YOUTUBE_ERRORS.INVALID_CALLBACK_DATA)
    except Exception as e:
        log_error_with_context(
            e,
            context_info={"operation": "youtube_question_callback"},
            user_id=user_id,
        )
        await query.message.reply_text(YOUTUBE_ERRORS.FOLLOWUP_ERROR)
