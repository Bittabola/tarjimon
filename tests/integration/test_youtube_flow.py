"""Integration tests for the YouTube summarization pipeline.

Tests cover:
- Full pipeline: URL → metadata → transcript → Gemini summary → formatted response
- Quota exhausted: user with 0 YouTube minutes gets upgrade prompt
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.integration.conftest import make_gemini_response
from tests.integration.helpers import make_text_update


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_httpx_response(*, status_code: int = 200, json_data: dict | None = None, text: str = ""):
    """Build a mock httpx.Response with .status_code, .json(), and .text."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _make_metadata_response(
    video_id: str = "dQw4w9WgXcQ",
    title: str = "Test Video Title",
    duration: int = 300,
    is_live: bool = False,
):
    """Build a metadata JSON response matching Supadata API format."""
    return _mock_httpx_response(
        status_code=200,
        json_data={
            "id": video_id,
            "title": title,
            "duration": duration,
            "isLive": is_live,
            "liveBroadcastContent": "none",
        },
    )


def _make_transcript_response(
    content: str = "This is the full transcript of the video.",
    lang: str = "en",
):
    """Build a transcript JSON response matching Supadata API format."""
    return _mock_httpx_response(
        status_code=200,
        json_data={
            "content": content,
            "lang": lang,
        },
    )


# ---------------------------------------------------------------------------
# Fixture: clear dedup cache
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_youtube_dedup_cache():
    """Clear the YouTube deduplication cache before and after each test."""
    from handlers.youtube import _youtube_processing_cache

    _youtube_processing_cache.clear()
    yield
    _youtube_processing_cache.clear()


# ---------------------------------------------------------------------------
# Test 1: Full pipeline
# ---------------------------------------------------------------------------

class TestYouTubeFullPipeline:
    """Test the complete YouTube summarization flow end-to-end."""

    async def test_youtube_summarization_full_pipeline(self, tmp_db, patch_gemini, monkeypatch):
        """YouTube URL -> metadata -> transcript -> Gemini summary -> response sent.

        Mocks:
        - httpx.AsyncClient for metadata and transcript fetches
        - Gemini client for summarization (via patch_gemini fixture)
        - SUPADATA_API_KEY must be set so fetch functions don't bail out early
        """
        import config
        from handlers.youtube import summarize_youtube

        # Ensure SUPADATA_API_KEY is set so fetch functions proceed
        monkeypatch.setattr(config, "SUPADATA_API_KEY", "test-supadata-key")
        # Also patch the module-level import in handlers.youtube
        import handlers.youtube as yt_mod
        monkeypatch.setattr(yt_mod, "SUPADATA_API_KEY", "test-supadata-key")

        user_id = 70001
        video_id = "dQw4w9WgXcQ"
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"

        # -- Set up the database: free user with enough minutes --
        from database import ensure_free_user_subscription
        ensure_free_user_subscription(user_id, youtube_minutes=30, translations=10)

        # -- Build update/context mocks --
        update, context = make_text_update(
            text=youtube_url,
            user_id=user_id,
            chat_id=user_id,
        )

        # -- Prepare httpx mock --
        # The handler calls fetch_youtube_metadata and fetch_youtube_transcript,
        # each of which does `async with httpx.AsyncClient(...) as client: response = await client.get(...)`
        # We mock the AsyncClient context manager to return controlled responses.

        metadata_resp = _make_metadata_response(
            video_id=video_id,
            title="Test Video Title",
            duration=300,  # 5 minutes -> billable = 5 min (with transcript)
        )
        transcript_resp = _make_transcript_response(
            content="This is a test transcript for the video.",
            lang="en",
        )

        # Track which URLs are being called to return appropriate responses
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "youtube/video" in url:
                return metadata_resp
            elif "transcript" in url:
                return transcript_resp
            return _mock_httpx_response(status_code=404, text="Not Found")

        mock_client_instance = MagicMock()
        mock_client_instance.get = AsyncMock(side_effect=mock_get)

        # Make the AsyncClient context manager return our mock
        mock_async_client = MagicMock()
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=False)

        # -- Configure Gemini mock --
        # The handler uses asyncio.to_thread(get_gemini_client().models.generate_content, ...)
        # patch_gemini patches get_gemini_client. We need to set up
        # the .models.generate_content (sync) side.
        gemini_summary_text = (
            "SARLAVHA: Test Video Title\n"
            "XULOSA: Bu video haqida qisqacha xulosa.\n"
            "ASOSIY FIKRLAR:\n- Birinchi fikr\n- Ikkinchi fikr\n"
            "SAVOLLAR:\n- Savol bir?\n- Savol ikki?"
        )
        gemini_response = make_gemini_response(text=gemini_summary_text)
        # asyncio.to_thread calls the sync .models.generate_content
        patch_gemini.models.generate_content = MagicMock(return_value=gemini_response)

        with patch("handlers.youtube.httpx.AsyncClient", mock_async_client):
            await summarize_youtube(update, context)

        # -- Assertions --
        # 1. The bot should have called reply_text for the initial status message
        update.message.reply_text.assert_called()

        # 2. The bot should have edited the message via safe_edit_message_text
        # which calls context.bot.edit_message_text
        assert context.bot.edit_message_text.call_count >= 2  # at least status + final

        # 3. The final edit should contain the formatted summary
        last_edit_call = context.bot.edit_message_text.call_args_list[-1]
        final_text = last_edit_call.kwargs.get("text", "")
        # The formatted output should contain the title and summary sections
        assert "Test Video Title" in final_text
        assert "Xulosa" in final_text or "xulosa" in final_text.lower()

        # 4. Reply markup should include question buttons
        final_markup = last_edit_call.kwargs.get("reply_markup")
        assert final_markup is not None

        # 5. Context chat_data should store questions and transcript
        assert f"yt_questions_{youtube_url}" in context.chat_data
        assert f"yt_transcript_{youtube_url}" in context.chat_data

        # 6. Both httpx calls should have been made (metadata + transcript)
        assert call_count == 2


