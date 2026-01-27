"""Common utilities and helpers for Telegram bot handlers."""

from __future__ import annotations

import threading
import traceback
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import (
    logger,
    GEMINI_API_KEY,
)
from constants import (
    TELEGRAM_CONSTANTS,
    ERROR_LOG_CONSTANTS,
    SUBSCRIPTION_LIMITS,
)
from database import (
    log_error_to_db,
    is_user_premium,
    ensure_free_user_subscription,
)
import strings as S
from google import genai


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


def ensure_free_user_sub(user_id: int) -> None:
    """
    Ensure a free user has a subscription record with initial limits.
    This is called when a free user uses the service for the first time.
    """
    ensure_free_user_subscription(
        user_id,
        youtube_minutes=SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES,
        translations=SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS,
    )


def get_stats_button(user_id: int) -> InlineKeyboardMarkup:
    """
    Get the persistent stats/subscribe button based on user's subscription status.
    Also includes a feedback button.

    Args:
        user_id: Telegram user ID

    Returns:
        InlineKeyboardMarkup with appropriate buttons
    """
    is_premium_user = is_user_premium(user_id)

    if is_premium_user:
        button_text = S.BTN_STATS
    else:
        button_text = S.BTN_SUBSCRIBE

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(button_text, callback_data="stats_show")],
            [InlineKeyboardButton("Admin bilan aloqa", callback_data="feedback_start")],
        ]
    )


def split_message(text: str, max_length: int = None) -> list[str]:
    """
    Split text into multiple parts to fit within Telegram's message length limit.

    Attempts to split at sensible boundaries (paragraphs, sentences, words).

    Args:
        text: The text to split
        max_length: Maximum length per part (defaults to Telegram's 4096 limit minus buffer)

    Returns:
        List of text parts, each fitting within the limit
    """
    if max_length is None:
        # Leave room for continuation indicators and buffer for HTML tags
        max_length = TELEGRAM_CONSTANTS.MAX_MESSAGE_LENGTH - 100

    if len(text) <= max_length:
        return [text]

    parts = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break

        # Find a good break point within the limit
        chunk = remaining[:max_length]
        break_point = max_length

        # Try to break at paragraph
        last_para = chunk.rfind("\n\n")
        if last_para > max_length * 0.5:
            break_point = last_para + 2  # Include the newlines
        else:
            # Try to break at sentence
            for end_char in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
                last_sentence = chunk.rfind(end_char)
                if last_sentence > max_length * 0.5:
                    break_point = last_sentence + len(end_char)
                    break
            else:
                # Try to break at newline
                last_newline = chunk.rfind("\n")
                if last_newline > max_length * 0.5:
                    break_point = last_newline + 1
                else:
                    # Try to break at word
                    last_space = chunk.rfind(" ")
                    if last_space > max_length * 0.5:
                        break_point = last_space + 1

        parts.append(remaining[:break_point].rstrip())
        remaining = remaining[break_point:].lstrip()

    return parts


def extract_gemini_response_text(response) -> str:
    """
    Extract text from a Gemini API response, handling thinking models properly.

    Thinking models return parts with a 'thought' attribute that should be skipped
    to only return the actual response content.

    Args:
        response: The Gemini API response object

    Returns:
        Extracted text content from the response, or empty string if none found
    """
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

    return response_text


async def safe_edit_message_text(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
    fallback_reply: Message | None = None,
) -> bool:
    """
    Safely edit a message, handling common Telegram API errors.

    If the message can't be edited (deleted, too old, etc.), optionally sends
    a new message as fallback. Automatically splits messages that exceed
    Telegram's length limit into multiple messages.

    Args:
        context: Telegram bot context
        chat_id: Chat ID where the message is
        message_id: ID of the message to edit
        text: New text content
        parse_mode: Parse mode for formatting (default: "HTML")
        reply_markup: Optional inline keyboard (only added to last message)
        fallback_reply: If provided, send a new reply to this message on failure

    Returns:
        True if edit succeeded, False if it failed (fallback may have been sent)
    """
    # Split text if it exceeds Telegram's limit
    parts = split_message(text)
    if len(parts) > 1:
        logger.info(
            f"Message split into {len(parts)} parts (original: {len(text)} chars)"
        )

    try:
        # Edit the original message with the first part
        # Only add reply_markup to the last part
        first_markup = reply_markup if len(parts) == 1 else None
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=parts[0],
            parse_mode=parse_mode,
            reply_markup=first_markup,
        )

        # Send remaining parts as new messages
        for i, part in enumerate(parts[1:], start=2):
            # Add reply_markup only to the last message
            part_markup = reply_markup if i == len(parts) else None
            await context.bot.send_message(
                chat_id=chat_id,
                text=part,
                parse_mode=parse_mode,
                reply_markup=part_markup,
            )

        return True
    except BadRequest as e:
        error_msg = str(e).lower()
        # Handle message too long error (shouldn't happen with splitting, but just in case)
        if "message is too long" in error_msg or "message_too_long" in error_msg:
            logger.warning(
                f"Message still too long after splitting ({len(parts[0])} chars), forcing hard truncate"
            )
            hard_truncated = parts[0][:3900] + "\n\n..."
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=hard_truncated,
                    parse_mode=None,  # Disable parse mode to avoid HTML issues
                    reply_markup=reply_markup,
                )
                return True
            except TelegramError as retry_error:
                logger.warning(f"Hard truncate retry also failed: {retry_error}")
                return False
        # These errors mean the message can't be edited anymore
        if any(
            phrase in error_msg
            for phrase in [
                "message to edit not found",
                "message can't be edited",
                "message is not modified",
                "message to delete not found",
            ]
        ):
            logger.debug(f"Message {message_id} can't be edited: {e}")
            # Try to send as new messages if fallback provided
            if fallback_reply:
                try:
                    for i, part in enumerate(parts, start=1):
                        part_markup = reply_markup if i == len(parts) else None
                        await fallback_reply.reply_text(
                            text=part,
                            parse_mode=parse_mode,
                            reply_markup=part_markup,
                        )
                except TelegramError as reply_error:
                    logger.warning(f"Fallback reply also failed: {reply_error}")
            return False
        # Re-raise unexpected BadRequest errors
        raise
    except TelegramError as e:
        logger.warning(f"Telegram error editing message {message_id}: {e}")
        return False


def log_error_with_context(
    error: Exception,
    context_info: dict = None,
    user_id: int = None,
    text_preview: str = None,
):
    """Enhanced error logging with contextual information. Also logs to database."""
    if not ERROR_LOG_CONSTANTS.CONTEXT_ENABLED:
        logger.error(f"Error: {error}")
        return

    error_details = {
        "error_type": type(error).__name__,
        "error_message": str(error),
    }

    if ERROR_LOG_CONSTANTS.INCLUDE_USER_CONTEXT and user_id:
        error_details["user_id"] = user_id

    if text_preview and len(text_preview) > 0:
        preview = text_preview[: ERROR_LOG_CONSTANTS.MAX_TEXT_PREVIEW]
        if len(text_preview) > ERROR_LOG_CONSTANTS.MAX_TEXT_PREVIEW:
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
