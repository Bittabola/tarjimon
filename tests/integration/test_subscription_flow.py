"""Integration tests for subscription and callback handler flows.

Tests:
1. /start for free user -- reply contains "Salom"
2. /start for premium user -- reply contains "Premium"
3. /subscribe command -- reply contains "Yulduz" or "Premium"
4. subscribe_buy callback -- context.bot.send_invoice called
5. stats_show callback for free user -- reply with remaining limits (contains "10")
6. pre-checkout with valid plan -- query.answer(ok=True)
7. pre-checkout with invalid plan -- query.answer(ok=False, ...)
8. successful payment -- activates premium, correct translation limit
9. duplicate payment -- second reply contains "allaqachon"
"""

from __future__ import annotations


import database
from handlers.subscription import (
    handle_stats_callback,
    handle_subscribe_callback,
    pre_checkout_handler,
    start,
    subscribe,
    successful_payment_handler,
)
from tests.integration.helpers import (
    make_callback_query_update,
    make_command_update,
    make_payment_update,
    make_pre_checkout_update,
)


# ------------------------------------------------------------------
# 1. /start command -- free user
# ------------------------------------------------------------------


async def test_start_command_free_user(tmp_db):
    """/start for a free user replies with a message containing 'Salom'."""
    update, ctx = make_command_update(command="/start", user_id=1)

    await start(update, ctx)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Salom" in text


# ------------------------------------------------------------------
# 2. /start command -- premium user
# ------------------------------------------------------------------


async def test_start_command_premium_user(tmp_db):
    """Premium user /start reply contains 'Premium'."""
    user_id = 2
    database.activate_premium(user_id, days=30, translation_limit=50)

    update, ctx = make_command_update(command="/start", user_id=user_id)

    await start(update, ctx)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Premium" in text


# ------------------------------------------------------------------
# 3. /subscribe command
# ------------------------------------------------------------------


async def test_subscribe_command(tmp_db):
    """/subscribe reply contains 'Yulduz' or 'Premium'."""
    update, ctx = make_command_update(command="/subscribe", user_id=3)

    await subscribe(update, ctx)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Yulduz" in text or "Premium" in text


# ------------------------------------------------------------------
# 4. subscribe_buy callback sends invoice
# ------------------------------------------------------------------


async def test_subscribe_buy_callback_sends_invoice(tmp_db):
    """subscribe_buy callback triggers context.bot.send_invoice."""
    update, ctx = make_callback_query_update(data="subscribe_buy", user_id=4)

    await handle_subscribe_callback(update, ctx)

    ctx.bot.send_invoice.assert_awaited_once()


# ------------------------------------------------------------------
# 5. stats_show callback for free user
# ------------------------------------------------------------------


async def test_stats_show_callback_free_user(tmp_db):
    """stats_show for a free user shows remaining limits (contains '10')."""
    user_id = 5
    database.ensure_free_user_subscription(user_id, translations=10)

    update, ctx = make_callback_query_update(data="stats_show", user_id=user_id)

    await handle_stats_callback(update, ctx)

    update.callback_query.message.reply_text.assert_awaited_once()
    text = update.callback_query.message.reply_text.call_args[0][0]
    assert "10" in text


# ------------------------------------------------------------------
# 6. Pre-checkout with valid plan
# ------------------------------------------------------------------


async def test_pre_checkout_valid_plan(tmp_db):
    """Payload 'premium_30_days' results in query.answer(ok=True)."""
    update, ctx = make_pre_checkout_update(payload="premium_30_days", user_id=6)

    await pre_checkout_handler(update, ctx)

    update.pre_checkout_query.answer.assert_awaited_once_with(ok=True)


# ------------------------------------------------------------------
# 7. Pre-checkout with invalid plan
# ------------------------------------------------------------------


async def test_pre_checkout_invalid_plan(tmp_db):
    """Invalid payload results in query.answer(ok=False, ...)."""
    update, ctx = make_pre_checkout_update(payload="invalid_plan", user_id=7)

    await pre_checkout_handler(update, ctx)

    update.pre_checkout_query.answer.assert_awaited_once()
    call_kwargs = update.pre_checkout_query.answer.call_args[1]
    assert call_kwargs["ok"] is False


# ------------------------------------------------------------------
# 8. Successful payment activates premium
# ------------------------------------------------------------------


async def test_successful_payment_activates_premium(tmp_db):
    """Payment activates premium with correct limits (50 translations)."""
    user_id = 1

    update, ctx = make_payment_update(
        user_id=user_id,
        telegram_payment_id="charge_activate_test",
        total_amount=350,
    )

    await successful_payment_handler(update, ctx)

    assert database.is_user_premium(user_id) is True

    sub = database.get_user_subscription(user_id)
    assert sub is not None
    assert sub["translation_remaining"] == 50


# ------------------------------------------------------------------
# 9. Duplicate payment is ignored
# ------------------------------------------------------------------


async def test_duplicate_payment_ignored(tmp_db):
    """Same telegram_payment_id twice -- second reply contains 'allaqachon'."""
    user_id = 1
    payment_id = "charge_duplicate_test"

    # First payment
    update1, ctx1 = make_payment_update(
        user_id=user_id,
        telegram_payment_id=payment_id,
        total_amount=350,
    )
    await successful_payment_handler(update1, ctx1)

    # Second payment with the same ID
    update2, ctx2 = make_payment_update(
        user_id=user_id,
        telegram_payment_id=payment_id,
        total_amount=350,
    )
    await successful_payment_handler(update2, ctx2)

    update2.message.reply_text.assert_awaited_once()
    text = update2.message.reply_text.call_args[0][0]
    assert "allaqachon" in text.lower()
