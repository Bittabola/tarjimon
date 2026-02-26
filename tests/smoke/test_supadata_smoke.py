"""Smoke test for the real Supadata transcript API.

Skipped unless SUPADATA_API_KEY is set in the environment.
Run with::

    SUPADATA_API_KEY=... pytest -m smoke tests/smoke/test_supadata_smoke.py -v
"""

from __future__ import annotations

import os

import pytest
import httpx

pytestmark = [pytest.mark.smoke]

_SKIP_REASON = "SUPADATA_API_KEY environment variable not set"


@pytest.mark.skipif(not os.environ.get("SUPADATA_API_KEY"), reason=_SKIP_REASON)
def test_supadata_fetches_transcript() -> None:
    """Fetch the transcript for a well-known YouTube video (Rick Astley -
    Never Gonna Give You Up) and verify we get non-empty content back."""
    api_key = os.environ["SUPADATA_API_KEY"]
    video_id = "dQw4w9WgXcQ"
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"

    with httpx.Client(timeout=30) as client:
        response = client.get(
            "https://api.supadata.ai/v1/transcript",
            params={
                "url": youtube_url,
                "mode": "native",
                "text": "true",
            },
            headers={"x-api-key": api_key},
        )

    assert response.status_code == 200, (
        f"Supadata API returned status {response.status_code}: "
        f"{response.text[:300]}"
    )

    data = response.json()
    content = data.get("content", "")
    assert len(content) > 0, "Supadata returned empty transcript content"
    # Rick Astley's song should contain recognizable lyrics
    assert len(content) > 50, (
        f"Transcript suspiciously short ({len(content)} chars): {content[:200]}"
    )
