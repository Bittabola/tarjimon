"""
Telegram bot handlers for the Tarjimon bot.

This package contains modular handlers split by functionality:
- common: Shared utilities (Gemini client, error logging, helpers)
- translation: Text and image translation handlers
- subscription: Subscription and payment handlers
- feedback: User feedback handlers
"""

from __future__ import annotations

# Re-export common utilities
from .common import (
    get_gemini_client,
    ensure_free_user_sub,
    get_stats_button,
    log_error_with_context,
)

# Re-export translation handlers
from .translation import (
    translate_message,
)

# Re-export subscription handlers
from .subscription import (
    start,
    subscribe,
    handle_subscribe_callback,
    handle_stats_callback,
    pre_checkout_handler,
    successful_payment_handler,
)

# Re-export feedback handlers
from .feedback import (
    aloqa,
    handle_feedback_callback,
    is_user_pending_feedback,
    clear_pending_feedback,
    handle_feedback_message,
)

__all__ = [
    # Common utilities
    "get_gemini_client",
    "ensure_free_user_sub",
    "get_stats_button",
    "log_error_with_context",
    # Translation
    "translate_message",
    # Subscription
    "start",
    "subscribe",
    "handle_subscribe_callback",
    "handle_stats_callback",
    "pre_checkout_handler",
    "successful_payment_handler",
    # Feedback
    "aloqa",
    "handle_feedback_callback",
    "is_user_pending_feedback",
    "clear_pending_feedback",
    "handle_feedback_message",
]
