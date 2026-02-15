"""Database management module for the Tarjimon bot."""

import sqlite3
import os
import threading
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from config import (
    DATABASE_FILE,
    TARJIMON_LOG_PATH,
    logger,
)
from constants import PRICING_CONSTANTS


class DatabaseManager:
    """Thread-safe database manager with proper connection handling."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            self.initialized = True
            # Ensure the directory exists
            os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)

    @contextmanager
    def get_connection(self):
        """Context manager for database connections with proper error handling.

        Note: WAL mode is set once during init_db() since it persists across connections.
        Per-connection pragmas (synchronous, temp_store, foreign_keys) are set here.
        """
        connection = None
        try:
            connection = sqlite3.connect(DATABASE_FILE, timeout=30.0)
            connection.row_factory = sqlite3.Row
            # Per-connection pragmas (WAL mode is set persistently in init_db)
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            # Explicitly commit any pending transactions when context exits successfully
            connection.commit()
        except sqlite3.Error as e:
            if connection:
                connection.rollback()
            logger.error(f"Database error: {e}")
            self._log_error_to_file(f"Database error: {e}")
            raise
        finally:
            if connection:
                connection.close()

    def _log_error_to_file(self, error_msg):
        """Fallback logging if database operations fail"""
        try:
            os.makedirs(TARJIMON_LOG_PATH, exist_ok=True)
            with open(
                os.path.join(TARJIMON_LOG_PATH, "db_errors.log"), "a", encoding="utf-8"
            ) as f:
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] DATABASE ERROR: {error_msg}\n")
        except Exception as ex:
            logger.critical(
                f"Critical error: Could not write to error log file. Original DB error: {error_msg}. Log writing error: {ex}"
            )


def init_db():
    """Initialize the database with tables and indexes.

    Sets WAL mode persistently (survives restarts) for better concurrency.
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Set WAL mode once - this persists across connections and restarts
            cursor.execute("PRAGMA journal_mode=WAL")

            # Create tables (context manager handles transaction)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                is_translation_related INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                content_type TEXT DEFAULT NULL,
                content_preview TEXT DEFAULT NULL,
                video_duration_minutes INTEGER DEFAULT NULL,
                parent_request_id INTEGER DEFAULT NULL
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                tier TEXT NOT NULL DEFAULT 'free',
                expires_at TEXT,
                youtube_minutes_remaining INTEGER NOT NULL DEFAULT 0,
                translation_remaining INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                telegram_payment_id TEXT NOT NULL UNIQUE,
                amount_stars INTEGER NOT NULL,
                plan TEXT NOT NULL,
                days INTEGER NOT NULL,
                timestamp_utc TEXT NOT NULL
            )
            """)

            # Error logging table for admin dashboard
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS errors_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                user_id INTEGER,
                error_type TEXT NOT NULL,
                error_message TEXT NOT NULL,
                content_type TEXT,
                content_preview TEXT,
                stack_trace TEXT
            )
            """)

            # Performance indexes
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_token_usage_timestamp ON api_token_usage(timestamp_utc)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_token_usage_user ON api_token_usage(user_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user ON user_subscriptions(user_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_payment_history_user ON payment_history(user_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_payment_history_telegram_id ON payment_history(telegram_payment_id)"
            )

            # Index for errors_log
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_errors_log_timestamp ON errors_log(timestamp_utc)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_errors_log_user ON errors_log(user_id)"
            )

            # User sessions table for persistence across restarts
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id INTEGER PRIMARY KEY,
                last_activity TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                daily_token_usage INTEGER NOT NULL DEFAULT 0,
                daily_reset_time TEXT NOT NULL,
                request_timestamps TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)

            # Index for user_sessions cleanup
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sessions_last_activity ON user_sessions(last_activity)"
            )

            # Feedback table for storing user feedback messages
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                message_text TEXT NOT NULL,
                feedback_msg_id INTEGER,
                admin_msg_id INTEGER,
                replied INTEGER NOT NULL DEFAULT 0,
                timestamp_utc TEXT NOT NULL
            )
            """)

            # Index for feedback lookup
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_admin_msg ON feedback(admin_msg_id)"
            )

            # Migration: add new columns if they don't exist
            try:
                cursor.execute(
                    "ALTER TABLE user_subscriptions ADD COLUMN youtube_minutes_remaining INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: rename old column if it exists (for existing databases)
            try:
                cursor.execute(
                    "ALTER TABLE user_subscriptions RENAME COLUMN youtube_remaining TO youtube_minutes_remaining"
                )
            except sqlite3.OperationalError:
                pass  # Column doesn't exist or already renamed

            try:
                cursor.execute(
                    "ALTER TABLE user_subscriptions ADD COLUMN translation_remaining INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: add new columns for detailed token tracking
            for column, sql in [
                (
                    "input_tokens",
                    "ALTER TABLE api_token_usage ADD COLUMN input_tokens INTEGER DEFAULT 0",
                ),
                (
                    "output_tokens",
                    "ALTER TABLE api_token_usage ADD COLUMN output_tokens INTEGER DEFAULT 0",
                ),
                (
                    "cost_usd",
                    "ALTER TABLE api_token_usage ADD COLUMN cost_usd REAL DEFAULT 0.0",
                ),
                (
                    "content_type",
                    "ALTER TABLE api_token_usage ADD COLUMN content_type TEXT DEFAULT NULL",
                ),
                (
                    "content_preview",
                    "ALTER TABLE api_token_usage ADD COLUMN content_preview TEXT DEFAULT NULL",
                ),
                (
                    "video_duration_minutes",
                    "ALTER TABLE api_token_usage ADD COLUMN video_duration_minutes INTEGER DEFAULT NULL",
                ),
                (
                    "parent_request_id",
                    "ALTER TABLE api_token_usage ADD COLUMN parent_request_id INTEGER DEFAULT NULL",
                ),
            ]:
                try:
                    cursor.execute(sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            logger.info("Database initialized successfully.")
            return True
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during database initialization: {e}")
        return False


def log_token_usage_to_db(
    user_id: int,
    service_name: str,
    tokens_this_call: int,
    is_translation: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
    content_type: str | None = None,
    content_preview: str | None = None,
    video_duration_minutes: int | None = None,
    parent_request_id: int | None = None,
) -> int | None:
    """Log token usage to SQLite database with improved error handling and cost tracking.

    Args:
        user_id: Telegram user ID
        service_name: Service name (gemini, gemini_youtube)
        tokens_this_call: Total tokens used
        is_translation: Whether this is a translation request
        input_tokens: Number of input tokens (for cost calculation)
        output_tokens: Number of output tokens (for cost calculation)
        content_type: Type of content (text, image, youtube, etc.)
        content_preview: Preview of content (truncated)
        video_duration_minutes: Duration of video in minutes (for YouTube requests)
        parent_request_id: ID of parent request (for followups linking to videos)

    Returns:
        The inserted row ID, or None if logging failed
    """
    if tokens_this_call <= 0:  # Don't log zero token calls
        return None

    # Calculate cost in USD
    # For thinking models (e.g. Gemini 2.5 Pro), total_token_count includes
    # thinking tokens that are billed at the output rate but not reported in
    # candidates_token_count. Use (total - input) as the billable output.
    cost_usd = 0.0
    billable_output_tokens = output_tokens
    if input_tokens > 0 or output_tokens > 0:
        billable_output_tokens = max(tokens_this_call - input_tokens, output_tokens)
        cost_usd = (
            input_tokens / 1_000_000
        ) * PRICING_CONSTANTS.GEMINI_INPUT_PRICE_PER_M + (
            billable_output_tokens / 1_000_000
        ) * PRICING_CONSTANTS.GEMINI_OUTPUT_PRICE_PER_M

    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            # Store UTC timestamp in database
            timestamp_utc = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
            INSERT INTO api_token_usage (timestamp_utc, user_id, service_name, token_count, is_translation_related, input_tokens, output_tokens, cost_usd, content_type, content_preview, video_duration_minutes, parent_request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    timestamp_utc,
                    user_id,
                    service_name,
                    tokens_this_call,
                    1 if is_translation else 0,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    content_type,
                    content_preview[:500] if content_preview else None,
                    video_duration_minutes,
                    parent_request_id,
                ),
            )

            inserted_id = cursor.lastrowid

            # conn.commit() is now handled by the context manager
            thinking_tokens = billable_output_tokens - output_tokens
            logger.info(
                f"DB_LOG: ID:{inserted_id} Tokens: {tokens_this_call} (in:{input_tokens}/out:{output_tokens}/thinking:{thinking_tokens}), Cost: ${cost_usd:.6f}, User: {user_id}, Service: {service_name}"
            )
            return inserted_id
    except sqlite3.Error as e:
        logger.error(
            f"Error logging token usage to DB: {e}. User: {user_id}, Service: {service_name}, Tokens: {tokens_this_call}"
        )
        # Fall back to file logging if database insert fails
        _fallback_log_token_usage(
            user_id, service_name, tokens_this_call, is_translation, str(e)
        )
        return None
    except Exception as ex:
        logger.error(f"Unexpected error in log_token_usage_to_db: {ex}")
        _fallback_log_token_usage(
            user_id, service_name, tokens_this_call, is_translation, str(ex)
        )
        return None


def _fallback_log_token_usage(
    user_id: int, service_name: str, tokens: int, is_translation: bool, error: str
):
    """Fallback logging for token usage when database fails"""
    try:
        os.makedirs(TARJIMON_LOG_PATH, exist_ok=True)
        with open(
            os.path.join(TARJIMON_LOG_PATH, "token_usage_fallback.log"),
            "a",
            encoding="utf-8",
        ) as f:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            f.write(
                f"[{timestamp}] ERROR: {error} | User: {user_id}, Service: {service_name}, Tokens: {tokens}, Translation: {is_translation}\n"
            )
    except Exception as fb_ex:
        logger.critical(
            f"Failed to write to fallback log for token usage. Error: {fb_ex}"
        )


def log_error_to_db(
    error_type: str,
    error_message: str,
    user_id: int | None = None,
    content_type: str | None = None,
    content_preview: str | None = None,
    stack_trace: str | None = None,
):
    """Log an error to the database for admin dashboard tracking.

    Args:
        error_type: Type/category of error (e.g., 'api_error', 'rate_limit', 'validation')
        error_message: Human-readable error message
        user_id: Telegram user ID (if available)
        content_type: Type of content being processed (text, image, youtube)
        content_preview: Preview of content that caused the error
        stack_trace: Full stack trace (if available)
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            timestamp_utc = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                INSERT INTO errors_log (timestamp_utc, user_id, error_type, error_message, content_type, content_preview, stack_trace)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp_utc,
                    user_id,
                    error_type,
                    error_message[:1000] if error_message else None,
                    content_type,
                    content_preview[:500] if content_preview else None,
                    stack_trace[:5000] if stack_trace else None,
                ),
            )

            logger.info(f"Error logged to DB: {error_type} - {error_message[:100]}")
    except sqlite3.Error as e:
        logger.error(f"Failed to log error to database: {e}")
    except Exception as ex:
        logger.error(f"Unexpected error in log_error_to_db: {ex}")


