"""Translation handlers for the Tarjimon bot."""

from __future__ import annotations

import io
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import (
    logger,
    GEMINI_MODEL_NAME,
    SUBSCRIPTION_PLAN,
    PROMPTS,
)
from constants import IMAGE_LIMITS, SUBSCRIPTION_LIMITS
from database import (
    log_token_usage_to_db,
    is_user_premium,
    get_user_subscription,
    get_user_remaining_limits,
    decrement_translation_limit,
    increment_translation_limit,
)
from user_management import user_manager
import strings as S
from utils import safe_html
from google.genai import types

from .common import (
    get_gemini_client,
    get_stats_button,
    log_error_with_context,
    ensure_free_user_sub,
    split_message,
)


def _format_translation_output(
    translated_text: str, has_image: bool, has_caption: bool
) -> str:
    """
    Format translation output with appropriate titles, handling structured responses.

    Args:
        translated_text: The translation result from Gemini
        has_image: Whether the message contains an image
        has_caption: Whether the image has a caption

    Returns:
        Formatted string with appropriate titles and sections
    """
    logger.info(
        f"Formatting output: has_image={has_image}, has_caption={has_caption}, text_length={len(translated_text)}"
    )

    if not translated_text:
        return S.GENERIC_ERROR

    # Handle structured response for image + caption
    if has_image and has_caption:
        if "IMAGE_TEXT:" in translated_text and "CAPTION_TEXT:" in translated_text:
            logger.info("Detected structured response, parsing...")
            parsed_result = _parse_structured_response(translated_text)
            if (
                parsed_result and len(parsed_result.strip()) > 10
            ):  # Ensure we got meaningful content
                return parsed_result
            else:
                logger.warning(
                    "Structured parsing failed or returned minimal content, using fallback"
                )

        # Fallback: treat as combined content
        logger.info("Using fallback formatting for image+caption")
        return S.LABEL_IMAGE_AND_TEXT_TRANSLATION + safe_html(translated_text)

    # Handle single content types
    if has_image and not has_caption:
        # Image only (OCR extraction)
        if "allaqachon o'zbek tilida" in translated_text.lower():
            return S.LABEL_IMAGE_RESULT + safe_html(translated_text)
        elif "rasmda matn topilmadi" in translated_text.lower():
            return S.LABEL_IMAGE_RESULT + safe_html(translated_text)
        else:
            return S.LABEL_IMAGE_TRANSLATION + safe_html(translated_text)
    elif not has_image:
        # Text message only
        if "allaqachon o'zbek tilida" in translated_text.lower():
            return S.LABEL_TEXT_RESULT + safe_html(translated_text)
        else:
            return S.LABEL_TEXT_TRANSLATION + safe_html(translated_text)
    else:
        # Fallback for any other case
        return safe_html(translated_text)


def _parse_structured_response(response: str) -> str:
    """
    Parse structured response with IMAGE_TEXT and CAPTION_TEXT sections.

    Args:
        response: Structured response from Gemini

    Returns:
        Formatted output with separate sections
    """
    try:
        logger.info(f"Parsing structured response: {response[:300]}...")

        # Use regex to extract content that might span multiple lines
        image_match = re.search(
            r"IMAGE_TEXT:\s*(.*?)(?=\n\s*CAPTION_TEXT:|$)", response, re.DOTALL
        )
        caption_match = re.search(r"CAPTION_TEXT:\s*(.*?)$", response, re.DOTALL)

        image_text = image_match.group(1).strip() if image_match else ""
        caption_text = caption_match.group(1).strip() if caption_match else ""

        logger.info(f"Extracted IMAGE_TEXT: {image_text[:100]}...")
        logger.info(f"Extracted CAPTION_TEXT: {caption_text[:100]}...")

        # Format the output with separate sections
        output_parts = []

        # Add image section (escape HTML in AI-generated content)
        if image_text:
            escaped_image_text = safe_html(image_text)
            if "allaqachon o'zbek tilida" in image_text.lower():
                output_parts.append(S.LABEL_IMAGE_RESULT + escaped_image_text)
            elif "rasmda matn topilmadi" in image_text.lower():
                output_parts.append(S.LABEL_IMAGE_RESULT + escaped_image_text)
            else:
                output_parts.append(S.LABEL_IMAGE_TRANSLATION + escaped_image_text)

        # Add caption section (escape HTML in AI-generated content)
        if caption_text:
            escaped_caption_text = safe_html(caption_text)
            if "allaqachon o'zbek tilida" in caption_text.lower():
                output_parts.append(S.LABEL_TEXT_RESULT + escaped_caption_text)
            else:
                output_parts.append(S.LABEL_TEXT_TRANSLATION + escaped_caption_text)

        # Join sections with double newline
        final_result = "\n\n".join(output_parts)
        logger.info(
            f"Structured parsing successful, final result length: {len(final_result)}"
        )
        return final_result

    except Exception as e:
        # Fallback to original response if parsing fails
        logger.error(f"Failed to parse structured response: {e}")
        logger.error(f"Original response was: {response[:500]}...")
        return S.LABEL_TRANSLATION + safe_html(response)


