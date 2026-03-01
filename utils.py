"""Utility functions for the Tarjimon bot.

This module provides common utility functions used across the application,
including HTML escaping and input validation.
"""

import html
import strings as S


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