def get_user_daily_youtube_count(user_id: int) -> int:
    """
    Get the number of YouTube summaries a user has requested today.

    Args:
        user_id: Telegram user ID

    Returns:
        Number of YouTube summary requests today
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Get start of today in UTC
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_iso = today_start.isoformat()

            cursor.execute(
                """
                SELECT COUNT(*) as count
                FROM api_token_usage 
                WHERE user_id = ? AND service_name = 'gemini_youtube' AND timestamp_utc >= ?
            """,
                (user_id, today_start_iso),
            )

            result = cursor.fetchone()
            count = result[0] if result else 0

            logger.debug(f"Daily YouTube count for user {user_id}: {count}")
            return count

    except sqlite3.Error as e:
        logger.error(f"Error getting daily YouTube count for user {user_id}: {e}")
        return 0
    except Exception as ex:
        logger.error(f"Unexpected error in get_user_daily_youtube_count: {ex}")
        return 0


def get_user_daily_translation_count(user_id: int) -> int:
    """
    Get the number of translations a user has requested today.

    Args:
        user_id: Telegram user ID

    Returns:
        Number of translation requests today
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Get start of today in UTC
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_iso = today_start.isoformat()

            cursor.execute(
                """
                SELECT COUNT(*) as count
                FROM api_token_usage 
                WHERE user_id = ? AND service_name = 'gemini' AND timestamp_utc >= ?
            """,
                (user_id, today_start_iso),
            )

            result = cursor.fetchone()
            count = result[0] if result else 0

            logger.debug(f"Daily translation count for user {user_id}: {count}")
            return count

    except sqlite3.Error as e:
        logger.error(f"Error getting daily translation count for user {user_id}: {e}")
        return 0
    except Exception as ex:
        logger.error(f"Unexpected error in get_user_daily_translation_count: {ex}")
        return 0


