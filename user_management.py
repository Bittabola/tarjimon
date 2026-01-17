"""
User Session Management and Token-Based Rate Limiting

This module provides comprehensive user session management with token-based rate limiting
to control API costs and ensure fair usage across all users.

Features:
- Token-based daily limits per user (20K tokens/day)
- System-wide monthly token budgets (5M tokens/month)
- Request rate limiting (10 requests/minute)
- Content size validation
- Real-time budget monitoring
- Integration with existing database token logging
- Session persistence across restarts
"""

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime as dt, timezone
from typing import Dict, List, Optional, Tuple

import strings as S
from config import (
    logger,
    DAILY_USER_TOKEN_LIMIT,
    REQUEST_RATE_LIMIT,
    MAX_TEXT_LENGTH,
    MAX_IMAGE_SIZE_MB,
    MONTHLY_TOKEN_LIMITS,
    SESSION_TIMEOUT_SECONDS,
    MAX_INACTIVE_SESSIONS,
)
from database import (
    DatabaseManager,
    save_user_session,
    load_user_session,
    delete_user_session,
    cleanup_old_sessions,
)


@dataclass
class UserSession:
    """User session data structure with token tracking."""

    user_id: int
    last_activity: float
    request_count: int
    request_timestamps: deque = field(default_factory=lambda: deque(maxlen=60))
    daily_token_usage: int = 0
    daily_reset_time: float = 0.0

    def to_dict(self) -> dict:
        """Convert session to dictionary for serialization."""
        return {
            "user_id": self.user_id,
            "last_activity": self.last_activity,
            "request_count": self.request_count,
            "request_timestamps": list(self.request_timestamps),
            "daily_token_usage": self.daily_token_usage,
            "daily_reset_time": self.daily_reset_time,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserSession":
        """Create session from dictionary."""
        session = cls(
            user_id=data["user_id"],
            last_activity=data["last_activity"],
            request_count=data["request_count"],
            daily_token_usage=data.get("daily_token_usage", 0),
            daily_reset_time=data.get("daily_reset_time", 0.0),
        )
        # Restore request timestamps
        timestamps = data.get("request_timestamps", [])
        for ts in timestamps:
            session.request_timestamps.append(ts)
        return session


class TokenBudgetManager:
    """Manages system-wide token budgets and limits."""

    def __init__(self):
        self.monthly_limits = MONTHLY_TOKEN_LIMITS
        self.db_manager = DatabaseManager()

    def get_monthly_usage(self, service: str = None) -> int:
        """Get current month's token usage from database."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()

                # Get first day of current month in UTC
                now = dt.now(timezone.utc)
                first_day = now.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                first_day_iso = first_day.strftime("%Y-%m-%dT%H:%M:%S")

                if service:
                    # Map service names to database patterns
                    if service == "gemini":
                        # Match all gemini services
                        cursor.execute(
                            """
                            SELECT COALESCE(SUM(token_count), 0) as total_tokens
                            FROM api_token_usage 
                            WHERE service_name LIKE 'gemini%' AND timestamp_utc >= ?
                        """,
                            (first_day_iso,),
                        )
                    else:
                        # Exact match for other services
                        cursor.execute(
                            """
                            SELECT COALESCE(SUM(token_count), 0) as total_tokens
                            FROM api_token_usage 
                            WHERE service_name = ? AND timestamp_utc >= ?
                        """,
                            (
                                service,
                                first_day_iso,
                            ),
                        )
                else:
                    # Get total usage across all services
                    cursor.execute(
                        """
                        SELECT COALESCE(SUM(token_count), 0) as total_tokens
                        FROM api_token_usage 
                        WHERE timestamp_utc >= ?
                    """,
                        (first_day_iso,),
                    )

                result = cursor.fetchone()
                total = result[0] if result else 0

                logger.debug(f"Monthly usage for {service or 'all'}: {total} tokens")
                return total

        except Exception as e:
            logger.error(f"Error getting monthly usage for {service}: {e}")
            return 0

    def check_monthly_budget(
        self, service: str, tokens_needed: int
    ) -> Tuple[bool, Optional[str]]:
        """Check if token request fits within monthly budget."""
        current_usage = self.get_monthly_usage(service)
        total_usage = self.get_monthly_usage()

        # Check service-specific limit
        if current_usage + tokens_needed > self.monthly_limits[service]:
            remaining = max(0, self.monthly_limits[service] - current_usage)
            error_msg = S.MONTHLY_SERVICE_LIMIT.format(
                service="Gemini",
                used=current_usage,
                limit=self.monthly_limits[service],
                remaining=remaining,
            )
            return False, error_msg

        # Check total system limit
        if total_usage + tokens_needed > self.monthly_limits["total"]:
            remaining = max(0, self.monthly_limits["total"] - total_usage)
            error_msg = S.MONTHLY_SYSTEM_LIMIT.format(
                used=total_usage,
                limit=self.monthly_limits["total"],
                remaining=remaining,
            )
            return False, error_msg

        return True, None

    def get_budget_status(self) -> dict:
        """Get current budget status for all services.

        Caches the usage values to avoid redundant database queries.
        """
        # Cache usage values to avoid multiple database queries
        gemini_used = self.get_monthly_usage("gemini")
        total_used = self.get_monthly_usage()

        return {
            "gemini": {
                "used": gemini_used,
                "limit": self.monthly_limits["gemini"],
                "remaining": max(0, self.monthly_limits["gemini"] - gemini_used),
            },
            "total": {
                "used": total_used,
                "limit": self.monthly_limits["total"],
                "remaining": max(0, self.monthly_limits["total"] - total_used),
            },
        }


class UserManager:
    """Manages user sessions and token-based rate limiting with database persistence."""

    def __init__(self):
        self.sessions: Dict[int, UserSession] = {}
        self.rate_limits = {
            "requests_per_minute": REQUEST_RATE_LIMIT,
            "daily_tokens_per_user": DAILY_USER_TOKEN_LIMIT,  # 20K tokens per user per day
            "max_text_length": MAX_TEXT_LENGTH,
            "max_image_size_mb": MAX_IMAGE_SIZE_MB,
        }
        self.budget_manager = TokenBudgetManager()
        self.db_manager = DatabaseManager()
        self._persist_interval = 60  # Persist sessions every 60 seconds
        self._last_persist_time = time.time()
        self._persist_lock = threading.Lock()  # Prevent concurrent persist operations

    def _persist_session(self, session: UserSession) -> None:
        """Persist a single session to the database."""
        try:
            # Convert timestamps to ISO format
            last_activity_iso = dt.fromtimestamp(
                session.last_activity, tz=timezone.utc
            ).isoformat()
            daily_reset_iso = dt.fromtimestamp(
                session.daily_reset_time, tz=timezone.utc
            ).isoformat()

            # Convert request timestamps to JSON
            timestamps_json = json.dumps(list(session.request_timestamps))

            save_user_session(
                user_id=session.user_id,
                last_activity=last_activity_iso,
                request_count=session.request_count,
                daily_token_usage=session.daily_token_usage,
                daily_reset_time=daily_reset_iso,
                request_timestamps=timestamps_json,
            )
        except Exception as e:
            logger.error(f"Error persisting session for user {session.user_id}: {e}")

    def _load_session_from_db(self, user_id: int) -> Optional[UserSession]:
        """Load a session from the database if it exists."""
        try:
            data = load_user_session(user_id)
            if not data:
                return None

            # Parse ISO timestamps back to floats
            last_activity = dt.fromisoformat(data["last_activity"]).timestamp()
            daily_reset_time = dt.fromisoformat(data["daily_reset_time"]).timestamp()

            # Parse request timestamps from JSON
            request_timestamps_list: List[float] = []
            if data.get("request_timestamps"):
                try:
                    request_timestamps_list = json.loads(data["request_timestamps"])
                except json.JSONDecodeError:
                    pass

            session = UserSession(
                user_id=user_id,
                last_activity=last_activity,
                request_count=data["request_count"],
                daily_token_usage=data["daily_token_usage"],
                daily_reset_time=daily_reset_time,
            )

            # Restore request timestamps
            for ts in request_timestamps_list:
                session.request_timestamps.append(ts)

            logger.debug(f"Loaded session for user {user_id} from database")
            return session

        except Exception as e:
            logger.error(f"Error loading session for user {user_id}: {e}")
            return None

    def get_or_create_session(self, user_id: int) -> UserSession:
        """Get or create a user session, loading from database if needed."""
        current_time = time.time()

        if user_id not in self.sessions:
            # Try to load from database first
            db_session = self._load_session_from_db(user_id)

            if db_session:
                # Check if the loaded session is still valid
                if current_time - db_session.last_activity <= SESSION_TIMEOUT_SECONDS:
                    self.sessions[user_id] = db_session
                    logger.debug(f"Restored session for user {user_id} from database")
                else:
                    # Session expired, delete from database
                    delete_user_session(user_id)
                    db_session = None

            if not db_session:
                # Create new session
                daily_usage = self._get_daily_token_usage(user_id)

                self.sessions[user_id] = UserSession(
                    user_id=user_id,
                    last_activity=current_time,
                    request_count=0,
                    daily_token_usage=daily_usage,
                    daily_reset_time=current_time,
                )
                logger.info(
                    f"Created new session for user {user_id}, daily tokens: {daily_usage}"
                )

        session = self.sessions[user_id]

        # Reset daily counter if needed (24 hours)
        if current_time - session.daily_reset_time > 86400:
            session.daily_token_usage = self._get_daily_token_usage(user_id)
            session.daily_reset_time = current_time
            logger.info(
                f"Reset daily counter for user {user_id}, tokens: {session.daily_token_usage}"
            )

        session.last_activity = current_time

        # Periodically persist active sessions (thread-safe)
        with self._persist_lock:
            if current_time - self._last_persist_time > self._persist_interval:
                self._persist_active_sessions()
                self._last_persist_time = current_time

        return session

    def _persist_active_sessions(self) -> None:
        """Persist all active sessions to the database."""
        for session in self.sessions.values():
            self._persist_session(session)

    def _get_daily_token_usage(self, user_id: int) -> int:
        """Get user's token usage for today from database."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()

                # Get start of today in UTC
                now = dt.now(timezone.utc)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                today_start_iso = today_start.strftime("%Y-%m-%dT%H:%M:%S")

                cursor.execute(
                    """
                    SELECT COALESCE(SUM(token_count), 0) as daily_tokens
                    FROM api_token_usage 
                    WHERE user_id = ? AND timestamp_utc >= ?
                """,
                    (user_id, today_start_iso),
                )

                result = cursor.fetchone()
                daily_total = result[0] if result else 0

                logger.debug(f"Daily token usage for user {user_id}: {daily_total}")
                return daily_total

        except Exception as e:
            logger.error(f"Error getting daily token usage for user {user_id}: {e}")
            return 0

    def check_token_limits(
        self, user_id: int, service: str, estimated_tokens: int
    ) -> Tuple[bool, Optional[str]]:
        """Check if user can use the estimated tokens."""
        session = self.get_or_create_session(user_id)

        # Check user's daily limit
        if (
            session.daily_token_usage + estimated_tokens
            > self.rate_limits["daily_tokens_per_user"]
        ):
            remaining = max(
                0, self.rate_limits["daily_tokens_per_user"] - session.daily_token_usage
            )
            error_msg = S.DAILY_TOKEN_LIMIT_EXCEEDED.format(
                used=session.daily_token_usage,
                limit=self.rate_limits["daily_tokens_per_user"],
                remaining=remaining,
            )
            return False, error_msg

        # Check system monthly budget
        budget_ok, budget_msg = self.budget_manager.check_monthly_budget(
            service, estimated_tokens
        )
        if not budget_ok:
            return False, budget_msg

        return True, None

    def check_rate_limit(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Check if user has exceeded request rate limits."""
        session = self.get_or_create_session(user_id)
        current_time = time.time()

        # Check requests per minute
        session.request_timestamps.append(current_time)
        recent_requests = sum(
            1 for ts in session.request_timestamps if current_time - ts < 60
        )

        if recent_requests > self.rate_limits["requests_per_minute"]:
            logger.warning(
                f"User {user_id} exceeded rate limit: {recent_requests} requests/minute"
            )
            error_msg = S.TOO_MANY_REQUESTS.format(
                recent=recent_requests,
                limit=self.rate_limits["requests_per_minute"],
            )
            return False, error_msg

        return True, None

    def check_text_length(self, text: str) -> Tuple[bool, Optional[str]]:
        """Check if text length is within limits."""
        if len(text) > self.rate_limits["max_text_length"]:
            error_msg = S.TEXT_TOO_LONG.format(
                actual=len(text),
                limit=self.rate_limits["max_text_length"],
            )
            return False, error_msg
        return True, None

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text (rough approximation)."""
        # Rough estimation: 1 token ≈ 4 characters
        # Add 10% buffer for Gemini overhead
        base_estimate = len(text) // 4
        return int(base_estimate * 1.1)

    def record_token_usage(self, user_id: int, tokens_used: int):
        """Record actual token usage for a user and persist."""
        session = self.get_or_create_session(user_id)
        session.daily_token_usage += tokens_used
        logger.debug(
            f"User {user_id} token usage updated: +{tokens_used}, total today: {session.daily_token_usage}"
        )
        # Persist session after token usage update
        self._persist_session(session)

    def cleanup_inactive_sessions(self) -> int:
        """
        Remove inactive sessions to prevent memory leaks.
        Also cleans up old sessions from the database.

        Returns:
            Number of sessions cleaned up
        """
        current_time = time.time()
        inactive_user_ids = []

        for user_id, session in self.sessions.items():
            if current_time - session.last_activity > SESSION_TIMEOUT_SECONDS:
                inactive_user_ids.append(user_id)

        # If we have too many sessions, also remove oldest ones
        if len(self.sessions) > MAX_INACTIVE_SESSIONS:
            # Sort by last activity and get excess sessions
            sorted_sessions = sorted(
                self.sessions.items(),
                key=lambda x: x[1].last_activity,
            )
            excess_count = len(self.sessions) - MAX_INACTIVE_SESSIONS
            for user_id, _ in sorted_sessions[:excess_count]:
                if user_id not in inactive_user_ids:
                    inactive_user_ids.append(user_id)

        # Remove inactive sessions from memory and database
        for user_id in inactive_user_ids:
            del self.sessions[user_id]
            delete_user_session(user_id)

        # Also cleanup old sessions from database that aren't in memory
        db_cleaned = cleanup_old_sessions(SESSION_TIMEOUT_SECONDS)

        total_cleaned = len(inactive_user_ids) + db_cleaned

        if total_cleaned > 0:
            logger.info(
                f"Cleaned up {len(inactive_user_ids)} in-memory sessions, "
                f"{db_cleaned} database sessions. Active sessions: {len(self.sessions)}"
            )

        return total_cleaned

    def persist_all_sessions(self) -> None:
        """Persist all current sessions to the database (call before shutdown)."""
        logger.info(f"Persisting {len(self.sessions)} sessions to database")
        for session in self.sessions.values():
            self._persist_session(session)
        logger.info("All sessions persisted successfully")


# Global instances
user_manager = UserManager()
budget_manager = TokenBudgetManager()
