"""Feedback handlers for the Tarjimon bot."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import (
    logger,
    FEEDBACK_BOT_TOKEN,
    FEEDBACK_ADMIN_ID,
)
from database import save_feedback, update_feedback_admin_msg_id
import strings as S


# Store users waiting to send feedback
_feedback_pending_users: set[int] = set()


async def aloqa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /aloqa command - prompt user to send feedback."""
    # Check if feedback feature is configured
    if not FEEDBACK_BOT_TOKEN or not FEEDBACK_ADMIN_ID:
        await update.message.reply_text(
            "Fikr-mulohaza funksiyasi hozircha mavjud emas.",
            parse_mode=ParseMode.HTML,
        )
        return

    user_id = update.effective_user.id
    _feedback_pending_users.add(user_id)

    await update.message.reply_text(
        S.FEEDBACK_PROMPT,
        parse_mode=ParseMode.HTML,
    )


async def handle_feedback_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the feedback button callback - prompt user to send feedback."""
    query = update.callback_query
    await query.answer()

    # Check if feedback feature is configured
    if not FEEDBACK_BOT_TOKEN or not FEEDBACK_ADMIN_ID:
        await query.message.reply_text(
            "Fikr-mulohaza funksiyasi hozircha mavjud emas.",
            parse_mode=ParseMode.HTML,
        )
        return

    user_id = update.effective_user.id
    _feedback_pending_users.add(user_id)

    await query.message.reply_text(
        S.FEEDBACK_PROMPT,
        parse_mode=ParseMode.HTML,
    )


def is_user_pending_feedback(user_id: int) -> bool:
    """Check if user is waiting to send feedback."""
    return user_id in _feedback_pending_users


def clear_pending_feedback(user_id: int) -> None:
    """Clear user's pending feedback state."""
    _feedback_pending_users.discard(user_id)


async def handle_feedback_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """
    Handle incoming feedback message from user.

    Returns True if the message was handled as feedback, False otherwise.
    """
    import httpx

    if not update.effective_user:
        return False

    user_id = update.effective_user.id

    # Check if user is in feedback mode
    if not is_user_pending_feedback(user_id):
        return False

    # Clear pending state
    clear_pending_feedback(user_id)

    message = update.message
    if not message or not message.text:
        await message.reply_text(S.FEEDBACK_SEND_ERROR)
        return True

    feedback_text = message.text
    username = update.effective_user.username
    first_name = update.effective_user.first_name

    # Save feedback to database first
    feedback_id = save_feedback(
        user_id=user_id,
        message_text=feedback_text,
        username=username,
        first_name=first_name,
        feedback_msg_id=message.message_id,
    )

    if not feedback_id:
        await message.reply_text(S.FEEDBACK_SEND_ERROR)
        return True

    # Format message for admin
    user_info = f"ID: {user_id}"
    if username:
        user_info += f" | @{username}"
    if first_name:
        user_info += f" | {first_name}"

    admin_text = f"<b>Yangi fikr-mulohaza</b>\n\n<b>Foydalanuvchi:</b> {user_info}\n\n<b>Xabar:</b>\n{feedback_text}"

    # Send to admin via feedback bot
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.telegram.org/bot{FEEDBACK_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": FEEDBACK_ADMIN_ID,
                    "text": admin_text,
                    "parse_mode": "HTML",
                },
                timeout=10.0,
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    admin_msg_id = result["result"]["message_id"]
                    update_feedback_admin_msg_id(feedback_id, admin_msg_id)
                    await message.reply_text(S.FEEDBACK_RECEIVED)
                else:
                    logger.error(f"Feedback bot error: {result}")
                    await message.reply_text(S.FEEDBACK_SEND_ERROR)
            else:
                logger.error(f"Feedback bot HTTP error: {response.status_code}")
                await message.reply_text(S.FEEDBACK_SEND_ERROR)

    except Exception as e:
        logger.error(f"Error sending feedback to admin: {e}")
        await message.reply_text(S.FEEDBACK_SEND_ERROR)

    return True
