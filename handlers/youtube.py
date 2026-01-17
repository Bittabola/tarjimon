"""YouTube summarization handlers for the Tarjimon bot."""

from __future__ import annotations

import re
import json
import time
import asyncio
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import httpx

from config import (
    logger,
    GEMINI_MODEL_NAME,
    YOUTUBE_MAX_OUTPUT_TOKENS,
    YOUTUBE_FOLLOWUP_MAX_TOKENS,
    YOUTUBE_TEMPERATURE,
    YOUTUBE_MAX_DURATION_MINUTES,
    YOUTUBE_NO_TRANSCRIPT_MULTIPLIER,
    YOUTUBE_CACHE_TTL_SECONDS,
    QUESTION_BUTTON_MAX_LENGTH,
    FREE_YOUTUBE_MINUTES_LIMIT,
    SUBSCRIPTION_PLAN,
    SUPADATA_API_KEY,
    get_days_remaining,
    PROMPTS,
)
from database import (
    log_token_usage_to_db,
    is_user_premium,
    get_user_subscription,
    get_user_remaining_limits,
    decrement_youtube_minutes,
)
from user_management import user_manager
import strings as S
from errors import (
    YOUTUBE_ERRORS,
    GENERAL_ERRORS,
    FORMATTING_LABELS,
)
from constants import (
    RETRY_CONSTANTS,
    API_TIMEOUTS,
)
from utils import retry_async
from google.genai import types

