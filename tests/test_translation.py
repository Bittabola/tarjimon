"""Tests for the execute_translation() business logic."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from conftest import make_translation_deps
from handlers.translation import execute_translation
import strings as S


async def test_successful_translation():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", deps=deps,
    )
    assert result.success is True
    assert result.translated_text == "Translated text"
    assert result.token_count == 100


async def test_failure_on_zero_tokens():
    deps = make_translation_deps(
        translate=AsyncMock(return_value=("some text", 0, 0, 0)),
    )
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", deps=deps,
    )
    assert result.success is False


async def test_failure_on_error_text():
    deps = make_translation_deps(
        translate=AsyncMock(return_value=(S.GENERIC_ERROR, 50, 25, 25)),
    )
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", deps=deps,
    )
    assert result.success is False


async def test_exception_propagates():
    deps = make_translation_deps(
        translate=AsyncMock(side_effect=RuntimeError("API down")),
    )
    with pytest.raises(RuntimeError, match="API down"):
        await execute_translation(
            user_id=1, text_input="hello", image_data=None,
            image_mime_type="image/jpeg", deps=deps,
        )


async def test_content_type_text():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input="hello", image_data=None,
        image_mime_type="image/jpeg", deps=deps,
    )
    assert result.content_type == "text"


async def test_content_type_image():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input=None, image_data=b"\x89PNG",
        image_mime_type="image/png", deps=deps,
    )
    assert result.content_type == "image"


async def test_content_type_image_with_caption():
    deps = make_translation_deps()
    result = await execute_translation(
        user_id=1, text_input="caption", image_data=b"\x89PNG",
        image_mime_type="image/png", deps=deps,
    )
    assert result.content_type == "image_with_caption"
