# AGENTS.md - Tarjimon Bot

This document provides guidance for AI coding agents working on this codebase.

## Project Overview

Tarjimon is a Telegram bot that translates messages into Uzbek and summarizes YouTube videos using Google Gemini. Built with Python 3.12+, FastAPI, and python-telegram-bot.

## Build, Lint, Test Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot (development)
python webhook.py

# Run with Docker
docker compose up -d --build

# Lint with ruff (if installed)
ruff check .
ruff format .

# Type checking (if mypy installed)
mypy .
```

**Note:** This project currently has no test suite. When adding tests, use pytest:
```bash
# Run all tests
pytest

# Run single test file
pytest tests/test_translation.py

# Run single test
pytest tests/test_translation.py::test_function_name -v
```

## Code Style Guidelines

### Imports

Order imports in this sequence with blank lines between groups:
1. `from __future__ import annotations` (if needed)
2. Standard library imports
3. Third-party imports
4. Local imports

```python
from __future__ import annotations

import asyncio
import re
from typing import Final, Optional

from telegram import Update
from telegram.ext import ContextTypes
from google.genai import types

from config import logger, GEMINI_MODEL_NAME
from database import log_token_usage_to_db
import strings as S
from .common import escape_html, get_stats_button
```

**Import conventions:**
- Use `import strings as S` for the strings module
- Use relative imports within packages: `from .common import ...`
- Avoid `from module import *`

### Formatting

- **Line length:** ~100 characters (no hard limit)
- **Quotes:** Double quotes for strings
- **Indentation:** 4 spaces
- **Trailing commas:** Use in multiline structures
- **Formatter:** ruff format (Black-compatible)

### Type Annotations

Use modern Python 3.12+ type syntax:

```python
# Correct
def process(items: list[str], config: dict[str, int]) -> str | None:
    ...

# Use Final for constants
MAX_RETRIES: Final[int] = 3

# Union types with |
def get_days(date: str) -> int | str:
    ...

# Optional parameters
def validate(text: str, max_length: int | None = None) -> bool:
    ...
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Functions | snake_case | `translate_message`, `get_stats_button` |
| Private functions | _snake_case | `_format_translation_output` |
| Variables | snake_case | `user_id`, `token_count` |
| Constants | SCREAMING_SNAKE_CASE | `MAX_TEXT_LENGTH`, `RATE_LIMITS` |
| Classes | PascalCase | `DatabaseManager`, `RateLimits` |
| Module files | snake_case | `user_management.py`, `admin_dashboard.py` |

### Constants Organization

Group related constants in frozen dataclasses:

```python
from dataclasses import dataclass
from typing import Final

@dataclass(frozen=True)
class RateLimits:
    """Rate limiting constants."""
    REQUESTS_PER_MINUTE: Final[int] = 10
    DAILY_TOKENS_PER_USER: Final[int] = 20_000

# Export as singleton
RATE_LIMITS = RateLimits()
```

Constants live in `constants.py`, re-exported through `config.py` for backward compatibility.

### Docstrings

Use Google-style docstrings:

```python
def validate_text_input(text: str, max_length: int = 50000) -> tuple[bool, str | None]:
    """
    Validate text input for processing.

    Args:
        text: The text to validate
        max_length: Maximum allowed length

    Returns:
        Tuple of (is_valid, error_message)
    """
```

### Error Handling

- Use user-facing messages from `strings.py`
- Log errors with `log_error_with_context()` for database tracking
- Handle Telegram API errors gracefully

```python
from handlers.common import log_error_with_context

try:
    result = await process_message(...)
except Exception as e:
    log_error_with_context(
        e,
        context_info={"operation": "translation"},
        user_id=user_id,
        text_preview=text[:200],
    )
    return S.GENERIC_ERROR
```

### Async Patterns

- Use `async def` for all Telegram handlers
- Use `asyncio.to_thread()` for blocking calls (not deprecated `get_event_loop()`)
- Prefer `httpx.AsyncClient` over synchronous requests

```python
# Correct: run blocking code in thread pool
response = await asyncio.to_thread(
    get_gemini_client().models.generate_content,
    model=GEMINI_MODEL_NAME,
    contents=content,
)
```

### Strings and Localization

All user-facing strings are in Uzbek and centralized in `strings.py`:

```python
import strings as S

# Use strings from the module
await message.reply_text(S.PROCESSING)
await message.reply_text(S.TEXT_TOO_LONG.format(actual=len(text), limit=MAX_LENGTH))
```

## Project Structure

```
tarjimon/
├── webhook.py           # FastAPI entry point, bot initialization
├── handlers/            # Telegram bot handlers (modular)
│   ├── __init__.py      # Re-exports all handlers
│   ├── common.py        # Shared utilities (Gemini client, error logging)
│   ├── translation.py   # Text/image translation
│   ├── youtube.py       # YouTube summarization
│   ├── subscription.py  # Payments and subscriptions
│   └── feedback.py      # User feedback
├── config.py            # Configuration, env vars, prompt loading
├── constants.py         # All magic numbers and limits
├── strings.py           # User-facing strings (Uzbek)
├── database.py          # SQLite database management
├── user_management.py   # User session and rate limiting
├── utils.py             # General utility functions
├── admin_dashboard.py   # FastAPI admin routes
└── prompts/             # Gemini prompt templates (markdown)
```

## Key Patterns

### Handler Registration

Handlers are added in `webhook.py` with specific order (YouTube before translation):

```python
# YouTube handler must be before translation to avoid double processing
youtube_filter = filters.TEXT & filters.Regex(YOUTUBE_URL_PATTERN)
application.add_handler(MessageHandler(youtube_filter, summarize_youtube))

# Translation handler excludes YouTube URLs
translate_filter = ~filters.COMMAND & (filters.TEXT & ~filters.Regex(YOUTUBE_URL_PATTERN) | ...)
application.add_handler(MessageHandler(translate_filter, translate_message))
```

### Database Access

Use the `DatabaseManager` singleton with context manager:

```python
from database import DatabaseManager

db_manager = DatabaseManager()
with db_manager.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    # Connection auto-commits on successful exit
```

### Gemini Client

Use lazy-initialized singleton:

```python
from handlers.common import get_gemini_client

client = get_gemini_client()
response = await asyncio.to_thread(
    client.models.generate_content,
    model=GEMINI_MODEL_NAME,
    contents=[...],
)
```

## Environment Variables

Required:
- `TELEGRAM_BOT_TOKEN` - Telegram Bot API token
- `GEMINI_API_KEY` - Google Gemini API key
- `GEMINI_MODEL_NAME` - Model name (e.g., `gemini-2.0-flash`)
- `WEBHOOK_URL` - Public webhook URL
- `WEBHOOK_SECRET` - Webhook validation secret

Optional:
- `SUPADATA_API_KEY` - YouTube transcript API
- `ADMIN_USERNAME`, `ADMIN_PASSWORD` - Admin dashboard auth
- `TARJIMON_DB_PATH` - Database directory (default: `data/sqlite_data`)
- `FEEDBACK_WEBHOOK_SECRET` - Required when feedback webhook is enabled

## Common Gotchas

1. **Message splitting:** Telegram has a 4096 character limit. Use `split_message()` from `handlers.common`
2. **HTML escaping:** Always escape user/AI-generated content with `escape_html()` before sending with `ParseMode.HTML`
3. **Rate limiting:** Check `user_manager.check_rate_limit()` before processing
4. **Subscription checks:** Use `is_user_premium()` and `get_user_remaining_limits()` from database module
5. **Prompts:** Load from `prompts/` directory via `config.PROMPTS` dict