def get_user_subscription(user_id: int) -> dict | None:
    """
    Get user's current subscription status.

    Args:
        user_id: Telegram user ID

    Returns:
        Dict with tier, expires_at, and remaining limits, or None if no subscription
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT tier, expires_at, youtube_minutes_remaining, translation_remaining 
                FROM user_subscriptions WHERE user_id = ?
            """,
                (user_id,),
            )

            result = cursor.fetchone()
            if not result:
                return None

            return {
                "tier": result[0],
                "expires_at": result[1],
                "youtube_minutes_remaining": result[2] or 0,
                "translation_remaining": result[3] or 0,
            }

    except sqlite3.Error as e:
        logger.error(f"Error getting subscription for user {user_id}: {e}")
        return None
    except Exception as ex:
        logger.error(f"Unexpected error in get_user_subscription: {ex}")
        return None


def is_user_premium(user_id: int) -> bool:
    """
    Check if user has an active premium subscription.

    Args:
        user_id: Telegram user ID

    Returns:
        True if user has active premium, False otherwise
    """
    subscription = get_user_subscription(user_id)
    if not subscription:
        return False

    if subscription["tier"] != "premium":
        return False

    if not subscription["expires_at"]:
        return False

    # Check if subscription has expired
    try:
        expires_at = datetime.fromisoformat(subscription["expires_at"])
        now = datetime.now(timezone.utc)
        return expires_at > now
    except Exception as e:
        logger.error(f"Error parsing expiry date for user {user_id}: {e}")
        return False