async def _perform_single_model_translation(
    user_id: int,
    text_input: str = None,
    image_data: bytes = None,
    mime_type: str = "image/jpeg",
) -> tuple[str, int, int, int]:
    """
    Performs OCR, language detection, and translation using a single Gemini model call.

    Returns:
        Tuple of (translated_text, total_token_count, input_tokens, output_tokens)
    """
    # Build prompt based on what content we have
    if image_data and text_input:
        system_prompt = PROMPTS["translation"]["text_with_image"]
    elif image_data:
        system_prompt = PROMPTS["translation"]["image_only"]
    else:
        system_prompt = PROMPTS["translation"]["text_only"]

    content = []
    if image_data:
        # Use the new API to create image part from bytes
        image_part = types.Part.from_bytes(data=image_data, mime_type=mime_type)
        content.append(image_part)

    prompt_with_text = system_prompt
    if text_input:
        prompt_with_text += f'\n\nHere is the text input to use: """{text_input}"""'
    else:
        prompt_with_text += (
            "\n\nThere is no separate text input, process the image only."
        )
    content.append(prompt_with_text)

    try:
        # Use asyncio.to_thread (Python 3.9+) instead of deprecated get_event_loop()
        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=GEMINI_MODEL_NAME,
            contents=content,
        )

        # Extract token counts from response metadata
        token_count = 0
        input_tokens = 0
        output_tokens = 0
        if response.usage_metadata:
            token_count = response.usage_metadata.total_token_count or 0
            input_tokens = response.usage_metadata.prompt_token_count or 0
            output_tokens = response.usage_metadata.candidates_token_count or 0

        # Debug logging for response analysis
        logger.debug(
            f"Gemini response length: {len(response.text)} chars, tokens: {token_count} (in:{input_tokens}/out:{output_tokens})"
        )
        logger.debug(f"Response preview: {response.text[:200]}...")

        return response.text.strip(), token_count, input_tokens, output_tokens

    except Exception as e:
        log_error_with_context(
            e, context_info={"operation": "single_model_translation"}, user_id=user_id
        )
        return S.GENERIC_ERROR, 0, 0, 0


