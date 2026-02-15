import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application
from config import (
    TELEGRAM_BOT_TOKEN,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    FEEDBACK_WEBHOOK_SECRET,
    logger,
    validate_config,
    FEEDBACK_BOT_TOKEN,
    FEEDBACK_ADMIN_ID,
)
from constants import SESSION_CONSTANTS
from database import init_db
from user_management import user_manager
from admin_dashboard import router as admin_router
from handlers import cleanup_youtube_cache
import uvicorn

# Global flag to control background task
_cleanup_task = None

# Error tracking for cleanup resilience
_cleanup_consecutive_errors = 0
_CLEANUP_MAX_CONSECUTIVE_ERRORS = 5
_CLEANUP_BACKOFF_MULTIPLIER = 5


async def _session_cleanup_loop():
    """Background task that periodically cleans up inactive sessions and caches.

    Includes error resilience: if cleanup fails repeatedly, applies exponential
    backoff to prevent log flooding and reduce resource usage.
    """
    global _cleanup_consecutive_errors

    while True:
        try:
            # Normal sleep interval, with backoff if errors are accumulating
            sleep_time = SESSION_CONSTANTS.CLEANUP_INTERVAL_SECONDS
            if _cleanup_consecutive_errors >= _CLEANUP_MAX_CONSECUTIVE_ERRORS:
                sleep_time = (
                    SESSION_CONSTANTS.CLEANUP_INTERVAL_SECONDS
                    * _CLEANUP_BACKOFF_MULTIPLIER
                )
                logger.warning(
                    f"Cleanup task in backoff mode due to {_cleanup_consecutive_errors} consecutive errors"
                )

            await asyncio.sleep(sleep_time)

            # Clean up inactive sessions
            cleaned_sessions = user_manager.cleanup_inactive_sessions()

            # Clean up YouTube deduplication cache
            cleaned_cache = cleanup_youtube_cache()

            if cleaned_sessions > 0 or cleaned_cache > 0:
                logger.debug(
                    f"Cleanup completed: {cleaned_sessions} sessions, {cleaned_cache} cache entries"
                )

            # Reset error counter on success
            _cleanup_consecutive_errors = 0

        except asyncio.CancelledError:
            logger.info("Session cleanup task cancelled")
            break
        except Exception as e:
            _cleanup_consecutive_errors += 1
            logger.error(
                f"Error in cleanup task (attempt {_cleanup_consecutive_errors}): {e}"
            )


# Create a FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    global _cleanup_task
    app_initialized = False
    try:
        # Startup event
        if not validate_config(is_webhook=False, require_webhook_secret=True):
            raise RuntimeError("Configuration validation failed")

        # Initialize database
        if not init_db():
            raise RuntimeError("Database initialization failed")

        # Initialize the bot application
        await application.initialize()
        app_initialized = True

        # Start session cleanup background task
        _cleanup_task = asyncio.create_task(_session_cleanup_loop())
        logger.info("Session cleanup background task started")

        # Set the webhook (idempotent operation) - skip if WEBHOOK_URL not available
        webhook_url = WEBHOOK_URL
        if webhook_url and webhook_url != "placeholder":
            # Always set webhook to ensure secret token stays in sync
            try:
                await application.bot.set_webhook(
                    url=webhook_url,
                    secret_token=WEBHOOK_SECRET,
                )
                logger.info(f"Webhook set to {webhook_url}")
            except Exception as e:
                logger.warning(f"Could not set webhook during startup: {e}")
        else:
            logger.info("WEBHOOK_URL not set - webhook will be configured externally")

        yield
    except Exception as e:
        logger.critical(f"Startup failed: {e}")
        raise
    finally:
        # Shutdown event
        try:
            # Cancel session cleanup task
            if _cleanup_task:
                _cleanup_task.cancel()
                try:
                    await _cleanup_task
                except asyncio.CancelledError:
                    pass
                logger.info("Session cleanup task stopped")
                _cleanup_task = None

            # Persist all sessions before shutdown
            user_manager.persist_all_sessions()

            # Don't delete webhook - let it persist for next startup
            logger.info("Shutting down bot application")

            # Shutdown the application
            if app_initialized:
                await application.shutdown()

        except Exception as e:
            logger.error(f"Error in shutdown: {e}")