from .common import (
    get_gemini_client,
    escape_html,
    extract_youtube_video_id,
    extract_youtube_url,
    log_error_with_context,
    ensure_free_user_sub,
    extract_gemini_response_text,
    safe_edit_message_text,
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


def cleanup_youtube_cache() -> int:
    """
    Clean expired entries from the YouTube deduplication cache.

    Called periodically by the background cleanup task to prevent memory growth
    when no requests are coming in.

    Returns:
        Number of expired entries removed
    """
    now = time.time()
    removed = 0

    with _youtube_cache_lock:
        expired_keys = [
            k
            for k, v in _youtube_processing_cache.items()
            if now - v > YOUTUBE_CACHE_TTL_SECONDS
        ]
        for k in expired_keys:
            del _youtube_processing_cache[k]
            removed += 1

    if removed > 0:
        logger.debug(f"YouTube cache cleanup: removed {removed} expired entries")

    return removed


async def _check_youtube_limits(
    user_id: int,
    video_id: str,
    video_duration_minutes: int,
    billable_minutes: int,
    has_transcript: bool,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    status_message_id: int,
) -> tuple[bool, bool]:
    """
    Check if user has sufficient YouTube minutes remaining.

    Handles both premium and free user limit checking, displays appropriate
    error messages with subscription prompts when limits are exceeded.

    Args:
        user_id: Telegram user ID
        video_id: YouTube video ID (for cache cleanup)
        video_duration_minutes: Actual video duration
        billable_minutes: Minutes to charge (may include multiplier)
        has_transcript: Whether transcript is available
        context: Telegram bot context
        chat_id: Chat ID for message editing
        status_message_id: Message ID to edit with error

    Returns:
        Tuple of (has_sufficient_limit, is_premium_user)
    """
    is_premium = is_user_premium(user_id)
    plan = SUBSCRIPTION_PLAN

    # Build cost explanation for 3x multiplier
    cost_explanation = ""
    if not has_transcript:
        cost_explanation = S.NO_TRANSCRIPT_COST_NOTE.format(
            multiplier=YOUTUBE_NO_TRANSCRIPT_MULTIPLIER
        )

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

            button_text = f"{S.BTN_INCREASE_LIMIT} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=S.YOUTUBE_LIMIT_EXCEEDED_PREMIUM.format(
                    duration=video_duration_minutes,
                    billable=billable_minutes,
                    cost_note=cost_explanation,
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
            return False, True
    else:
        # Free users: check remaining minutes from subscription
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

            button_text = f"{S.BTN_SUBSCRIBE} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=S.YOUTUBE_LIMIT_EXCEEDED_FREE.format(
                    duration=video_duration_minutes,
                    billable=billable_minutes,
                    cost_note=cost_explanation,
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
            return False, False

    return True, is_premium


@retry_async(
    max_attempts=RETRY_CONSTANTS.MAX_ATTEMPTS,
    delay_seconds=RETRY_CONSTANTS.INITIAL_DELAY_SECONDS,
    backoff_multiplier=RETRY_CONSTANTS.BACKOFF_MULTIPLIER,
    max_delay_seconds=RETRY_CONSTANTS.MAX_DELAY_SECONDS,
    exceptions=(httpx.TimeoutException, httpx.ConnectError),
)
async def fetch_youtube_metadata(video_id: str) -> dict | None:
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
        async with httpx.AsyncClient(timeout=API_TIMEOUTS.SUPADATA_METADATA) as client:
            response = await client.get(
                "https://api.supadata.ai/v1/youtube/video",
                params={"id": video_id},
                headers={"x-api-key": SUPADATA_API_KEY},
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

    except (httpx.TimeoutException, httpx.ConnectError):
        # Let retry decorator handle these
        raise
    except httpx.HTTPError as e:
        logger.warning(f"Supadata metadata API request error for {video_id}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Could not fetch metadata for {video_id}: {e}")
        return None


@retry_async(
    max_attempts=RETRY_CONSTANTS.MAX_ATTEMPTS,
    delay_seconds=RETRY_CONSTANTS.INITIAL_DELAY_SECONDS,
    backoff_multiplier=RETRY_CONSTANTS.BACKOFF_MULTIPLIER,
    max_delay_seconds=RETRY_CONSTANTS.MAX_DELAY_SECONDS,
    exceptions=(httpx.TimeoutException, httpx.ConnectError),
)
async def fetch_youtube_transcript(video_id: str) -> tuple[str | None, str | None]:
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
        async with httpx.AsyncClient(
            timeout=API_TIMEOUTS.SUPADATA_TRANSCRIPT
        ) as client:
            response = await client.get(
                "https://api.supadata.ai/v1/transcript",
                params={
                    "url": youtube_url,
                    "mode": "native",  # Only fetch existing captions, don't generate
                    "text": "true",  # Return plain text instead of timestamped chunks
                },
                headers={"x-api-key": SUPADATA_API_KEY},
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

    except (httpx.TimeoutException, httpx.ConnectError):
        # Let retry decorator handle these
        raise
    except httpx.HTTPError as e:
        logger.warning(f"Supadata API request error for {video_id}: {e}")
        return None, None
    except Exception as e:
        logger.debug(f"Could not fetch transcript for {video_id}: {e}")
        return None, None


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
            output_parts.append(f"<b>{escape_html(title)}</b>")

        # Add summary section
        if summary:
            output_parts.append(
                f"\n{FORMATTING_LABELS.SUMMARY_SECTION}{escape_html(summary)}"
            )

        # Add key points section
        if points:
            output_parts.append(
                f"\n{FORMATTING_LABELS.KEY_POINTS_SECTION}{escape_html(points)}"
            )

        if output_parts:
            return "\n".join(output_parts), questions

        # Fallback if parsing fails
        return FORMATTING_LABELS.VIDEO_SUMMARY + escape_html(response), []

    except Exception as e:
        logger.error(f"Failed to format YouTube output: {e}")
        return FORMATTING_LABELS.VIDEO_SUMMARY + escape_html(response), []


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
        transcript_text, lang_code = await fetch_youtube_transcript(video_id)
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
        response_text = extract_gemini_response_text(response)

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
            text_preview=youtube_url,
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
        transcript_text, _ = await fetch_youtube_transcript(video_id)
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
        response_text = extract_gemini_response_text(response)

        # Log for debugging
        logger.info(
            f"YouTube follow-up [{source_method}]: {len(response_text)} chars, {token_count} tokens (in:{input_tokens}/out:{output_tokens})"
        )
        if not response_text and response.candidates:
            candidate = response.candidates[0]
            logger.warning(
                f"Empty YouTube follow-up response. Finish reason: {candidate.finish_reason}"
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
            text_preview=youtube_url,
        )
        return YOUTUBE_ERRORS.FOLLOWUP_ERROR, 0, 0, 0


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
    metadata = await fetch_youtube_metadata(video_id)
    if not metadata or "duration" not in metadata:
        _clear_youtube_cache(user_id, video_id)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=YOUTUBE_ERRORS.METADATA_ERROR,
        )
        return

    # Check if video is a live stream (not supported)
    is_live = (
        metadata.get("isLive", False) or metadata.get("liveBroadcastContent") == "live"
    )
    if is_live:
        _clear_youtube_cache(user_id, video_id)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=YOUTUBE_ERRORS.LIVE_VIDEO,
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
    transcript_text, transcript_lang = await fetch_youtube_transcript(video_id)
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
    has_limit, is_premium = await _check_youtube_limits(
        user_id=user_id,
        video_id=video_id,
        video_duration_minutes=video_duration_minutes,
        billable_minutes=billable_minutes,
        has_transcript=has_transcript,
        context=context,
        chat_id=update.effective_chat.id,
        status_message_id=status_message.message_id,
    )
    if not has_limit:
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
            ensure_free_user_sub(user_id)
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

        await safe_edit_message_text(
            context=context,
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=formatted_output,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            fallback_reply=message,
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
            text_preview=youtube_url,
        )
        if status_message:
            await safe_edit_message_text(
                context=context,
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=YOUTUBE_ERRORS.SUMMARY_ERROR,
                parse_mode=ParseMode.HTML,
                fallback_reply=message,
            )
    finally:
        # Clear deduplication cache
        _clear_youtube_cache(user_id, video_id)


async def handle_youtube_question_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle callback when user clicks a follow-up question button.
    """
    query = update.callback_query

    # Acknowledge the callback immediately to prevent timeout errors
    # If this fails (query too old), we still proceed with the request
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Could not answer callback query (likely expired): {e}")

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
            try:
                await query.answer(YOUTUBE_ERRORS.QUESTION_ALREADY_ANSWERED)
            except Exception:
                pass  # Callback may have expired
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
            f"{S.LABEL_QUESTION} {escape_html(question)}\n\n<i>{S.PREPARING_ANSWER}</i>",
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
        formatted_output = f"{S.LABEL_QUESTION} {escape_html(question)}\n\n{S.LABEL_ANSWER}\n{escape_html(answer)}"

        await safe_edit_message_text(
            context=context,
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=formatted_output,
            parse_mode=ParseMode.HTML,
            fallback_reply=query.message,
        )

    except json.JSONDecodeError:
        logger.error(f"Invalid callback data: {query.data}")
        await query.message.reply_text(YOUTUBE_ERRORS.INVALID_CALLBACK_DATA)
    except Exception as e:
        log_error_with_context(
            e,
            context_info={"operation": "youtube_question_callback"},
            user_id=user_id,
            text_preview=locals().get("youtube_url"),
        )
        await query.message.reply_text(YOUTUBE_ERRORS.FOLLOWUP_ERROR)
