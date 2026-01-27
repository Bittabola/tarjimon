"""Configuration module for the Tarjimon bot."""

import os
import re
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv

# Import constants from constants module
from constants import (
    SUBSCRIPTION_LIMITS,
    PRICING_CONSTANTS,
    MONTHS_UZ,
)
import strings as S

# Load environment variables from .env file
load_dotenv()

# --- Logging Setup ---
LOG_DIR = os.environ.get("TARJIMON_LOG_PATH", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Log rotation settings
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
LOG_BACKUP_COUNT = 5  # Keep 5 backup files (total ~60 MB max)

# Set up logging with UTC timestamps
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()

# Set up rotating file handler (auto-rotates when file reaches 10MB)
file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "bot.log"),
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
)
file_handler.setFormatter(formatter)

# Set up console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Configure logging
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger(__name__)

# --- Configuration ---
# Make sure to set these environment variables before running the bot
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME")

# Webhook settings
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")  # Secret token for webhook validation

# Supadata API (for YouTube transcripts)
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY")

# Feedback bot settings (required for feedback feature)
FEEDBACK_BOT_TOKEN = os.environ.get("FEEDBACK_BOT_TOKEN")
_feedback_admin_id = os.environ.get("FEEDBACK_ADMIN_ID")
FEEDBACK_ADMIN_ID = int(_feedback_admin_id) if _feedback_admin_id else None

# Admin dashboard credentials (HTTP Basic Auth)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")  # Required for dashboard access

# Base paths can be overridden through environment variables
TARJIMON_DB_PATH = os.environ.get("TARJIMON_DB_PATH", "data/sqlite_data")
TARJIMON_CONFIG_PATH = os.environ.get("TARJIMON_CONFIG_PATH", "config")
TARJIMON_LOG_PATH = os.environ.get("TARJIMON_LOG_PATH", "logs")

# SQLite database file in the chosen database directory
DATABASE_FILE = os.path.join(TARJIMON_DB_PATH, "tracking_data.db")

# --- Token Budget Management ---
# Monthly token limits for cost control
MONTHLY_TOKEN_LIMITS = {
    "gemini": 5_000_000,  # 5M tokens per month (Gemini only)
    "total": 5_000_000,  # 5M tokens total per month
}

# --- Revenue Settings (computed from constants) ---
NET_REVENUE_PER_STAR = PRICING_CONSTANTS.STARS_TO_USD * (
    1 - PRICING_CONSTANTS.TELEGRAM_FEE_PERCENT / 100
)

# Premium package value breakdown for amortized P/L calculation
_PREMIUM_NET_REVENUE = SUBSCRIPTION_LIMITS.PREMIUM_PRICE_STARS * NET_REVENUE_PER_STAR
_PREMIUM_VIDEO_SHARE = 0.80
_PREMIUM_TRANSLATION_SHARE = 0.20
REVENUE_PER_VIDEO_MINUTE = (
    _PREMIUM_NET_REVENUE * _PREMIUM_VIDEO_SHARE
) / SUBSCRIPTION_LIMITS.PREMIUM_YOUTUBE_MINUTES
REVENUE_PER_TRANSLATION = (
    _PREMIUM_NET_REVENUE * _PREMIUM_TRANSLATION_SHARE
) / SUBSCRIPTION_LIMITS.PREMIUM_TRANSLATIONS

# Subscription plan (Stars pricing)
SUBSCRIPTION_PLAN = {
    "stars": SUBSCRIPTION_LIMITS.PREMIUM_PRICE_STARS,
    "days": SUBSCRIPTION_LIMITS.PREMIUM_PERIOD_DAYS,
    "title": S.PLAN_TITLE,
    "description": S.PLAN_DESCRIPTION.format(
        youtube_minutes=SUBSCRIPTION_LIMITS.PREMIUM_YOUTUBE_MINUTES,
        translations=SUBSCRIPTION_LIMITS.PREMIUM_TRANSLATIONS,
        days=SUBSCRIPTION_LIMITS.PREMIUM_PERIOD_DAYS,
    ),
    "youtube_minutes_limit": SUBSCRIPTION_LIMITS.PREMIUM_YOUTUBE_MINUTES,
    "translation_limit": SUBSCRIPTION_LIMITS.PREMIUM_TRANSLATIONS,
}


