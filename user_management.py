"""
User Session Management and Rate Limiting

This module provides user session management with request rate limiting
and content size validation.

Features:
- Request rate limiting (10 requests/minute)
- Content size validation
- Session persistence across restarts
"""

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime as dt, timezone
import strings as S
from config import logger
from constants import (
    RATE_LIMITS,
    TEXT_LIMITS,
    IMAGE_LIMITS,
    SESSION_CONSTANTS,
)
from database import (
    save_user_session,
    load_user_session,
    delete_user_session,
    cleanup_old_sessions,
)


@dataclass
class UserSession:
    """User session data structure."""

    user_id: int
    last_activity: float
    request_count: int
    request_timestamps: deque = field(default_factory=lambda: deque(maxlen=60))

    def to_dict(self) -> dict:
        """Convert session to dictionary for serialization."""
        return {
            "user_id": self.user_id,
            "last_activity": self.last_activity,
            "request_count": self.request_count,
            "request_timestamps": list(self.request_timestamps),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserSession":
        """Create session from dictionary."""
        session = cls(
            user_id=data["user_id"],
            last_activity=data["last_activity"],
            request_count=data["request_count"],
        )
        # Restore request timestamps
        timestamps = data.get("request_timestamps", [])
        for ts in timestamps:
            session.request_timestamps.append(ts)
        return session


class UserManager:
    """Manages user sessions and rate limiting with database persistence."""

    def __init__(self):
        self.sessions: dict[int, UserSession] = {}
        self.rate_limits = {
            "requests_per_minute": RATE_LIMITS.REQUESTS_PER_MINUTE,
            "max_text_length": TEXT_LIMITS.MAX_TEXT_LENGTH,
            "max_image_size_mb": IMAGE_LIMITS.MAX_IMAGE_SIZE_MB,
        }
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

            # Convert request timestamps to JSON
            timestamps_json = json.dumps(list(session.request_timestamps))

            save_user_session(
                user_id=session.user_id,
                last_activity=last_activity_iso,
                request_count=session.request_count,
                request_timestamps=timestamps_json,
            )
        except Exception as e:
            logger.error(f"Error persisting session for user {session.user_id}: {e}")

    def _load_session_from_db(self, user_id: int) -> UserSession | None:
        """Load a session from the database if it exists."""
        try:
            data = load_user_session(user_id)
            if not data:
                return None

            # Parse ISO timestamps back to floats
            last_activity = dt.fromisoformat(data["last_activity"]).timestamp()

            # Parse request timestamps from JSON
            request_timestamps_list: list[float] = []
            if data.get("request_timestamps"):
                try:
                    request_timestamps_list = json.loads(data["request_timestamps"])
                except json.JSONDecodeError:
                    pass

            session = UserSession(
                user_id=user_id,
                last_activity=last_activity,
                request_count=data["request_count"],
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
                if (
                    current_time - db_session.last_activity
                    <= SESSION_CONSTANTS.TIMEOUT_SECONDS
                ):
                    self.sessions[user_id] = db_session
                    logger.debug(f"Restored session for user {user_id} from database")
                else:
                    # Session expired, delete from database
                    delete_user_session(user_id)
                    db_session = None

            if not db_session:
                # Create new session
                self.sessions[user_id] = UserSession(
                    user_id=user_id,
                    last_activity=current_time,
                    request_count=0,
                )
                logger.info(f"Created new session for user {user_id}")

        session = self.sessions[user_id]
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

    def check_rate_limit(self, user_id: int) -> tuple[bool, str | None]:
        """Check if user has exceeded request rate limits."""
        session = self.get_or_create_session(user_id)
        current_time = time.time()

        # Count recent requests BEFORE appending
        recent_requests = sum(
            1 for ts in session.request_timestamps if current_time - ts < 60
        )

        if recent_requests >= self.rate_limits["requests_per_minute"]:
            logger.warning(
                f"User {user_id} exceeded rate limit: {recent_requests} requests/minute"
            )
            error_msg = S.TOO_MANY_REQUESTS.format(
                recent=recent_requests,
                limit=self.rate_limits["requests_per_minute"],
            )
            return False, error_msg

        # Only record timestamp for allowed requests
        session.request_timestamps.append(current_time)
        return True, None

    def check_text_length(self, text: str) -> tuple[bool, str | None]:
        """Check if text length is within limits."""
        if len(text) > self.rate_limits["max_text_length"]:
            error_msg = S.TEXT_TOO_LONG.format(
                actual=len(text),
                limit=self.rate_limits["max_text_length"],
            )
            return False, error_msg
        return True, None

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
            if current_time - session.last_activity > SESSION_CONSTANTS.TIMEOUT_SECONDS:
                inactive_user_ids.append(user_id)

        # If we have too many sessions, also remove oldest ones
        if len(self.sessions) > SESSION_CONSTANTS.MAX_INACTIVE_SESSIONS:
            # Sort by last activity and get excess sessions
            sorted_sessions = sorted(
                self.sessions.items(),
                key=lambda x: x[1].last_activity,
            )
            excess_count = len(self.sessions) - SESSION_CONSTANTS.MAX_INACTIVE_SESSIONS
            for user_id, _ in sorted_sessions[:excess_count]:
                if user_id not in inactive_user_ids:
                    inactive_user_ids.append(user_id)

        # Remove inactive sessions from memory and database
        for user_id in inactive_user_ids:
            del self.sessions[user_id]
            delete_user_session(user_id)

        # Also cleanup old sessions from database that aren't in memory
        db_cleaned = cleanup_old_sessions(SESSION_CONSTANTS.TIMEOUT_SECONDS)

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


# Global instance
user_manager = UserManager()
