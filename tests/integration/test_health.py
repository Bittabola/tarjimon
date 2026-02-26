"""Integration tests for the health and root endpoints.

Tests:
- GET / returns 200 with {"status": "running"}
- GET /health returns 200 with healthy DB status
- GET /health returns 503 when DB connection fails
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest


@pytest.fixture()
async def client(tmp_db):
    """Async HTTP client wired to the FastAPI ``app`` from webhook.py.

    Depends on ``tmp_db`` so the database is initialised before any request
    that touches it.  Uses ``httpx.ASGITransport`` so requests are handled
    in-process without starting a real server.
    """
    from webhook import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_root_returns_running(client):
    """GET / returns 200 with {"status": "running"}."""
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"


async def test_health_healthy_db(client):
    """GET /health with a working DB returns 200 and database: ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["checks"]["database"] == "ok"


async def test_health_degraded_db(client):
    """GET /health when DB connection fails returns 503 with degraded status."""
    import database

    def _broken_get_connection(self):
        import sqlite3
        raise sqlite3.OperationalError("disk I/O error")

    with patch.object(
        database.DatabaseManager, "get_connection", _broken_get_connection
    ):
        response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert "error" in body["checks"]["database"]
