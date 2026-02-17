"""Tests for the execute_translation() business logic."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from conftest import make_translation_deps
from handlers.translation import execute_translation
import strings as S


async def test_successful_translation():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    assert result.success is True
    assert result.translated_text == "Translated text"
    assert result.token_count == 100
    deps.log_usage.assert_called_once()
    deps.record_session_usage.assert_called_once_with(1, 100)
    deps.refund_quota.assert_not_called()


async def test_refund_on_zero_tokens():
    deps = make_translation_deps(
        translate=AsyncMock(return_value=("some text", 0, 0, 0)),
    )
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    assert result.success is False
    deps.refund_quota.assert_called_once_with(1, 1)


async def test_refund_on_error_text():
    deps = make_translation_deps(
        translate=AsyncMock(return_value=(S.GENERIC_ERROR, 50, 25, 25)),
    )
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    assert result.success is False
    deps.refund_quota.assert_called_once_with(1, 1)


async def test_refund_on_exception():
    deps = make_translation_deps(
        translate=AsyncMock(side_effect=RuntimeError("API down")),
    )
    with pytest.raises(RuntimeError, match="API down"):
        await execute_translation(
            user_id=1, text_input="hello", image_data=None,
            image_mime_type="image/jpeg", is_premium=False, deps=deps,
        )
    deps.refund_quota.assert_called_once_with(1, 1)


async def test_no_refund_on_success():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    assert result.success is True
    deps.refund_quota.assert_not_called()


async def test_reservation_failure():
    deps = make_translation_deps(
        reserve_quota=MagicMock(return_value=False),
    )
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    assert result.success is False
    deps.translate.assert_not_called()
    deps.refund_quota.assert_not_called()


async def test_free_user_ensures_subscription():
    deps = make_translation_deps()
    await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    deps.ensure_subscription.assert_called_once_with(1)


async def test_premium_skips_subscription():
    deps = make_translation_deps()
    await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=True, deps=deps,
    )
    deps.ensure_subscription.assert_not_called()


async def test_content_type_text():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    assert result.content_type == "text"


async def test_content_type_image():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input=None, image_data=b"\x89PNG",
        image_mime_type="image/png", is_premium=False, deps=deps,
    )
    assert result.content_type == "image"


async def test_content_type_image_with_caption():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input="caption", image_data=b"\x89PNG",
        image_mime_type="image/png", is_premium=False, deps=deps,
    )
    assert result.content_type == "image_with_caption"


async def test_usage_not_logged_on_failure():
    deps = make_translation_deps(
        translate=AsyncMock(return_value=("some text", 0, 0, 0)),
    )
    await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", is_premium=False, deps=deps,
    )
    deps.log_usage.assert_not_called()
    deps.record_session_usage.assert_not_called()
