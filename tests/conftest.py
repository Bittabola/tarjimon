"""Test configuration for local environments without full runtime dependencies."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Stub out heavy third-party modules that aren't needed for unit tests.
# This must happen *before* any application code is imported.
# ---------------------------------------------------------------------------

def _ensure_stub(module_name: str, attrs: dict | None = None) -> types.ModuleType:
    """Create a stub module (if not already loaded) and set attributes."""
    if module_name not in sys.modules:
        mod = types.ModuleType(module_name)
        if attrs:
            for k, v in attrs.items():
                setattr(mod, k, v)
        sys.modules[module_name] = mod
    return sys.modules[module_name]


# dotenv
_ensure_stub("dotenv", {"load_dotenv": lambda *a, **kw: None})

# telegram (python-telegram-bot) — use MagicMock so any attribute access works
_tg_mock = MagicMock()
for _tg_name in ("telegram", "telegram.error", "telegram.ext", "telegram.constants"):
    if _tg_name not in sys.modules:
        sys.modules[_tg_name] = _tg_mock
# telegram.error exceptions need to be real exception classes for `except` clauses
sys.modules["telegram.error"].BadRequest = type("BadRequest", (Exception,), {})
sys.modules["telegram.error"].TelegramError = type("TelegramError", (Exception,), {})

# google-genai
_ensure_stub("google", {})
_ensure_stub("google.genai", {"Client": MagicMock()})
_genai_types_mod = _ensure_stub("google.genai.types", {
    "Part": MagicMock(),
    "Content": MagicMock(),
    "FileData": MagicMock(),
    "GenerateContentConfig": MagicMock(),
})
# Stub google.genai.errors with real exception classes for `except` clauses
_api_error = type("APIError", (Exception,), {"code": 0, "status": "", "message": ""})
_genai_errors_mod = _ensure_stub("google.genai.errors", {
    "APIError": _api_error,
    "ClientError": type("ClientError", (_api_error,), {}),
    "ServerError": type("ServerError", (_api_error,), {}),
})
# Make `from google import genai` work
sys.modules["google"].genai = sys.modules["google.genai"]
sys.modules["google.genai"].types = _genai_types_mod
sys.modules["google.genai"].errors = _genai_errors_mod

# httpx — use the real package when installed (needed for integration tests
# that use ASGITransport); fall back to a stub only when it is missing.
try:
    import httpx as _httpx_real  # noqa: F401 — force into sys.modules
except ImportError:
    _ensure_stub("httpx", {
        "AsyncClient": MagicMock(),
        "ASGITransport": MagicMock(),
        "TimeoutException": type("TimeoutException", (Exception,), {}),
        "ConnectError": type("ConnectError", (Exception,), {}),
        "HTTPError": type("HTTPError", (Exception,), {}),
    })

# PIL / Pillow (imported by some modules)
_ensure_stub("PIL", {})
_ensure_stub("PIL.Image", {"open": MagicMock()})

# uvicorn (only needed at runtime, not during tests)
_ensure_stub("uvicorn", {"run": MagicMock()})

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def make_translation_deps(**overrides):
    """Build a TranslationDeps with all-passing defaults. Override any field by name."""
    from handlers.translation import TranslationDeps

    defaults = dict(
        ensure_subscription=MagicMock(),
        reserve_quota=MagicMock(return_value=True),
        refund_quota=MagicMock(return_value=True),
        translate=AsyncMock(return_value=("Translated text", 100, 50, 50)),
        log_usage=MagicMock(return_value=1),
        record_session_usage=MagicMock(),
        log_error=MagicMock(),
    )
    defaults.update(overrides)
    return TranslationDeps(**defaults)
