"""Helper functions for building mock Telegram Update and Context objects.

These builders create MagicMock objects that faithfully match the
python-telegram-bot library's interface so that handler functions can be
called directly in integration tests without a running Telegram server.

Each ``make_*`` function returns objects whose attributes and async methods
mirror the real Telegram types used by the tarjimon handlers.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Minimal 1x1 transparent PNG (67 bytes) used as fake image data for photo
# download mocks.  This avoids needing Pillow at test time while still being
# a valid image payload.
# ---------------------------------------------------------------------------
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"                          # PNG signature
    b"\x00\x00\x00\rIHDR"                          # IHDR chunk length + type
    b"\x00\x00\x00\x01\x00\x00\x00\x01"           # width=1, height=1
    b"\x08\x02"                                    # bit depth=8, color type=RGB
    b"\x00\x00\x00"                                # compression, filter, interlace
    b"\x90wS\xde"                                  # IHDR CRC
    b"\x00\x00\x00\x0cIDATx"                       # IDAT chunk length + type + zlib header
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"     # compressed data
    b"\x18\xd8N"                                   # IDAT CRC
    b"\x00\x00\x00\x00IEND"                        # IEND chunk
    b"\xaeB`\x82"                                  # IEND CRC
)


# ---------------------------------------------------------------------------
# Builder: Bot
# ---------------------------------------------------------------------------

def make_bot() -> MagicMock:
    """Create a mock bot with AsyncMock methods matching ``context.bot``.

    Captured methods:
    - ``edit_message_text``
    - ``send_message``
    - ``send_invoice``
    - ``get_file`` -- returns a file mock whose ``download_to_memory``
      writes ``_TINY_PNG`` bytes into the supplied buffer.

    Returns:
        A MagicMock configured as a Telegram Bot.
    """
    bot = MagicMock()

    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_invoice = AsyncMock()

    # get_file returns a file-like mock that supports download_to_memory
    file_mock = MagicMock()

    async def _download_to_memory(buf: io.BytesIO) -> None:
        buf.write(_TINY_PNG)
        buf.seek(0)

    file_mock.download_to_memory = AsyncMock(side_effect=_download_to_memory)
    bot.get_file = AsyncMock(return_value=file_mock)

    return bot


# ---------------------------------------------------------------------------
# Builder: Context
# ---------------------------------------------------------------------------

def make_context(bot: MagicMock | None = None) -> MagicMock:
    """Create a mock ``ContextTypes.DEFAULT_TYPE`` instance.

    Args:
        bot: An optional pre-built bot mock.  If ``None``, one is created
             via :func:`make_bot`.

    Returns:
        A MagicMock that behaves like a Telegram CallbackContext.
    """
    ctx = MagicMock()
    ctx.bot = bot or make_bot()
    # user_data and chat_data are commonly accessed as dicts
    ctx.user_data = {}
    ctx.chat_data = {}
    return ctx


# ---------------------------------------------------------------------------
# Builder: User
# ---------------------------------------------------------------------------

def make_user(
    user_id: int = 12345,
    username: str = "testuser",
    first_name: str = "Test",
) -> MagicMock:
    """Create a mock ``telegram.User``.

    Args:
        user_id: The Telegram user ID.
        username: The Telegram username (without ``@``).
        first_name: The user's first name.

    Returns:
        A MagicMock with ``.id``, ``.username``, and ``.first_name``.
    """
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.first_name = first_name
    return user


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_reply_text_mock() -> AsyncMock:
    """Return an ``AsyncMock`` for ``message.reply_text`` that resolves to a
    message-like object with a ``.message_id`` attribute.
    """
    reply_msg = MagicMock()
    reply_msg.message_id = 9999
    return AsyncMock(return_value=reply_msg)


def _make_message(
    *,
    user: MagicMock,
    chat_id: int,
    message_id: int = 1,
    text: str | None = None,
    caption: str | None = None,
    photo: list | None = None,
    document: MagicMock | None = None,
    successful_payment: MagicMock | None = None,
) -> MagicMock:
    """Build a mock ``telegram.Message`` with the most commonly accessed attrs."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.text = text
    msg.caption = caption
    msg.photo = photo
    msg.document = document
    msg.successful_payment = successful_payment
    msg.reply_text = _make_reply_text_mock()
    # Some handlers access message.from_user
    msg.from_user = user
    return msg


def _make_update(
    *,
    user: MagicMock,
    chat_id: int,
    message: MagicMock | None = None,
    callback_query: MagicMock | None = None,
    pre_checkout_query: MagicMock | None = None,
) -> MagicMock:
    """Build a mock ``telegram.Update`` wiring user/chat/message together."""
    update = MagicMock()
    update.effective_user = user

    # effective_chat
    chat = MagicMock()
    chat.id = chat_id
    update.effective_chat = chat

    update.message = message
    update.callback_query = callback_query
    update.pre_checkout_query = pre_checkout_query

    return update


# ---------------------------------------------------------------------------
# Public builders: each returns (update, context)
# ---------------------------------------------------------------------------

