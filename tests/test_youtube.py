"""Tests for the execute_youtube_summary() business logic."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from conftest import make_youtube_summary_deps
from handlers.youtube import execute_youtube_summary


async def test_successful_summarization():
    deps = make_youtube_summary_deps()
    result = await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text="some transcript", billable_minutes=5,
        is_premium=False, deps=deps,
    )
    assert result.success is True
    assert result.summary == "Summary text"
    assert result.token_count == 200
    deps.log_usage.assert_called_once()
    deps.record_session_usage.assert_called_once_with(1, 200)
    deps.refund_minutes.assert_not_called()


async def test_refund_on_zero_tokens():
    deps = make_youtube_summary_deps(
        summarize=AsyncMock(return_value=("error text", 0, 0, 0, None)),
    )
    result = await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=5,
        is_premium=False, deps=deps,
    )
    assert result.success is False
    deps.refund_minutes.assert_called_once_with(1, 5)


async def test_refund_on_exception():
    deps = make_youtube_summary_deps(
        summarize=AsyncMock(side_effect=RuntimeError("API down")),
    )
    with pytest.raises(RuntimeError, match="API down"):
        await execute_youtube_summary(
            user_id=1, youtube_url="https://youtube.com/watch?v=abc",
            transcript_text=None, billable_minutes=10,
            is_premium=False, deps=deps,
        )
    deps.refund_minutes.assert_called_once_with(1, 10)


async def test_no_refund_on_success():
    deps = make_youtube_summary_deps()
    result = await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=5,
        is_premium=False, deps=deps,
    )
    assert result.success is True
    deps.refund_minutes.assert_not_called()


async def test_reservation_failure():
    deps = make_youtube_summary_deps(
        reserve_minutes=MagicMock(return_value=False),
    )
    result = await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=5,
        is_premium=False, deps=deps,
    )
    assert result.success is False
    deps.summarize.assert_not_called()
    deps.refund_minutes.assert_not_called()


async def test_free_user_ensures_subscription():
    deps = make_youtube_summary_deps()
    await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=5,
        is_premium=False, deps=deps,
    )
    deps.ensure_subscription.assert_called_once_with(1)


async def test_premium_skips_subscription():
    deps = make_youtube_summary_deps()
    await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=5,
        is_premium=True, deps=deps,
    )
    deps.ensure_subscription.assert_not_called()


async def test_returned_transcript_preserved():
    deps = make_youtube_summary_deps(
        summarize=AsyncMock(
            return_value=("Summary", 200, 100, 100, "generated transcript")
        ),
    )
    result = await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=5,
        is_premium=False, deps=deps,
    )
    assert result.returned_transcript == "generated transcript"


async def test_request_id_from_log_usage():
    deps = make_youtube_summary_deps(
        log_usage=MagicMock(return_value=99),
    )
    result = await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=5,
        is_premium=False, deps=deps,
    )
    assert result.request_id == 99


async def test_usage_logged_with_billable_minutes():
    deps = make_youtube_summary_deps()
    await execute_youtube_summary(
        user_id=1, youtube_url="https://youtube.com/watch?v=abc",
        transcript_text=None, billable_minutes=7,
        is_premium=False, deps=deps,
    )
    call_kwargs = deps.log_usage.call_args
    assert call_kwargs[1]["video_duration_minutes"] == 7