def activate_premium(
    user_id: int, days: int, youtube_minutes_limit: int, translation_limit: int
) -> bool:
    """
    Activate or extend premium subscription for a user.

    Uses a single database transaction for both reading existing subscription
    and updating/inserting to ensure consistency.

    Args:
        user_id: Telegram user ID
        days: Number of days to add
        youtube_minutes_limit: Number of YouTube minutes to add
        translation_limit: Number of translations to add

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()

            # Check existing subscription within the same transaction
            cursor.execute(
                """
                SELECT tier, expires_at, youtube_minutes_remaining, translation_remaining
                FROM user_subscriptions WHERE user_id = ?
            """,
                (user_id,),
            )
            result = cursor.fetchone()

            if result and result[1]:  # Has subscription with expires_at
                # Extend existing subscription
                try:
                    current_expiry = datetime.fromisoformat(result[1])
                    # If already expired, start from now
                    if current_expiry < now:
                        current_expiry = now
                except Exception:
                    current_expiry = now

                new_expiry = current_expiry + timedelta(days=days)
                new_expiry_iso = new_expiry.isoformat()

                # Add new limits to existing remaining limits
                new_youtube_minutes = (result[2] or 0) + youtube_minutes_limit
                new_translation = (result[3] or 0) + translation_limit

                cursor.execute(
                    """
                    UPDATE user_subscriptions
                    SET tier = 'premium', expires_at = ?,
                        youtube_minutes_remaining = ?, translation_remaining = ?,
                        updated_at = ?
                    WHERE user_id = ?
                """,
                    (
                        new_expiry_iso,
                        new_youtube_minutes,
                        new_translation,
                        now_iso,
                        user_id,
                    ),
                )
            else:
                # Create new subscription
                expires_at = now + timedelta(days=days)
                expires_at_iso = expires_at.isoformat()

                cursor.execute(
                    """
                    INSERT INTO user_subscriptions
                    (user_id, tier, expires_at, youtube_minutes_remaining, translation_remaining, created_at, updated_at)
                    VALUES (?, 'premium', ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        tier = 'premium',
                        expires_at = excluded.expires_at,
                        youtube_minutes_remaining = excluded.youtube_minutes_remaining,
                        translation_remaining = excluded.translation_remaining,
                        updated_at = excluded.updated_at
                """,
                    (
                        user_id,
                        expires_at_iso,
                        youtube_minutes_limit,
                        translation_limit,
                        now_iso,
                        now_iso,
                    ),
                )

            logger.info(
                f"Premium activated for user {user_id}: {days} days, {youtube_minutes_limit} minutes, {translation_limit} translations"
            )
            return True

    except sqlite3.Error as e:
        logger.error(f"Error activating premium for user {user_id}: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in activate_premium: {ex}")
        return False


def log_payment(
    user_id: int, telegram_payment_id: str, amount_stars: int, plan: str, days: int
) -> bool:
    """
    Log a successful payment to the database.

    Args:
        user_id: Telegram user ID
        telegram_payment_id: Unique payment ID from Telegram
        amount_stars: Number of Stars paid
        plan: Plan name (e.g., "premium_7_days")
        days: Number of days purchased

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            timestamp_utc = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                INSERT INTO payment_history (user_id, telegram_payment_id, amount_stars, plan, days, timestamp_utc)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (user_id, telegram_payment_id, amount_stars, plan, days, timestamp_utc),
            )

            logger.info(f"Payment logged: User {user_id}, {amount_stars} Stars, {plan}")
            return True

    except sqlite3.Error as e:
        logger.error(f"Error logging payment for user {user_id}: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in log_payment: {ex}")
        return False


