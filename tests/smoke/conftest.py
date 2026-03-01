"""Smoke test configuration.

Smoke tests hit real external APIs (Gemini) and are skipped by default when
the required credentials are not set as environment variables.

Run them explicitly with::

    GEMINI_API_KEY=... pytest -m smoke tests/smoke/ -v

Because the root ``tests/conftest.py`` replaces ``google.genai`` (and
potentially ``httpx``) in ``sys.modules`` with MagicMock stubs, this
conftest restores the *real* packages so that smoke tests can call the
actual APIs.
"""

from __future__ import annotations

import importlib
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Restore real third-party modules that the root conftest.py may have stubbed
# ---------------------------------------------------------------------------

_GOOGLE_MODULES = [
    "google",
    "google.genai",
    "google.genai.types",
    "google.genai.errors",
]

# PIL is also stubbed by the root conftest and google.genai.types imports it.
_PIL_MODULES = [
    "PIL",
    "PIL.Image",
]


def _is_stub(mod: types.ModuleType | None) -> bool:
    """Return True if *mod* is a plain stub (types.ModuleType created by
    _ensure_stub) or a MagicMock rather than a real installed package."""
    if mod is None:
        return True
    # MagicMock modules have no __file__ and their type is MagicMock
    if type(mod).__name__ == "MagicMock":
        return True
    # Stub modules created via types.ModuleType also lack __file__
    if not hasattr(mod, "__file__") and not hasattr(mod, "__path__"):
        return True
    return False


def _restore_real_modules() -> None:
    """Remove stubs for google.genai (and its dependency PIL) and
    re-import the real packages."""
    saved: dict[str, types.ModuleType] = {}

    # Remove PIL stubs first — google.genai.types tries ``import PIL`` and
    # handles ImportError gracefully, but a *broken* stub (MagicMock /
    # types.ModuleType without real attributes) causes AttributeError.
    # Removing the stub lets google.genai fall back to its own ImportError
    # handling when Pillow isn't installed.
    for name in _PIL_MODULES:
        if name in sys.modules and _is_stub(sys.modules[name]):
            saved[name] = sys.modules.pop(name)

    for name in _GOOGLE_MODULES:
        if name in sys.modules and _is_stub(sys.modules[name]):
            saved[name] = sys.modules.pop(name)

    # Now import the real package — importlib.import_module will populate
    # sys.modules with the real versions.
    try:
        importlib.import_module("google.genai")
    except ImportError:
        # If the real package isn't installed, restore the stubs so the rest
        # of the test-suite doesn't break.
        for name, mod in saved.items():
            if name not in sys.modules:
                sys.modules[name] = mod
        raise


# Run the restoration at conftest-load time (before any smoke test is
# collected).  The root conftest runs first so stubs are already in place.
_needs_restore = (
    any(_is_stub(sys.modules.get(m)) for m in _GOOGLE_MODULES)
    or any(_is_stub(sys.modules.get(m)) for m in _PIL_MODULES)
)
if _needs_restore:
    _restore_real_modules()


# ---------------------------------------------------------------------------
# Auto-skip smoke tests when credentials are missing
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip smoke-marked tests when the required env vars are absent."""
    gemini_key = os.environ.get("GEMINI_API_KEY")

    for item in items:
        markers = {m.name for m in item.iter_markers()}
        if "smoke" not in markers:
            continue

        # Check per-test which key is needed (module name heuristic)
        module_name = item.module.__name__ if item.module else ""

        if "gemini" in module_name and not gemini_key:
            item.add_marker(
                pytest.mark.skip(reason="GEMINI_API_KEY not set")
            )