async def translate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle message translation for various message types with rate limiting.
    (This version is simplified to use a single-model pipeline).
    """
    if not update.effective_user:
        logger.warning("Received update without effective_user, skipping translation")
        return

    user_id = update.effective_user.id
    message = update.message

    # Guard against None message (can happen with edited messages or other update types)
    if message is None:
        logger.warning(
            f"Received update without message for user {user_id}, skipping translation"
        )
        return

    # Check if this is a feedback message first
    from .feedback import is_user_pending_feedback, handle_feedback_message

    if message and message.text and is_user_pending_feedback(user_id):
        await handle_feedback_message(update, context)
        return

    # Show status message immediately so user knows bot is processing
    status_message = await message.reply_text(S.PROCESSING)

    allowed, error_message = user_manager.check_rate_limit(user_id)
    if not allowed:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=error_message,
        )
        return

    # Check limits based on subscription tier
    is_premium = is_user_premium(user_id)

    if is_premium:
        # Premium users: check remaining total limits
        remaining = get_user_remaining_limits(user_id)
        if remaining["translation_remaining"] <= 0:
            plan = SUBSCRIPTION_PLAN
            button_text = f"{S.BTN_INCREASE_LIMIT} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.TRANSLATION_LIMIT_EXCEEDED_PREMIUM.format(
                    stars=plan["stars"],
                    translation_limit=plan["translation_limit"],
                    youtube_limit=plan["youtube_minutes_limit"],
                    days=plan["days"],
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
    else:
        # Free users: check remaining limits
        subscription = get_user_subscription(user_id)

        if subscription:
            translation_remaining = subscription.get("translation_remaining", 0)
        else:
            translation_remaining = SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS

        if translation_remaining <= 0:
            plan = SUBSCRIPTION_PLAN
            button_text = f"{S.BTN_SUBSCRIBE} - {plan['stars']} Yulduz"
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.TRANSLATION_LIMIT_EXCEEDED_FREE.format(
                    free_limit=SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS,
                    stars=plan["stars"],
                    translation_limit=plan["translation_limit"],
                    youtube_limit=plan["youtube_minutes_limit"],
                    days=plan["days"],
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return

    text_input = None
    image_data = None
    image_mime_type = "image/jpeg"  # Default for photos
    reserved_translation = False

    try:
        # Update status message to show we're now translating
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=S.TRANSLATING,
        )

        if message.photo or (
            message.document
            and message.document.mime_type
            and message.document.mime_type.startswith("image/")
        ):
            if message.photo:
                file_id = message.photo[-1].file_id
                file_size = message.photo[-1].file_size
                image_mime_type = "image/jpeg"  # Telegram photos are always JPEG
            else:
                file_id = message.document.file_id
                file_size = message.document.file_size
                image_mime_type = (
                    message.document.mime_type
                )  # Use actual MIME type from document

            if file_size and file_size > IMAGE_LIMITS.MAX_IMAGE_SIZE_MB * 1024 * 1024:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=status_message.message_id,
                    text=S.IMAGE_TOO_LARGE.format(
                        max_size=IMAGE_LIMITS.MAX_IMAGE_SIZE_MB
                    ),
                )
                return

            file = await context.bot.get_file(file_id)
            image_buffer = io.BytesIO()
            await file.download_to_memory(image_buffer)
            image_buffer.seek(0)
            image_data = image_buffer.getvalue()
            text_input = message.caption.strip() if message.caption else ""

        elif message.text:
            text_input = message.text
        elif message.caption and not image_data:
            text_input = message.caption
        else:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.SEND_TEXT_OR_IMAGE,
            )
            return

        if not text_input and not image_data:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.NO_CONTENT,
            )
            return

        if text_input:
            text_ok, text_error = user_manager.check_text_length(text_input)
            if not text_ok:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=status_message.message_id,
                    text=text_error,
                )
                return

        # Budget guardrail before model call.
        estimated_tokens = user_manager.estimate_tokens(text_input or "")
        if image_data:
            # OCR and multimodal requests are significantly more expensive than text-only.
            estimated_tokens += 6000

        budget_ok, budget_error = user_manager.check_token_limits(
            user_id=user_id,
            service="gemini",
            estimated_tokens=estimated_tokens,
        )
        if not budget_ok:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=budget_error,
            )
            return

        # Reserve one translation credit before the expensive API call.
        if not is_premium:
            ensure_free_user_sub(user_id)

        if not decrement_translation_limit(user_id):
            log_error_with_context(
                RuntimeError("Could not reserve translation quota"),
                context_info={"operation": "translation_quota_reservation"},
                user_id=user_id,
                text_preview=text_input,
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.GENERIC_ERROR,
            )
            return

        reserved_translation = True

        (
            translated_text,
            token_count,
            input_tokens,
            output_tokens,
        ) = await _perform_single_model_translation(
            user_id, text_input, image_data, image_mime_type
        )

        # Determine content type for logging
        if image_data and text_input:
            content_type = "image_with_caption"
        elif image_data:
            content_type = "image"
        else:
            content_type = "text"

        # Log token usage to database and update user session
        if token_count > 0:
            log_token_usage_to_db(
                user_id,
                "gemini",
                token_count,
                is_translation=True,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                content_type=content_type,
                content_preview=text_input[:200] if text_input else None,
            )
            user_manager.record_token_usage(user_id, token_count)

        # If generation failed after quota reservation, refund the credit.
        if token_count <= 0 or translated_text == S.GENERIC_ERROR:
            if not increment_translation_limit(user_id):
                log_error_with_context(
                    RuntimeError("Could not refund translation quota"),
                    context_info={"operation": "translation_quota_refund"},
                    user_id=user_id,
                    text_preview=text_input,
                )
            reserved_translation = False
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.GENERIC_ERROR,
            )
            return

        # Format output with appropriate title and separate sections
        formatted_output = _format_translation_output(
            translated_text,
            has_image=bool(image_data),
            has_caption=bool(text_input and image_data),
        )

        # Get stats/subscribe button
        stats_keyboard = get_stats_button(user_id)

        # Split if message is too long
        output_text = formatted_output or S.GENERIC_ERROR
        parts = split_message(output_text)

        if len(parts) > 1:
            logger.info(f"Translation split into {len(parts)} parts")

        # Edit the status message with the first part
        first_markup = stats_keyboard if len(parts) == 1 else None
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            text=parts[0],
            parse_mode=ParseMode.HTML,
            reply_markup=first_markup,
        )

        # Send remaining parts as new messages
        for i, part in enumerate(parts[1:], start=2):
            part_markup = stats_keyboard if i == len(parts) else None
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=part,
                parse_mode=ParseMode.HTML,
                reply_markup=part_markup,
            )

    except Exception as e:
        if reserved_translation:
            if not increment_translation_limit(user_id):
                log_error_with_context(
                    RuntimeError("Could not refund translation quota after failure"),
                    context_info={"operation": "translation_quota_refund_exception"},
                    user_id=user_id,
                    text_preview=text_input,
                )
        log_error_with_context(
            e,
            context_info={"operation": "main_translation_handler_single_model"},
            user_id=user_id,
            text_preview=text_input,
        )
        if status_message:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_message.message_id,
                text=S.GENERIC_ERROR,
                parse_mode=ParseMode.HTML,
            )