def get_payment_by_telegram_id(telegram_payment_id: str) -> dict | None:
    """
    Check if a payment with the given Telegram payment ID already exists.
    Used for idempotency check to prevent duplicate payment processing.

    Args:
        telegram_payment_id: Unique payment ID from Telegram

    Returns:
        Dict with payment details if found, None otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT id, user_id, telegram_payment_id, amount_stars, plan, days, timestamp_utc
                FROM payment_history WHERE telegram_payment_id = ?
            """,
                (telegram_payment_id,),
            )

            result = cursor.fetchone()
            if not result:
                return None

            return {
                "id": result[0],
                "user_id": result[1],
                "telegram_payment_id": result[2],
                "amount_stars": result[3],
                "plan": result[4],
                "days": result[5],
                "timestamp_utc": result[6],
            }

    except sqlite3.Error as e:
        logger.error(f"Error checking payment {telegram_payment_id}: {e}")
        return None
    except Exception as ex:
        logger.error(f"Unexpected error in get_payment_by_telegram_id: {ex}")
        return None


def decrement_youtube_minutes(user_id: int, minutes: int) -> bool:
    """
    Decrement the user's remaining YouTube minutes.

    Args:
        user_id: Telegram user ID
        minutes: Number of minutes to subtract

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                UPDATE user_subscriptions 
                SET youtube_minutes_remaining = youtube_minutes_remaining - ?, updated_at = ?
                WHERE user_id = ? AND youtube_minutes_remaining >= ?
            """,
                (minutes, now_iso, user_id, minutes),
            )

            if cursor.rowcount > 0:
                logger.debug(
                    f"Decremented {minutes} YouTube minutes for user {user_id}"
                )
                return True
            return False

    except sqlite3.Error as e:
        logger.error(f"Error decrementing YouTube minutes for user {user_id}: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in decrement_youtube_minutes: {ex}")
        return False


def decrement_translation_limit(user_id: int) -> bool:
    """
    Decrement the user's remaining translation limit by 1.

    Args:
        user_id: Telegram user ID

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                UPDATE user_subscriptions 
                SET translation_remaining = translation_remaining - 1, updated_at = ?
                WHERE user_id = ? AND translation_remaining > 0
            """,
                (now_iso, user_id),
            )

            if cursor.rowcount > 0:
                logger.debug(f"Decremented translation limit for user {user_id}")
                return True
            return False

    except sqlite3.Error as e:
        logger.error(f"Error decrementing translation limit for user {user_id}: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in decrement_translation_limit: {ex}")
        return False


def get_user_remaining_limits(user_id: int) -> dict:
    """
    Get user's remaining limits for premium features.

    Args:
        user_id: Telegram user ID

    Returns:
        Dict with youtube_minutes_remaining and translation_remaining
    """
    subscription = get_user_subscription(user_id)
    if not subscription:
        return {"youtube_minutes_remaining": 0, "translation_remaining": 0}

    # Check if subscription is still valid
    if subscription["expires_at"]:
        try:
            expires_at = datetime.fromisoformat(subscription["expires_at"])
            now = datetime.now(timezone.utc)
            if expires_at <= now:
                return {"youtube_minutes_remaining": 0, "translation_remaining": 0}
        except Exception:
            return {"youtube_minutes_remaining": 0, "translation_remaining": 0}

    return {
        "youtube_minutes_remaining": subscription.get("youtube_minutes_remaining", 0),
        "translation_remaining": subscription.get("translation_remaining", 0),
    }