# ---------------------------------------------------------------------------
# Test 2: Quota exhausted
# ---------------------------------------------------------------------------

class TestYouTubeQuotaExhausted:
    """Test that a user with no remaining YouTube minutes is blocked."""

    async def test_youtube_quota_exhausted(self, tmp_db, patch_gemini, monkeypatch):
        """User with 0 YouTube minutes receives an upgrade prompt, no API calls.

        The handler should:
        1. Fetch metadata (to get duration)
        2. Fetch transcript (to determine billing)
        3. Check limits -> find 0 remaining -> show upgrade prompt
        4. NOT call Gemini at all
        """
        import config
        from handlers.youtube import summarize_youtube

        # Ensure SUPADATA_API_KEY is set
        monkeypatch.setattr(config, "SUPADATA_API_KEY", "test-supadata-key")
        import handlers.youtube as yt_mod
        monkeypatch.setattr(yt_mod, "SUPADATA_API_KEY", "test-supadata-key")

        user_id = 70002
        video_id = "xYz123AbCdE"
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"

        # -- Set up database: free user with 0 YouTube minutes remaining --
        from database import ensure_free_user_subscription

        ensure_free_user_subscription(user_id, youtube_minutes=0, translations=10)

        # Verify the user actually has 0 minutes
        from database import get_user_subscription
        sub = get_user_subscription(user_id)
        assert sub is not None
        assert sub["youtube_minutes_remaining"] == 0

        # -- Build update/context --
        update, context = make_text_update(
            text=youtube_url,
            user_id=user_id,
            chat_id=user_id,
        )

        # -- Prepare httpx mock for metadata + transcript --
        metadata_resp = _make_metadata_response(
            video_id=video_id,
            title="Quota Test Video",
            duration=600,  # 10 minutes
        )
        transcript_resp = _make_transcript_response(
            content="Some transcript text here.",
            lang="en",
        )

        async def mock_get(url, **kwargs):
            if "youtube/video" in url:
                return metadata_resp
            elif "transcript" in url:
                return transcript_resp
            return _mock_httpx_response(status_code=404, text="Not Found")

        mock_client_instance = MagicMock()
        mock_client_instance.get = AsyncMock(side_effect=mock_get)

        mock_async_client = MagicMock()
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("handlers.youtube.httpx.AsyncClient", mock_async_client):
            await summarize_youtube(update, context)

        # -- Assertions --
        # 1. The bot should have sent a status message first
        update.message.reply_text.assert_called()

        # 2. The bot should have edited the message with a limit exceeded prompt
        assert context.bot.edit_message_text.called

        # Find the edit call that contains the limit exceeded text
        limit_call_found = False
        for call in context.bot.edit_message_text.call_args_list:
            call_text = call.kwargs.get("text", "")
            # The limit exceeded messages contain keywords about limits/subscription
            if "limit" in call_text.lower() or "paket" in call_text.lower() or "obuna" in call_text.lower():
                limit_call_found = True
                # Should include a subscribe/upgrade button
                markup = call.kwargs.get("reply_markup")
                assert markup is not None
                break

        assert limit_call_found, (
            "Expected a limit-exceeded message but none of the edit_message_text calls "
            "contained limit/subscription text. Calls: "
            + str([c.kwargs.get("text", "")[:100] for c in context.bot.edit_message_text.call_args_list])
        )

        # 3. Gemini should NOT have been called (no summarization)
        patch_gemini.models.generate_content.assert_not_called()
        patch_gemini.aio.models.generate_content.assert_not_called()