app = FastAPI(lifespan=lifespan)

# Include admin dashboard routes
app.include_router(admin_router)

# Initialize the bot application
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# Initialize handlers (import them after application is created)
from handlers import (  # noqa: E402
    start,
    subscribe,
    handle_subscribe_callback,
    handle_stats_callback,
    handle_feedback_callback,
    pre_checkout_handler,
    successful_payment_handler,
    translate_message,
    summarize_youtube,
    handle_youtube_question_callback,
    aloqa,
    YOUTUBE_URL_PATTERN,
)
from telegram.ext import (  # noqa: E402
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TimedOut, NetworkError  # noqa: E402


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Global error handler to gracefully handle transient errors.
    Logs the error and suppresses common transient issues like network timeouts.
    """
    error = context.error

    # Suppress transient network errors - they're expected occasionally
    if isinstance(error, (TimedOut, NetworkError)):
        logger.warning(
            f"Transient network error (suppressed): {type(error).__name__}: {error}"
        )
        return

    # Log other errors for investigation
    logger.error(f"Unhandled error: {type(error).__name__}: {error}", exc_info=error)


# Register the global error handler
application.add_error_handler(error_handler)

# Add handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("subscribe", subscribe))
application.add_handler(CommandHandler("aloqa", aloqa))

# Payment handlers
application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
application.add_handler(
    MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler)
)

# Subscription callback handler (for plan selection buttons)
application.add_handler(
    CallbackQueryHandler(handle_subscribe_callback, pattern=r"^subscribe_")
)

# Stats callback handler (for stats/subscribe button on responses)
application.add_handler(
    CallbackQueryHandler(handle_stats_callback, pattern=r"^stats_show$")
)

# Feedback callback handler (for "Admin bilan aloqa" button)
application.add_handler(
    CallbackQueryHandler(handle_feedback_callback, pattern=r"^feedback_start$")
)

# YouTube summarization handler - must be registered BEFORE translation handler
# Uses regex filter to match YouTube URLs
youtube_filter = filters.TEXT & filters.Regex(YOUTUBE_URL_PATTERN)
application.add_handler(MessageHandler(youtube_filter, summarize_youtube))

# YouTube follow-up question callback handler (matches JSON callback data with "u" and "q" keys)
application.add_handler(
    CallbackQueryHandler(handle_youtube_question_callback, pattern=r'^\{"u":')
)

# Translation message handler - exclude YouTube URLs to avoid double processing
translate_filter = ~filters.COMMAND & (
    (filters.FORWARDED & (filters.TEXT | filters.CAPTION))
    | (filters.TEXT & ~filters.FORWARDED & ~filters.Regex(YOUTUBE_URL_PATTERN))
    | filters.PHOTO
    | (filters.Document.IMAGE)
)
application.add_handler(MessageHandler(translate_filter, translate_message))


@app.get("/")
async def root():
    """Root endpoint for health checks."""
    return {"status": "running", "message": "Tarjimon bot webhook is active"}


@app.get("/health")
async def health_check():
    """
    Enhanced health check endpoint.
    Checks database connectivity and returns system status.
    """
    from database import DatabaseManager
    import sqlite3

    health_status = {
        "status": "healthy",
        "service": "tarjimon-bot",
        "checks": {},
    }

    # Check database connectivity
    try:
        db_manager = DatabaseManager()
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            health_status["checks"]["database"] = "ok"
    except sqlite3.Error as e:
        health_status["checks"]["database"] = f"error: {str(e)[:50]}"
        health_status["status"] = "degraded"
    except Exception as e:
        health_status["checks"]["database"] = f"error: {str(e)[:50]}"
        health_status["status"] = "degraded"

    # Check active sessions count
    try:
        health_status["checks"]["active_sessions"] = len(user_manager.sessions)
    except Exception:
        health_status["checks"]["active_sessions"] = "unknown"

    # Return appropriate status code
    if health_status["status"] == "healthy":
        return health_status
    else:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=health_status)


@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming webhook requests from Telegram."""
    try:
        # Validate webhook secret token (required for security)
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        client_host = request.client.host if request.client else "unknown"
        if not WEBHOOK_SECRET:
            logger.error("WEBHOOK_SECRET is missing; refusing webhook request.")
            return Response(status_code=503)
        if secret_header != WEBHOOK_SECRET:
            logger.warning(
                f"Webhook request with invalid secret token from {client_host}"
            )
            return Response(status_code=403)

        # Get the request body as JSON
        data = await request.json()

        # Create an Update object from the received data
        update = Update.de_json(data, application.bot)

        # Process the update
        await application.process_update(update)

        # Return a 200 OK response to Telegram
        return Response(status_code=200)

    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return Response(status_code=500)


@app.post("/feedback_webhook")
async def feedback_webhook(request: Request):
    """Handle incoming webhook requests from the feedback bot."""
    import httpx
    from database import get_feedback_by_admin_msg_id, mark_feedback_replied
    import strings as S
    from utils import safe_html

    # Check if feedback feature is configured
    if not FEEDBACK_BOT_TOKEN or not FEEDBACK_ADMIN_ID:
        logger.debug(
            "Feedback webhook called but FEEDBACK_BOT_TOKEN or FEEDBACK_ADMIN_ID not configured"
        )
        return Response(status_code=200)

    # Validate feedback webhook secret token (required for security)
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    client_host = request.client.host if request.client else "unknown"
    if not FEEDBACK_WEBHOOK_SECRET:
        logger.error(
            "FEEDBACK_WEBHOOK_SECRET is missing while feedback feature is enabled."
        )
        return Response(status_code=503)
    if secret_header != FEEDBACK_WEBHOOK_SECRET:
        logger.warning(
            f"Feedback webhook request with invalid secret token from {client_host}"
        )
        return Response(status_code=403)

    try:
        data = await request.json()

        # Only process messages that are replies
        message = data.get("message", {})
        reply_to = message.get("reply_to_message")

        if not reply_to:
            # Not a reply, ignore
            return Response(status_code=200)

        # Check if the sender is the admin
        from_user = message.get("from", {})
        if from_user.get("id") != FEEDBACK_ADMIN_ID:
            return Response(status_code=200)

        # Get the original feedback by the replied message ID
        admin_msg_id = reply_to.get("message_id")
        feedback = get_feedback_by_admin_msg_id(admin_msg_id)

        if not feedback:
            logger.warning(f"Feedback not found for admin_msg_id: {admin_msg_id}")
            return Response(status_code=200)

        # Send reply to the original user via the main bot
        reply_text = message.get("text", "")
        if not reply_text:
            return Response(status_code=200)

        user_id = feedback["user_id"]

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Send reply via main bot
            response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user_id,
                    "text": f"<b>Javob:</b>\n\n{safe_html(reply_text)}",
                    "parse_mode": "HTML",
                },
                timeout=10.0,
            )

            if response.status_code == 200 and response.json().get("ok"):
                mark_feedback_replied(feedback["id"])

                # Notify admin that reply was sent
                await client.post(
                    f"https://api.telegram.org/bot{FEEDBACK_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": FEEDBACK_ADMIN_ID,
                        "text": S.FEEDBACK_REPLY_SENT,
                        "reply_to_message_id": message.get("message_id"),
                    },
                    timeout=10.0,
                )
            else:
                # Notify admin of error
                await client.post(
                    f"https://api.telegram.org/bot{FEEDBACK_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": FEEDBACK_ADMIN_ID,
                        "text": S.FEEDBACK_REPLY_ERROR,
                        "reply_to_message_id": message.get("message_id"),
                    },
                    timeout=10.0,
                )

        return Response(status_code=200)

    except Exception as e:
        logger.error(f"Error in feedback webhook: {e}")
        return Response(status_code=500)


# Removed deprecated event handlers for startup and shutdown as they are now handled in the asynccontextmanager.

if __name__ == "__main__":
    # Get the port from the environment variable, default to 8000
    port = int(os.environ.get("PORT", 8080))
    # Run the FastAPI app using uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