def save_user_session(
    user_id: int,
    last_activity: str,
    request_count: int,
    daily_token_usage: int,
    daily_reset_time: str,
    request_timestamps: str | None = None,
) -> bool:
    """
    Save or update a user session to the database for persistence.

    Args:
        user_id: Telegram user ID
        last_activity: ISO timestamp of last activity
        request_count: Number of requests in current period
        daily_token_usage: Token usage for the current day
        daily_reset_time: ISO timestamp when daily counter should reset
        request_timestamps: JSON-encoded list of recent request timestamps

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                INSERT INTO user_sessions 
                (user_id, last_activity, request_count, daily_token_usage, daily_reset_time, request_timestamps, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_activity = excluded.last_activity,
                    request_count = excluded.request_count,
                    daily_token_usage = excluded.daily_token_usage,
                    daily_reset_time = excluded.daily_reset_time,
                    request_timestamps = excluded.request_timestamps,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    last_activity,
                    request_count,
                    daily_token_usage,
                    daily_reset_time,
                    request_timestamps,
                    now_iso,
                    now_iso,
                ),
            )
            return True

    except sqlite3.Error as e:
        logger.error(f"Error saving session for user {user_id}: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in save_user_session: {ex}")
        return False


def load_user_session(user_id: int) -> dict | None:
    """
    Load a user session from the database.

    Args:
        user_id: Telegram user ID

    Returns:
        Dict with session data or None if not found
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT last_activity, request_count, daily_token_usage, daily_reset_time, request_timestamps
                FROM user_sessions WHERE user_id = ?
                """,
                (user_id,),
            )

            result = cursor.fetchone()
            if not result:
                return None

            return {
                "last_activity": result[0],
                "request_count": result[1],
                "daily_token_usage": result[2],
                "daily_reset_time": result[3],
                "request_timestamps": result[4],
            }

    except sqlite3.Error as e:
        logger.error(f"Error loading session for user {user_id}: {e}")
        return None
    except Exception as ex:
        logger.error(f"Unexpected error in load_user_session: {ex}")
        return None


def delete_user_session(user_id: int) -> bool:
    """
    Delete a user session from the database.

    Args:
        user_id: Telegram user ID

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            return True

    except sqlite3.Error as e:
        logger.error(f"Error deleting session for user {user_id}: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in delete_user_session: {ex}")
        return False


def cleanup_old_sessions(timeout_seconds: int = 7200) -> int:
    """
    Remove sessions that have been inactive for longer than the timeout.

    Args:
        timeout_seconds: Session timeout in seconds (default 2 hours)

    Returns:
        Number of sessions deleted
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Calculate cutoff time
            cutoff_time = datetime.now(timezone.utc) - timedelta(
                seconds=timeout_seconds
            )
            cutoff_iso = cutoff_time.isoformat()

            cursor.execute(
                "DELETE FROM user_sessions WHERE last_activity < ?", (cutoff_iso,)
            )
            deleted_count = cursor.rowcount

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old sessions from database")

            return deleted_count

    except sqlite3.Error as e:
        logger.error(f"Error cleaning up old sessions: {e}")
        return 0
    except Exception as ex:
        logger.error(f"Unexpected error in cleanup_old_sessions: {ex}")
        return 0


def ensure_free_user_subscription(
    user_id: int, youtube_minutes: int, translations: int
) -> bool:
    """
    Ensure a free user has a subscription record with the given limits.
    Creates a new record if none exists, or resets if expired.

    Args:
        user_id: Telegram user ID
        youtube_minutes: Initial YouTube minutes limit
        translations: Initial translation limit

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()

            # Calculate expiry (30 days from now for free tier)
            expires_at = now + timedelta(days=30)
            expires_at_iso = expires_at.isoformat()

            # Check if user already has a subscription
            subscription = get_user_subscription(user_id)

            if subscription:
                # Check if expired
                if subscription["expires_at"]:
                    try:
                        current_expiry = datetime.fromisoformat(
                            subscription["expires_at"]
                        )
                        if current_expiry > now:
                            # Not expired, don't reset
                            return True
                    except Exception:
                        pass

                # Expired or invalid - reset to free tier limits
                cursor.execute(
                    """
                    UPDATE user_subscriptions 
                    SET tier = 'free', expires_at = ?, 
                        youtube_minutes_remaining = ?, translation_remaining = ?,
                        updated_at = ?
                    WHERE user_id = ?
                """,
                    (expires_at_iso, youtube_minutes, translations, now_iso, user_id),
                )
            else:
                # Create new free subscription
                cursor.execute(
                    """
                    INSERT INTO user_subscriptions 
                    (user_id, tier, expires_at, youtube_minutes_remaining, translation_remaining, created_at, updated_at)
                    VALUES (?, 'free', ?, ?, ?, ?, ?)
                """,
                    (
                        user_id,
                        expires_at_iso,
                        youtube_minutes,
                        translations,
                        now_iso,
                        now_iso,
                    ),
                )

            logger.info(
                f"Free subscription ensured for user {user_id}: {youtube_minutes} min, {translations} translations"
            )
            return True

    except sqlite3.Error as e:
        logger.error(f"Error ensuring free subscription for user {user_id}: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in ensure_free_user_subscription: {ex}")
        return False


