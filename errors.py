"""Error messages module for the Tarjimon bot.

This module provides error message classes that import strings from strings.py
for centralized translation management.
"""

from dataclasses import dataclass
from typing import Final

import strings as S


@dataclass(frozen=True)
class RateLimitErrors:
    """Rate limiting error messages."""

    DAILY_TOKEN_LIMIT_EXCEEDED: Final[str] = S.DAILY_TOKEN_LIMIT_EXCEEDED
    TOO_MANY_REQUESTS: Final[str] = S.TOO_MANY_REQUESTS
    MONTHLY_SERVICE_LIMIT: Final[str] = S.MONTHLY_SERVICE_LIMIT
    MONTHLY_SYSTEM_LIMIT: Final[str] = S.MONTHLY_SYSTEM_LIMIT


@dataclass(frozen=True)
class InputValidationErrors:
    """Input validation error messages."""

    TEXT_TOO_LONG: Final[str] = S.TEXT_TOO_LONG
    IMAGE_TOO_LARGE: Final[str] = S.IMAGE_TOO_LARGE
    EMPTY_TEXT: Final[str] = S.EMPTY_TEXT
    TEXT_TOO_SHORT: Final[str] = S.TEXT_TOO_SHORT
    NO_CONTENT: Final[str] = S.NO_CONTENT
    SEND_TEXT_OR_IMAGE: Final[str] = S.SEND_TEXT_OR_IMAGE


@dataclass(frozen=True)
class YouTubeErrors:
    """YouTube-related error messages."""

    INVALID_URL: Final[str] = S.YOUTUBE_INVALID_URL
    LIVE_VIDEO: Final[str] = S.YOUTUBE_LIVE_VIDEO
    VIDEO_NOT_FOUND: Final[str] = S.YOUTUBE_VIDEO_NOT_FOUND
    PRIVATE_VIDEO: Final[str] = S.YOUTUBE_PRIVATE_VIDEO
    AGE_RESTRICTED: Final[str] = S.YOUTUBE_AGE_RESTRICTED
    VIDEO_TOO_LONG: Final[str] = S.YOUTUBE_VIDEO_TOO_LONG
    VIDEO_DURATION_EXCEEDED: Final[str] = S.YOUTUBE_VIDEO_DURATION_EXCEEDED
    METADATA_ERROR: Final[str] = S.YOUTUBE_METADATA_ERROR
    SUMMARY_ERROR: Final[str] = S.YOUTUBE_SUMMARY_ERROR
    QUESTION_NOT_FOUND: Final[str] = S.YOUTUBE_QUESTION_NOT_FOUND
    QUESTION_ALREADY_ANSWERED: Final[str] = S.YOUTUBE_QUESTION_ALREADY_ANSWERED
    FOLLOWUP_ERROR: Final[str] = S.YOUTUBE_FOLLOWUP_ERROR
    INVALID_CALLBACK_DATA: Final[str] = S.YOUTUBE_INVALID_CALLBACK_DATA


@dataclass(frozen=True)
class SubscriptionErrors:
    """Subscription-related error messages."""

    TRANSLATION_LIMIT_EXCEEDED_FREE: Final[str] = S.TRANSLATION_LIMIT_EXCEEDED_FREE
    TRANSLATION_LIMIT_EXCEEDED_PREMIUM: Final[str] = (
        S.TRANSLATION_LIMIT_EXCEEDED_PREMIUM
    )
    YOUTUBE_LIMIT_EXCEEDED_FREE: Final[str] = S.YOUTUBE_LIMIT_EXCEEDED_FREE
    YOUTUBE_LIMIT_EXCEEDED_PREMIUM: Final[str] = S.YOUTUBE_LIMIT_EXCEEDED_PREMIUM
    NO_TRANSCRIPT_COST_NOTE: Final[str] = S.NO_TRANSCRIPT_COST_NOTE
    INVALID_PLAN: Final[str] = S.INVALID_PLAN
    PAYMENT_ALREADY_PROCESSED: Final[str] = S.PAYMENT_ALREADY_PROCESSED
    PAYMENT_LOG_ERROR: Final[str] = S.PAYMENT_LOG_ERROR
    ACTIVATION_ERROR: Final[str] = S.ACTIVATION_ERROR


