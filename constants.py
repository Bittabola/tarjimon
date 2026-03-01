"""Constants module for the Tarjimon bot.

This module centralizes all magic numbers, limits, and configuration constants
used throughout the application for better maintainability.
"""

from dataclasses import dataclass
from typing import Final

import strings as S


@dataclass(frozen=True)
class RateLimits:
    """Rate limiting constants."""

    REQUESTS_PER_MINUTE: Final[int] = 10
    DAILY_TOKENS_PER_USER: Final[int] = 20_000
    MONTHLY_SYSTEM_TOKENS: Final[int] = 5_000_000


@dataclass(frozen=True)
class TextLimits:
    """Text processing limits."""

    MAX_TEXT_LENGTH: Final[int] = 50_000
    MAX_CHUNK_SIZE: Final[int] = 30_000
    SHORT_TEXT_THRESHOLD: Final[int] = 10_000
    MAX_OUTPUT_TOKENS: Final[int] = 4_096
    LONG_TEXT_OUTPUT_TOKENS: Final[int] = 8_192
    MIN_TRANSLATION_LENGTH_RATIO: Final[float] = 0.05
    MAX_TRANSLATION_LENGTH_RATIO: Final[float] = 3.0
    MIN_ORIGINAL_LENGTH_FOR_RATIO_CHECK: Final[int] = 100
    TOKEN_ESTIMATION_RATIO: Final[int] = 4  # 1 token ≈ 4 characters


@dataclass(frozen=True)
class ImageLimits:
    """Image processing limits."""

    MAX_IMAGE_SIZE_MB: Final[int] = 10
    MAX_IMAGE_DIMENSION: Final[int] = 2048
    IMAGE_COMPRESSION_QUALITY: Final[int] = 85
    OCR_MAX_TOKENS: Final[int] = 1500
    OCR_TEMPERATURE: Final[float] = 0.0
    OCR_FALLBACK_TEXT_LENGTH: Final[int] = 500
    UZBEK_DETECTION_THRESHOLD: Final[float] = 0.9


@dataclass(frozen=True)
class SubscriptionLimits:
    """Subscription tier limits."""

    # Free tier
    FREE_TRANSLATIONS: Final[int] = 10
    FREE_PERIOD_DAYS: Final[int] = 30

    # Premium tier
    PREMIUM_TRANSLATIONS: Final[int] = 50
    PREMIUM_PERIOD_DAYS: Final[int] = 30
    PREMIUM_PRICE_STARS: Final[int] = 350


@dataclass(frozen=True)
class SessionConstants:
    """Session management constants."""

    CLEANUP_INTERVAL_SECONDS: Final[int] = 3600  # 1 hour
    TIMEOUT_SECONDS: Final[int] = 7200  # 2 hours
    MAX_INACTIVE_SESSIONS: Final[int] = 1000


@dataclass(frozen=True)
class RetryConstants:
    """Retry logic constants."""

    MAX_ATTEMPTS: Final[int] = 3
    INITIAL_DELAY_SECONDS: Final[float] = 1.0
    BACKOFF_MULTIPLIER: Final[float] = 2.0
    MAX_DELAY_SECONDS: Final[float] = 30.0

    # Translation specific
    MAX_TRANSLATION_RETRIES: Final[int] = 1
    CHUNK_DELAY_SECONDS: Final[float] = 0.5
    RETRY_DELAY_SECONDS: Final[float] = 1.0


@dataclass(frozen=True)
class APITimeouts:
    """API request timeouts in seconds."""

    GEMINI_DEFAULT: Final[int] = 120
    TELEGRAM_FILE_DOWNLOAD: Final[int] = 60
    HEALTH_CHECK: Final[int] = 5


@dataclass(frozen=True)
class PricingConstants:
    """API pricing constants (USD)."""

    # Gemini 2.5 Pro pricing per 1M tokens (as of Feb 2026)
    # https://ai.google.dev/gemini-api/docs/pricing
    GEMINI_INPUT_PRICE_PER_M: Final[float] = 1.25
    GEMINI_OUTPUT_PRICE_PER_M: Final[float] = 10.00  # applies to output + thinking tokens
    GEMINI_INPUT_PRICE_PER_M_LONG: Final[float] = 2.50  # >200k context
    GEMINI_OUTPUT_PRICE_PER_M_LONG: Final[float] = 20.00  # >200k context
    GEMINI_LONG_CONTEXT_THRESHOLD: Final[int] = 200_000

    # Telegram Stars
    STARS_TO_USD: Final[float] = 0.02  # 1 Star ≈ $0.02
    TELEGRAM_FEE_PERCENT: Final[int] = 30


@dataclass(frozen=True)
class ErrorLogConstants:
    """Error logging constants."""

    CONTEXT_ENABLED: Final[bool] = True
    MAX_TRACEBACK_LINES: Final[int] = 10
    MAX_TEXT_PREVIEW: Final[int] = 200
    INCLUDE_USER_CONTEXT: Final[bool] = True
    MAX_ERROR_MESSAGE_LENGTH: Final[int] = 1000
    MAX_CONTENT_PREVIEW_LENGTH: Final[int] = 500
    MAX_STACK_TRACE_LENGTH: Final[int] = 5000


@dataclass(frozen=True)
class DatabaseConstants:
    """Database-related constants."""

    CONNECTION_TIMEOUT: Final[float] = 30.0
    MAX_CONTENT_PREVIEW_LENGTH: Final[int] = 500


@dataclass(frozen=True)
class TelegramConstants:
    """Telegram API constants."""

    MAX_MESSAGE_LENGTH: Final[int] = 4096
    MAX_CALLBACK_DATA_LENGTH: Final[int] = 64
    MAX_BUTTON_TEXT_LENGTH: Final[int] = 64


# Export all constant classes as singletons
RATE_LIMITS = RateLimits()
TEXT_LIMITS = TextLimits()
IMAGE_LIMITS = ImageLimits()
SUBSCRIPTION_LIMITS = SubscriptionLimits()
SESSION_CONSTANTS = SessionConstants()
RETRY_CONSTANTS = RetryConstants()
API_TIMEOUTS = APITimeouts()
PRICING_CONSTANTS = PricingConstants()
ERROR_LOG_CONSTANTS = ErrorLogConstants()
DATABASE_CONSTANTS = DatabaseConstants()
TELEGRAM_CONSTANTS = TelegramConstants()


# Supported image formats
SUPPORTED_IMAGE_FORMATS: Final[list[str]] = ["JPEG", "PNG", "WEBP", "GIF", "BMP"]

# Uzbek month names for date formatting (imported from strings.py)
MONTHS_UZ: Final[list[str]] = S.MONTHS