def save_feedback(
    user_id: int,
    message_text: str,
    username: str | None = None,
    first_name: str | None = None,
    feedback_msg_id: int | None = None,
    admin_msg_id: int | None = None,
) -> int | None:
    """
    Save user feedback to the database.

    Args:
        user_id: Telegram user ID
        message_text: The feedback message
        username: User's Telegram username (optional)
        first_name: User's first name (optional)
        feedback_msg_id: Message ID in user's chat (optional)
        admin_msg_id: Message ID sent to admin (optional)

    Returns:
        The inserted feedback ID, or None if failed
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            timestamp_utc = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                INSERT INTO feedback (user_id, username, first_name, message_text, feedback_msg_id, admin_msg_id, timestamp_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    first_name,
                    message_text,
                    feedback_msg_id,
                    admin_msg_id,
                    timestamp_utc,
                ),
            )

            feedback_id = cursor.lastrowid
            logger.info(f"Feedback saved: ID={feedback_id}, User={user_id}")
            return feedback_id

    except sqlite3.Error as e:
        logger.error(f"Error saving feedback for user {user_id}: {e}")
        return None
    except Exception as ex:
        logger.error(f"Unexpected error in save_feedback: {ex}")
        return None


def update_feedback_admin_msg_id(feedback_id: int, admin_msg_id: int) -> bool:
    """
    Update the admin message ID for a feedback entry.

    Args:
        feedback_id: The feedback entry ID
        admin_msg_id: The message ID sent to admin

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE feedback SET admin_msg_id = ? WHERE id = ?",
                (admin_msg_id, feedback_id),
            )
            return cursor.rowcount > 0

    except sqlite3.Error as e:
        logger.error(f"Error updating feedback admin_msg_id: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in update_feedback_admin_msg_id: {ex}")
        return False


def get_feedback_by_admin_msg_id(admin_msg_id: int) -> dict | None:
    """
    Get feedback entry by the admin message ID (for reply lookup).

    Args:
        admin_msg_id: The message ID in admin's chat

    Returns:
        Dict with feedback data or None if not found
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, user_id, username, first_name, message_text, feedback_msg_id, admin_msg_id, replied, timestamp_utc
                FROM feedback WHERE admin_msg_id = ?
                """,
                (admin_msg_id,),
            )

            result = cursor.fetchone()
            if not result:
                return None

            return {
                "id": result[0],
                "user_id": result[1],
                "username": result[2],
                "first_name": result[3],
                "message_text": result[4],
                "feedback_msg_id": result[5],
                "admin_msg_id": result[6],
                "replied": result[7],
                "timestamp_utc": result[8],
            }

    except sqlite3.Error as e:
        logger.error(f"Error getting feedback by admin_msg_id {admin_msg_id}: {e}")
        return None
    except Exception as ex:
        logger.error(f"Unexpected error in get_feedback_by_admin_msg_id: {ex}")
        return None


def mark_feedback_replied(feedback_id: int) -> bool:
    """
    Mark a feedback entry as replied.

    Args:
        feedback_id: The feedback entry ID

    Returns:
        True if successful, False otherwise
    """
    db_manager = DatabaseManager()
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE feedback SET replied = 1 WHERE id = ?",
                (feedback_id,),
            )
            return cursor.rowcount > 0

    except sqlite3.Error as e:
        logger.error(f"Error marking feedback {feedback_id} as replied: {e}")
        return False
    except Exception as ex:
        logger.error(f"Unexpected error in mark_feedback_replied: {ex}")
        return False
