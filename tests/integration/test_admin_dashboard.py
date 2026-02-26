"""Integration tests for the admin dashboard (HTTP Basic Auth).

Tests:
- GET /admin/ without Authorization header returns 401
- GET /admin/ with wrong Basic Auth credentials returns 401
- GET /admin/ with correct Basic Auth credentials returns 200 + HTML
"""

from __future__ import annotations

import base64

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    """Build an HTTP Basic Auth header dict."""
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

TEST_ADMIN_USER = "admin"
TEST_ADMIN_PASS = "test-admin-pass-789"


# ---------------------------------------------------------------------------
# Fixture: async HTTP client with admin credentials patched
# ---------------------------------------------------------------------------

@pytest.fixture()
async def client(tmp_db, monkeypatch):
    """Create an ``httpx.AsyncClient`` connected to the webhook FastAPI app.

    Monkeypatches ``ADMIN_PASSWORD`` (and ``ADMIN_USERNAME``) on both the
    ``config`` module and the ``admin_dashboard`` module so the Basic Auth
    guard uses known test values.

    Depends on ``tmp_db`` to ensure the database is initialised before any
    request that touches it.
    """
    import config
    import admin_dashboard

    monkeypatch.setattr(config, "ADMIN_USERNAME", TEST_ADMIN_USER)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", TEST_ADMIN_PASS)
    monkeypatch.setattr(admin_dashboard, "ADMIN_USERNAME", TEST_ADMIN_USER)
    monkeypatch.setattr(admin_dashboard, "ADMIN_PASSWORD", TEST_ADMIN_PASS)

    from webhook import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_admin_no_auth_returns_401(client: httpx.AsyncClient):
    """GET /admin/ without an Authorization header must return 401."""
    response = await client.get("/admin/")
    assert response.status_code == 401


async def test_admin_wrong_password_returns_401(client: httpx.AsyncClient):
    """GET /admin/ with incorrect Basic Auth credentials must return 401."""
    headers = _basic_auth_header(TEST_ADMIN_USER, "wrong-password")
    response = await client.get("/admin/", headers=headers)
    assert response.status_code == 401


async def test_admin_valid_auth_returns_200(client: httpx.AsyncClient):
    """GET /admin/ with correct credentials must return 200 and HTML content."""
    headers = _basic_auth_header(TEST_ADMIN_USER, TEST_ADMIN_PASS)
    response = await client.get("/admin/", headers=headers)
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Tarjimon Admin" in response.text
