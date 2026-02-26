"""Integration test fixtures for the tarjimon bot.

Provides:
- ``tmp_db`` -- function-scoped fixture that creates a fresh SQLite database
  in a temporary directory, isolating each test from production data.
- ``make_gemini_response`` -- helper function (not a fixture) that builds a
  MagicMock resembling a Gemini API response.
- ``patch_gemini`` -- fixture that patches the Gemini client in
  ``handlers.common`` so no real API calls are made.
- ``reset_feedback_state`` -- autouse fixture that clears feedback pending
  users between tests.
- ``reset_user_sessions`` -- autouse fixture that clears user session data
  between tests.

These fixtures build on top of the root ``tests/conftest.py`` which stubs out
heavy third-party modules (telegram, google-genai, httpx, PIL) before any
application code is imported.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a fake Gemini API response
# ---------------------------------------------------------------------------

def make_gemini_response(
    text: str = "Translated text",
    total_tokens: int = 100,
    input_tokens: int = 50,
    output_tokens: int = 50,
) -> MagicMock:
    """Build a ``MagicMock`` that resembles a Gemini ``GenerateContentResponse``.

    The mock provides:
    - ``.text`` -- the response text (used by fallback paths).
    - ``.usage_metadata.total_token_count`` / ``.prompt_token_count`` /
      ``.candidates_token_count`` -- token accounting fields.
    - ``.candidates[0].content.parts[0].text`` -- the text accessible via
      the ``extract_gemini_response_text`` helper.
    - ``.candidates[0].content.parts[0].thought`` -- set to ``False`` so the
      part is not skipped by thinking-model filtering.

    Args:
        text: The response text to embed in the mock.
        total_tokens: Total token count reported by the API.
        input_tokens: Input (prompt) token count.
        output_tokens: Output (candidates) token count.

    Returns:
        A ``MagicMock`` configured to look like a Gemini response.
    """
    response = MagicMock()

    # Top-level .text shortcut
    response.text = text

    # Usage metadata
    response.usage_metadata.total_token_count = total_tokens
    response.usage_metadata.prompt_token_count = input_tokens
    response.usage_metadata.candidates_token_count = output_tokens

    # Candidates structure for extract_gemini_response_text
    part = MagicMock()
    part.text = text
    part.thought = False

    response.candidates = [MagicMock()]
    response.candidates[0].content.parts = [part]

    return response


# ---------------------------------------------------------------------------
# Fixture: temporary database
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Create a fresh SQLite database in a temporary directory.

    Monkeypatches ``database.DATABASE_FILE`` so that all database operations
    during the test target the temporary file.  Resets the
    ``DatabaseManager`` singleton so it picks up the new path.

    Yields the ``pathlib.Path`` to the temporary database file.
    """
    import database

    db_file = str(tmp_path / "test.db")

    monkeypatch.setattr(database, "DATABASE_FILE", db_file)

    # Reset singleton so __init__ re-runs with the patched path
    database.DatabaseManager._instance = None

    result = database.init_db()
    assert result is True, "init_db() must succeed for the temp database"

    yield tmp_path / "test.db"

    # Reset the singleton so subsequent tests start fresh.
    # DatabaseManager uses per-call connections (via context manager) so there
    # is no persistent connection to close, but we clear the initialized flag
    # to ensure the next instantiation re-runs __init__.
    instance = database.DatabaseManager._instance
    database.DatabaseManager._instance = None
    if instance is not None:
        instance.initialized = False


# ---------------------------------------------------------------------------
# Fixture: patch Gemini client in handlers.common
# ---------------------------------------------------------------------------

@pytest.fixture()
def patch_gemini():
    """Patch the Gemini client so no real API calls are made.

    Patches three attributes in ``handlers.common``:
    - ``_gemini_client`` -- replaced with a ``MagicMock`` whose
      ``aio.models.generate_content`` is an ``AsyncMock`` returning a
      default ``make_gemini_response()``.
    - ``_gemini_client_lock`` -- replaced with a ``MagicMock`` to prevent
      any real threading lock acquisition.
    - ``get_gemini_client`` -- replaced to always return the mock client.

    Yields the mock client so tests can customise its return values.
    """
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=make_gemini_response(),
    )

    with (
        patch("handlers.common._gemini_client", mock_client),
        patch("handlers.common._gemini_client_lock", MagicMock()),
        patch("handlers.common.get_gemini_client", return_value=mock_client),
    ):
        yield mock_client


# ---------------------------------------------------------------------------
# Autouse fixture: reset feedback pending-users set
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_feedback_state():
    """Clear the feedback pending-users set before and after each test."""
    from handlers import feedback

    feedback._feedback_pending_users.clear()
    yield
    feedback._feedback_pending_users.clear()


# ---------------------------------------------------------------------------
# Autouse fixture: reset user session data
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_user_sessions():
    """Clear user session data before and after each test."""
    from user_management import user_manager

    user_manager.sessions.clear()
    yield
    user_manager.sessions.clear()