def make_text_update(
    text: str = "Hello world",
    user_id: int = 12345,
    chat_id: int = 12345,
    message_id: int = 1,
    bot: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build an (update, context) pair for a plain text message.

    Args:
        text: The message text.
        user_id: Telegram user ID.
        chat_id: Telegram chat ID.
        message_id: The message's unique ID in the chat.
        bot: Optional pre-built bot mock.

    Returns:
        A ``(update, context)`` tuple ready to pass to any handler.
    """
    user = make_user(user_id=user_id)
    msg = _make_message(
        user=user,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
    )
    update = _make_update(user=user, chat_id=chat_id, message=msg)
    ctx = make_context(bot=bot)
    return update, ctx


def make_photo_update(
    user_id: int = 12345,
    chat_id: int = 12345,
    caption: str | None = None,
    file_size: int = 1024,
    file_id: str = "fake-file-id",
    bot: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build an (update, context) pair for a photo message.

    The returned context's ``bot.get_file()`` is pre-configured so that
    ``file.download_to_memory(buf)`` writes a minimal valid PNG into the
    buffer -- matching the pattern used in ``handlers/translation.py``.

    Args:
        user_id: Telegram user ID.
        chat_id: Telegram chat ID.
        caption: Optional caption on the photo.
        file_size: Reported file size in bytes.
        file_id: The Telegram file ID string.
        bot: Optional pre-built bot mock.

    Returns:
        A ``(update, context)`` tuple.
    """
    user = make_user(user_id=user_id)

    # Telegram delivers photos as a list of PhotoSize objects (thumbnails);
    # handlers always use the last (largest) element.
    photo_size = MagicMock()
    photo_size.file_id = file_id
    photo_size.file_size = file_size
    photo_list = [photo_size]

    msg = _make_message(
        user=user,
        chat_id=chat_id,
        caption=caption,
        photo=photo_list,
        text=None,
    )
    update = _make_update(user=user, chat_id=chat_id, message=msg)
    ctx = make_context(bot=bot)
    return update, ctx


def make_command_update(
    command: str = "/start",
    user_id: int = 12345,
    chat_id: int = 12345,
    bot: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build an (update, context) pair for a ``/command`` message.

    Internally delegates to :func:`make_text_update` since Telegram
    delivers commands as regular text messages whose ``.text`` starts
    with ``/``.

    Args:
        command: The full command string (e.g. ``"/start"``).
        user_id: Telegram user ID.
        chat_id: Telegram chat ID.
        bot: Optional pre-built bot mock.

    Returns:
        A ``(update, context)`` tuple.
    """
    return make_text_update(
        text=command,
        user_id=user_id,
        chat_id=chat_id,
        bot=bot,
    )


def make_callback_query_update(
    data: str = "stats_show",
    user_id: int = 12345,
    chat_id: int = 12345,
    bot: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build an (update, context) pair for an inline-keyboard callback query.

    The mock callback query provides:
    - ``.data`` -- the callback data string
    - ``.answer()`` -- an AsyncMock
    - ``.message.reply_text()`` -- an AsyncMock returning a message-like object

    Args:
        data: The callback query data payload.
        user_id: Telegram user ID.
        chat_id: Telegram chat ID.
        bot: Optional pre-built bot mock.

    Returns:
        A ``(update, context)`` tuple.
    """
    user = make_user(user_id=user_id)

    # Build the message attached to the callback query
    query_message = MagicMock()
    query_message.message_id = 100
    query_message.reply_text = _make_reply_text_mock()

    # Build the callback query itself
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.message = query_message

    update = _make_update(
        user=user,
        chat_id=chat_id,
        message=None,
        callback_query=query,
    )
    ctx = make_context(bot=bot)
    return update, ctx


def make_pre_checkout_update(
    payload: str = "premium_30_days",
    user_id: int = 12345,
    bot: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build an (update, context) pair for a pre-checkout query.

    The handler (``pre_checkout_handler``) checks:
    - ``update.pre_checkout_query.invoice_payload``
    - ``update.pre_checkout_query.answer(ok=True/False, ...)``

    Args:
        payload: The invoice payload string to validate.
        user_id: Telegram user ID.
        bot: Optional pre-built bot mock.

    Returns:
        A ``(update, context)`` tuple.
    """
    user = make_user(user_id=user_id)

    query = MagicMock()
    query.invoice_payload = payload
    query.answer = AsyncMock()

    update = _make_update(
        user=user,
        chat_id=user_id,  # pre-checkout queries come from user's private chat
        message=None,
        pre_checkout_query=query,
    )
    ctx = make_context(bot=bot)
    return update, ctx


def make_payment_update(
    user_id: int = 12345,
    chat_id: int = 12345,
    telegram_payment_id: str = "charge_test_123",
    total_amount: int = 150,
    bot: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build an (update, context) pair for a successful payment message.

    The handler (``successful_payment_handler``) accesses:
    - ``update.message.successful_payment.telegram_payment_charge_id``
    - ``update.message.successful_payment.total_amount``
    - ``update.effective_user.id``

    Args:
        user_id: Telegram user ID.
        chat_id: Telegram chat ID.
        telegram_payment_id: The Telegram-generated payment charge ID.
        total_amount: Total amount in the smallest currency unit (stars).
        bot: Optional pre-built bot mock.

    Returns:
        A ``(update, context)`` tuple.
    """
    user = make_user(user_id=user_id)

    # Build the SuccessfulPayment mock
    payment = MagicMock()
    payment.telegram_payment_charge_id = telegram_payment_id
    payment.total_amount = total_amount

    msg = _make_message(
        user=user,
        chat_id=chat_id,
        successful_payment=payment,
        text=None,
    )
    update = _make_update(user=user, chat_id=chat_id, message=msg)
    ctx = make_context(bot=bot)
    return update, ctx
