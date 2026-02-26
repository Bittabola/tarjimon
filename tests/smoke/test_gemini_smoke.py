"""Smoke tests for the real Google Gemini API.

These tests are skipped unless GEMINI_API_KEY is set in the environment.
Run with::

    GEMINI_API_KEY=... pytest -m smoke tests/smoke/test_gemini_smoke.py -v
"""

from __future__ import annotations

import os

import pytest

# The smoke conftest restores the real google.genai before collection,
# so this import gets the genuine package.
import google.genai as genai

pytestmark = [pytest.mark.smoke]

_SKIP_REASON = "GEMINI_API_KEY environment variable not set"

# Model to use for smoke tests — use a fast, cheap model.
_SMOKE_MODEL = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.0-flash")


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason=_SKIP_REASON)
def test_gemini_translates_to_uzbek() -> None:
    """Send a short English phrase and verify the model returns a non-empty
    Uzbek translation."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = (
        "You are an expert translation bot. Translate the provided text to Uzbek.\n\n"
        "1. Analyze the language of the provided text\n"
        "2. If already in Uzbek (Latin or Cyrillic), respond: "
        "\"Bu matn allaqachon o'zbek tilida.\"\n"
        "3. If in another language, translate accurately to Uzbek (Latin script)\n\n"
        "Provide only the translation or status message, no additional formatting.\n\n"
        'Here is the text input to use: """Hello, how are you?"""'
    )

    response = client.models.generate_content(
        model=_SMOKE_MODEL,
        contents=prompt,
    )

    assert response.text is not None, "Gemini returned None text"
    text = response.text.strip()
    assert len(text) > 0, "Gemini returned empty response"
    # Should not be an error string
    assert "error" not in text.lower() or "xato" in text.lower() or len(text) > 20, (
        f"Gemini response looks like an error: {text!r}"
    )


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason=_SKIP_REASON)
def test_gemini_summarizes_transcript() -> None:
    """Send a short English transcript with the Uzbek summarization prompt
    and verify we get a non-empty response."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    transcript = (
        "Today we are going to talk about Python programming. "
        "Python is a versatile programming language that is widely used "
        "in web development, data science, and artificial intelligence. "
        "It was created by Guido van Rossum and first released in 1991. "
        "Python emphasizes code readability and simplicity."
    )

    prompt = (
        "Siz YouTube videolarini o'zbek tilida umumlashtiruvchi yordamchisiz.\n\n"
        f"Quyida video transkripsiyasi berilgan:\n\nTRANSKRIPSIYA:\n{transcript}\n\n"
        "Vazifa:\n"
        "1. Mazmunni o'qib, asosiy g'oyalarni tushunib oling\n"
        "2. Sarlavha yozing\n"
        "3. Qisqacha mazmunini o'zbek tilida yozing (lotin alifbosida)\n"
        "4. Asosiy fikrlarni sanab o'ting\n\n"
        "Javob formati (aynan shu formatda yozing, emoji ishlatmang):\n\n"
        "SARLAVHA: [Mavzuga mos sarlavha]\n\n"
        "XULOSA: [2-3 jumlada umumiy mazmun]\n\n"
        "ASOSIY FIKRLAR:\n"
        "- [Birinchi muhim fikr]\n"
        "- [Ikkinchi muhim fikr]\n"
        "- [Uchinchi muhim fikr]\n"
    )

    response = client.models.generate_content(
        model=_SMOKE_MODEL,
        contents=prompt,
    )

    assert response.text is not None, "Gemini returned None text"
    text = response.text.strip()
    assert len(text) > 0, "Gemini returned empty response"