@dataclass(frozen=True)
class GeneralErrors:
    """General error messages."""

    GENERIC_ERROR: Final[str] = S.GENERIC_ERROR
    PROCESSING: Final[str] = S.PROCESSING
    TRANSLATING: Final[str] = S.TRANSLATING
    PREPARING_SUMMARY: Final[str] = S.PREPARING_SUMMARY
    PREPARING_ANSWER: Final[str] = S.PREPARING_ANSWER
    VIDEO_RECEIVED: Final[str] = S.VIDEO_RECEIVED
    INVALID_CALLBACK_DATA: Final[str] = S.INVALID_CALLBACK_DATA


@dataclass(frozen=True)
class TranslationErrors:
    """Translation-specific error messages."""

    OCR_NO_TEXT: Final[str] = S.OCR_NO_TEXT
    ALREADY_UZBEK: Final[str] = S.ALREADY_UZBEK
    TRANSLATION_FAILED: Final[str] = S.TRANSLATION_FAILED


@dataclass(frozen=True)
class FormattingLabels:
    """UI labels for formatted output."""

    # Image-related labels
    IMAGE_RESULT: Final[str] = S.LABEL_IMAGE_RESULT
    IMAGE_TRANSLATION: Final[str] = S.LABEL_IMAGE_TRANSLATION
    IMAGE_AND_TEXT_TRANSLATION: Final[str] = S.LABEL_IMAGE_AND_TEXT_TRANSLATION

    # Text-related labels
    TEXT_RESULT: Final[str] = S.LABEL_TEXT_RESULT
    TEXT_TRANSLATION: Final[str] = S.LABEL_TEXT_TRANSLATION

    # Generic translation label
    TRANSLATION: Final[str] = S.LABEL_TRANSLATION

    # YouTube labels
    VIDEO_SUMMARY: Final[str] = S.LABEL_VIDEO_SUMMARY
    SUMMARY_SECTION: Final[str] = S.LABEL_SUMMARY_SECTION
    KEY_POINTS_SECTION: Final[str] = S.LABEL_KEY_POINTS

    QUESTION_LABEL: Final[str] = S.LABEL_QUESTION
    ANSWER_LABEL: Final[str] = S.LABEL_ANSWER

    # Button labels
    STATS_BUTTON: Final[str] = S.BTN_STATS
    SUBSCRIBE_BUTTON: Final[str] = S.BTN_SUBSCRIBE
    INCREASE_LIMIT_BUTTON: Final[str] = S.BTN_INCREASE_LIMIT
    SUBSCRIBE_ACTION_BUTTON: Final[str] = S.BTN_SUBSCRIBE


@dataclass(frozen=True)
class PaymentMessages:
    """Payment-related messages."""

    SUCCESS_TITLE: Final[str] = S.PAYMENT_SUCCESS_TITLE
    SUBSCRIPTION_ACTIVATED: Final[str] = S.PAYMENT_SUBSCRIPTION_ACTIVATED
    EXPIRES_AT: Final[str] = S.PAYMENT_EXPIRES_AT
    YOUR_LIMITS: Final[str] = S.PAYMENT_YOUR_LIMITS
    YOUTUBE_MINUTES_FORMAT: Final[str] = S.PAYMENT_YOUTUBE_MINUTES_FORMAT
    TRANSLATIONS_FORMAT: Final[str] = S.PAYMENT_TRANSLATIONS_FORMAT
    THANK_YOU: Final[str] = S.PAYMENT_THANK_YOU


# Export all error message classes as singletons
RATE_LIMIT_ERRORS = RateLimitErrors()
INPUT_VALIDATION_ERRORS = InputValidationErrors()
YOUTUBE_ERRORS = YouTubeErrors()
SUBSCRIPTION_ERRORS = SubscriptionErrors()
GENERAL_ERRORS = GeneralErrors()
TRANSLATION_ERRORS = TranslationErrors()
FORMATTING_LABELS = FormattingLabels()
PAYMENT_MESSAGES = PaymentMessages()
