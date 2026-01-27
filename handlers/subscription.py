"""Subscription and payment handlers for the Tarjimon bot."""

from __future__ import annotations

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import strings as S
from config import (
    logger,
    SUBSCRIPTION_PLAN,
    format_date_uzbek,
    get_days_remaining,
)
from constants import SUBSCRIPTION_LIMITS
from database import (
    is_user_premium,
    activate_premium,
    log_payment,
    get_payment_by_telegram_id,
    get_user_subscription,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    user_id = update.effective_user.id
    is_premium = is_user_premium(user_id)

    if is_premium:
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0) if subscription else 0
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0) if subscription else 0
        )

        formatted_date = format_date_uzbek(expires_at)

        status_text = S.STATUS_PREMIUM.format(
            date=formatted_date,
            youtube_minutes=youtube_minutes_remaining,
            translations=translation_remaining,
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        S.BTN_INCREASE_LIMIT, callback_data="subscribe_show"
                    )
                ]
            ]
        )
    else:
        # Get free user's remaining limits
        subscription = get_user_subscription(user_id)
        if subscription:
            youtube_minutes_remaining = subscription.get(
                "youtube_minutes_remaining", SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES
            )
            translation_remaining = subscription.get(
                "translation_remaining", SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS
            )
        else:
            youtube_minutes_remaining = SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES
            translation_remaining = SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS

        status_text = S.STATUS_FREE.format(
            youtube_minutes=youtube_minutes_remaining,
            translations=translation_remaining,
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(S.BTN_SUBSCRIBE, callback_data="subscribe_show")]]
        )

    plan = SUBSCRIPTION_PLAN
    await update.message.reply_text(
        S.WELCOME_MESSAGE.format(
            status_text=status_text,
            free_youtube_minutes=SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES,
            free_translations=SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS,
            premium_youtube_minutes=plan["youtube_minutes_limit"],
            premium_translations=plan["translation_limit"],
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /subscribe command - show subscription options."""
    user_id = update.effective_user.id
    is_premium = is_user_premium(user_id)

    # Create subscription button
    plan = SUBSCRIPTION_PLAN
    button_text = f"{plan['title']} - {plan['stars']} Yulduz"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
    )

    if is_premium:
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0) if subscription else 0
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0) if subscription else 0
        )

        formatted_date = format_date_uzbek(expires_at)
        days_remaining = get_days_remaining(expires_at)

        await update.message.reply_text(
            S.SUBSCRIBE_PREMIUM_USER_INFO.format(
                days_remaining=days_remaining,
                date=formatted_date,
                youtube_minutes=youtube_minutes_remaining,
                translations=translation_remaining,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        limits_text = S.SUBSCRIBE_FREE_USER_INFO.format(
            free_youtube_minutes=SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES,
            free_translations=SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS,
            stars=plan["stars"],
            premium_youtube_minutes=plan["youtube_minutes_limit"],
            premium_translations=plan["translation_limit"],
            days=plan["days"],
        )

        await update.message.reply_text(
            f"{S.SUBSCRIBE_HEADING}\n\n{limits_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def handle_subscribe_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle subscription button clicks - send invoice."""
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        # Query expired (user clicked too late), ignore
        pass

    if not query.data.startswith("subscribe_"):
        return

    # Handle "show subscription options" callback
    if query.data == "subscribe_show":
        user_id = update.effective_user.id
        is_premium = is_user_premium(user_id)

        plan = SUBSCRIPTION_PLAN
        button_text = f"{plan['title']} - {plan['stars']} Yulduz"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
        )

        if is_premium:
            subscription = get_user_subscription(user_id)
            youtube_minutes_remaining = (
                subscription.get("youtube_minutes_remaining", 0) if subscription else 0
            )
            translation_remaining = (
                subscription.get("translation_remaining", 0) if subscription else 0
            )

            await query.message.reply_text(
                f"<b>Qolgan limitlar:</b> {youtube_minutes_remaining} daqiqa video, {translation_remaining} ta tarjima\n\n"
                f"<b>Premium paket ({plan['stars']} Yulduz):</b>\n"
                f"- {plan['youtube_minutes_limit']} daqiqa YouTube video\n"
                f"- {plan['translation_limit']} ta tarjima\n"
                f"- {plan['days']} kun amal qiladi",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        else:
            limits_text = S.SUBSCRIBE_FREE_USER_INFO.format(
                free_youtube_minutes=SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES,
                free_translations=SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS,
                stars=plan["stars"],
                premium_youtube_minutes=plan["youtube_minutes_limit"],
                premium_translations=plan["translation_limit"],
                days=plan["days"],
            )

            await query.message.reply_text(
                f"{S.SUBSCRIBE_HEADING}\n\n{limits_text}",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        return

    # Handle "buy" callback
    if query.data == "subscribe_buy":
        plan = SUBSCRIPTION_PLAN

        # Send invoice using Telegram Yulduz
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=plan["title"],
            description=plan["description"],
            payload="premium_30_days",
            provider_token="",  # Empty string for Telegram Yulduz
            currency="XTR",  # XTR = Telegram Yulduz
            prices=[LabeledPrice(plan["title"], plan["stars"])],
        )


async def handle_stats_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle stats button clicks - show user stats and subscription options."""
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        # Query expired (user clicked too late), ignore
        pass

    if query.data != "stats_show":
        return

    user_id = update.effective_user.id
    is_premium = is_user_premium(user_id)
    plan = SUBSCRIPTION_PLAN

    if is_premium:
        # Premium user: show remaining limits and option to buy more
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0) if subscription else 0
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0) if subscription else 0
        )

        formatted_date = format_date_uzbek(expires_at)
        days_remaining = get_days_remaining(expires_at)

        button_text = f"{S.BTN_INCREASE_LIMIT} - {plan['stars']} Yulduz"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
        )

        await query.message.reply_text(
            S.STATS_PREMIUM.format(
                days_remaining=days_remaining,
                date=formatted_date,
                youtube_minutes=youtube_minutes_remaining,
                translations=translation_remaining,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        # Free user: show remaining limits
        subscription = get_user_subscription(user_id)

        if subscription:
            youtube_minutes_remaining = subscription.get("youtube_minutes_remaining", 0)
            translation_remaining = subscription.get("translation_remaining", 0)
        else:
            # New user - show full free limits
            youtube_minutes_remaining = SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES
            translation_remaining = SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS

        button_text = f"{S.BTN_SUBSCRIBE} - {plan['stars']} Yulduz"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_text, callback_data="subscribe_buy")]]
        )

        await query.message.reply_text(
            S.STATS_FREE.format(
                youtube_minutes=youtube_minutes_remaining,
                free_youtube_minutes=SUBSCRIPTION_LIMITS.FREE_YOUTUBE_MINUTES,
                translations=translation_remaining,
                free_translations=SUBSCRIPTION_LIMITS.FREE_TRANSLATIONS,
                stars=plan["stars"],
                premium_youtube_minutes=plan["youtube_minutes_limit"],
                premium_translations=plan["translation_limit"],
                days=plan["days"],
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def pre_checkout_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle pre-checkout query - validate the purchase."""
    query = update.pre_checkout_query

    # Validate the plan exists (we only have one plan now)
    plan_id = query.invoice_payload
    if plan_id != "premium_30_days":
        await query.answer(ok=False, error_message=S.INVALID_PLAN)
        return

    # All good, approve the purchase
    await query.answer(ok=True)


async def successful_payment_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle successful payment - activate subscription."""
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    telegram_payment_id = payment.telegram_payment_charge_id

    # Idempotency check: verify this payment hasn't been processed already
    existing_payment = get_payment_by_telegram_id(telegram_payment_id)
    if existing_payment:
        logger.warning(
            f"Duplicate payment webhook ignored: {telegram_payment_id} for user {user_id}"
        )
        # Still send a success message since the user expects confirmation
        await update.message.reply_text(
            S.PAYMENT_ALREADY_PROCESSED,
            parse_mode=ParseMode.HTML,
        )
        return

    plan = SUBSCRIPTION_PLAN
    days = plan["days"]
    youtube_minutes_limit = plan["youtube_minutes_limit"]
    translation_limit = plan["translation_limit"]

    # Log the payment first (this creates the record for idempotency)
    if not log_payment(
        user_id=user_id,
        telegram_payment_id=telegram_payment_id,
        amount_stars=payment.total_amount,
        plan="premium_30_days",
        days=days,
    ):
        logger.error(f"Failed to log payment {telegram_payment_id} for user {user_id}")
        await update.message.reply_text(S.PAYMENT_LOG_ERROR)
        return

    # Activate premium with limits
    if activate_premium(user_id, days, youtube_minutes_limit, translation_limit):
        subscription = get_user_subscription(user_id)
        expires_at = subscription["expires_at"] if subscription else "N/A"
        youtube_minutes_remaining = (
            subscription.get("youtube_minutes_remaining", 0)
            if subscription
            else youtube_minutes_limit
        )
        translation_remaining = (
            subscription.get("translation_remaining", 0)
            if subscription
            else translation_limit
        )

        formatted_date = format_date_uzbek(expires_at)

        await update.message.reply_text(
            f"{S.PAYMENT_SUCCESS_TITLE}"
            f"{S.PAYMENT_SUBSCRIPTION_ACTIVATED}"
            f"{S.PAYMENT_EXPIRES_AT.format(date=formatted_date)}"
            f"{S.PAYMENT_YOUR_LIMITS}"
            f"{S.PAYMENT_YOUTUBE_MINUTES_FORMAT.format(minutes=youtube_minutes_remaining)}"
            f"{S.PAYMENT_TRANSLATIONS_FORMAT.format(count=translation_remaining)}"
            f"{S.PAYMENT_THANK_YOU}",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(S.ACTIVATION_ERROR)