def format_date_uzbek(iso_date: str) -> str:
    """
    Format an ISO date string to Uzbek format.

    Args:
        iso_date: ISO format date string (e.g., "2024-01-15T12:00:00+00:00")

    Returns:
        Formatted date string (e.g., "2024-yil 15-yanvar")
    """
    try:
        date_obj = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return f"{date_obj.year}-yil {date_obj.day}-{MONTHS_UZ[date_obj.month - 1]}"
    except Exception:
        # Fallback to first 10 chars if parsing fails
        return iso_date[:10] if iso_date and len(iso_date) > 10 else iso_date


def get_days_remaining(iso_date: str) -> int | str:
    """
    Calculate days remaining until an ISO date.

    Args:
        iso_date: ISO format date string

    Returns:
        Number of days remaining, or "?" if parsing fails
    """
    try:
        date_obj = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        if date_obj.tzinfo is None:
            date_obj = date_obj.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (date_obj - now).days + 1
        return max(0, days)
    except Exception:
        return "?"


def validate_config(is_webhook: bool = False, check_prompts: bool = True):
    """Validate that required environment variables and prompts are set.

    Args:
        is_webhook: If True, also validates webhook-specific config
        check_prompts: If True, validates that required prompts are loaded
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Error: TELEGRAM_BOT_TOKEN environment variable not set.")
        return False
    if not GEMINI_API_KEY:
        logger.error("Error: GEMINI_API_KEY environment variable not set.")
        return False
    if not GEMINI_MODEL_NAME:
        logger.error(
            "Error: GEMINI_MODEL_NAME environment variable not set. Please set it to your desired Gemini model."
        )
        return False
    if is_webhook and not WEBHOOK_URL:
        logger.error(
            "Error: WEBHOOK_URL environment variable not set for webhook mode."
        )
        return False

    # Validate prompts (done after PROMPTS is loaded at module level)
    if check_prompts:
        prompts_valid, missing = validate_prompts()
        if not prompts_valid:
            logger.error(f"Error: Missing required prompts: {', '.join(missing)}")
            return False

    return True


# --- Prompt Loading ---
PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompts_from_file(filename: str) -> dict[str, str]:
    """
    Load prompts from a markdown file.

    The file format uses '---' separators and '## section_name' headers.
    Returns a dict mapping section names to prompt text.
    """
    filepath = PROMPTS_DIR / filename
    if not filepath.exists():
        logger.warning(f"Prompt file not found: {filepath}")
        return {}

    content = filepath.read_text(encoding="utf-8")

    # Split by --- separator and parse sections
    prompts = {}
    sections = re.split(r"\n---\n", content)

    for section in sections:
        # Look for ## header
        match = re.match(r"##\s+(\w+)\s*\n(.*)", section, re.DOTALL)
        if match:
            name = match.group(1).strip()
            text = match.group(2).strip()
            prompts[name] = text

    return prompts


def load_all_prompts() -> dict[str, dict[str, str]]:
    """
    Load all prompts from the prompts directory.

    Returns a nested dict: {category: {prompt_name: prompt_text}}
    """
    return {
        "translation": _load_prompts_from_file("translation.md"),
        "youtube_summary": _load_prompts_from_file("youtube_summary.md"),
        "youtube_followup": _load_prompts_from_file("youtube_followup.md"),
    }


# Load prompts at module import time
PROMPTS = load_all_prompts()

# Required prompts that must exist for the bot to function
REQUIRED_PROMPTS = [
    ("translation", "text_only"),
    ("translation", "image_only"),
    ("translation", "text_with_image"),
    ("youtube_summary", "with_transcript"),
    ("youtube_summary", "without_transcript"),
    ("youtube_followup", "with_transcript"),
    ("youtube_followup", "without_transcript"),
]


def validate_prompts() -> tuple[bool, list[str]]:
    """
    Validate that all required prompts are loaded.

    Returns:
        Tuple of (is_valid, list of missing prompts)
    """
    missing = []
    for category, name in REQUIRED_PROMPTS:
        if not PROMPTS.get(category, {}).get(name):
            missing.append(f"{category}.{name}")

    return len(missing) == 0, missing
