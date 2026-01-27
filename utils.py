"""Utility functions for the Tarjimon bot.

This module provides common utility functions used across the application,
including HTML escaping, input validation, and retry decorators.
"""

import html
import re
import functools
import asyncio
from collections.abc import Callable
from typing import TypeVar, ParamSpec
from config import logger
import strings as S

# Type variables for generic retry decorator
P = ParamSpec("P")
T = TypeVar("T")

# YouTube URL regex pattern - matches various YouTube URL formats
YOUTUBE_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})"
)


def safe_html(text: str, max_length: int | None = None) -> str:
    """
    Escape HTML special characters and optionally truncate text.

    Args:
        text: The text to escape
        max_length: Optional maximum length (truncates with "..." if exceeded)

    Returns:
        HTML-escaped and optionally truncated text
    """
    if not text:
        return ""

    escaped = html.escape(text)

    if max_length and len(escaped) > max_length:
        # Truncate and add ellipsis, ensuring we don't cut in the middle of an HTML entity
        truncated = escaped[: max_length - 3]
        # Check if we cut in the middle of an entity like &amp;
        last_amp = truncated.rfind("&")
        if last_amp != -1 and ";" not in truncated[last_amp:]:
            truncated = truncated[:last_amp]
        return truncated + "..."

    return escaped


def validate_youtube_url(url: str) -> str | None:
    """
    Validate and extract video ID from a YouTube URL.

    Args:
        url: The URL to validate

    Returns:
        Video ID if valid YouTube URL, None otherwise
    """
    if not url:
        return None

    # Strip whitespace
    url = url.strip()

    # Check against pattern
    match = YOUTUBE_URL_PATTERN.search(url)
    if match:
        return match.group(1)

    return None


def extract_youtube_url(text: str) -> str | None:
    """
    Extract YouTube URL from text if present.

    Args:
        text: Input text that may contain a YouTube URL

    Returns:
        Full YouTube URL if found, None otherwise
    """
    if not text:
        return None

    video_id = validate_youtube_url(text)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"

    return None


def validate_text_input(
    text: str, max_length: int = 50000, min_length: int = 1
) -> tuple[bool, str | None]:
    """
    Validate text input for processing.

    Args:
        text: The text to validate
        max_length: Maximum allowed length
        min_length: Minimum required length

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not text:
        return False, S.EMPTY_TEXT

    if len(text) < min_length:
        return False, S.TEXT_TOO_SHORT.format(min_length=min_length)

    if len(text) > max_length:
        return False, S.TEXT_TOO_LONG.format(actual=len(text), limit=max_length)

    return True, None


def validate_image_size(
    file_size: int, max_size_mb: int = 10
) -> tuple[bool, str | None]:
    """
    Validate image file size.

    Args:
        file_size: File size in bytes
        max_size_mb: Maximum allowed size in megabytes

    Returns:
        Tuple of (is_valid, error_message)
    """
    max_size_bytes = max_size_mb * 1024 * 1024

    if file_size > max_size_bytes:
        return False, S.IMAGE_TOO_LARGE.format(max_size=max_size_mb)

    return True, None


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to specified length with suffix.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: String to append when truncating

    Returns:
        Truncated text with suffix if needed
    """
    if not text or len(text) <= max_length:
        return text

    return text[: max_length - len(suffix)] + suffix


def retry_async(
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    backoff_multiplier: float = 2.0,
    max_delay_seconds: float = 30.0,
    exceptions: tuple = (Exception,),
    on_retry: Callable[[Exception, int], None] | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator for retrying async functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        delay_seconds: Initial delay between retries
        backoff_multiplier: Multiplier for delay after each retry
        max_delay_seconds: Maximum delay between retries
        exceptions: Tuple of exception types to catch and retry
        on_retry: Optional callback function called on each retry

    Returns:
        Decorated function with retry logic

    Example:
        @retry_async(max_attempts=3, delay_seconds=1.0)
        async def call_api():
            return await some_api_call()
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exception = None
            current_delay = delay_seconds

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    if on_retry:
                        on_retry(e, attempt)

                    logger.warning(
                        f"Function {func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {current_delay:.1f}s..."
                    )

                    await asyncio.sleep(current_delay)
                    current_delay = min(
                        current_delay * backoff_multiplier, max_delay_seconds
                    )

            # This should never be reached, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Unexpected state in retry logic for {func.__name__}")

        return wrapper

    return decorator


def retry_sync(
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    backoff_multiplier: float = 2.0,
    max_delay_seconds: float = 30.0,
    exceptions: tuple = (Exception,),
    on_retry: Callable[[Exception, int], None] | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator for retrying synchronous functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        delay_seconds: Initial delay between retries
        backoff_multiplier: Multiplier for delay after each retry
        max_delay_seconds: Maximum delay between retries
        exceptions: Tuple of exception types to catch and retry
        on_retry: Optional callback function called on each retry

    Returns:
        Decorated function with retry logic
    """
    import time

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exception = None
            current_delay = delay_seconds

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    if on_retry:
                        on_retry(e, attempt)

                    logger.warning(
                        f"Function {func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {current_delay:.1f}s..."
                    )

                    time.sleep(current_delay)
                    current_delay = min(
                        current_delay * backoff_multiplier, max_delay_seconds
                    )

            if last_exception:
                raise last_exception
            raise RuntimeError(f"Unexpected state in retry logic for {func.__name__}")

        return wrapper

    return decorator


def format_number(number: int | float, decimal_places: int = 0) -> str:
    """
    Format a number with thousand separators.

    Args:
        number: The number to format
        decimal_places: Number of decimal places to show

    Returns:
        Formatted number string with thousand separators
    """
    if decimal_places > 0:
        return f"{number:,.{decimal_places}f}"
    return f"{int(number):,}"


def sanitize_callback_data(data: str, max_length: int = 64) -> str:
    """
    Sanitize callback data for Telegram inline buttons.

    Telegram limits callback data to 64 bytes.

    Args:
        data: The callback data string
        max_length: Maximum allowed length

    Returns:
        Sanitized callback data
    """
    if not data:
        return ""

    # Telegram callback data is limited to 64 bytes
    encoded = data.encode("utf-8")
    if len(encoded) <= max_length:
        return data

    # Truncate to fit
    while len(encoded) > max_length:
        data = data[:-1]
        encoded = data.encode("utf-8")

    return data
