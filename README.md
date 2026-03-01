# Tarjimon Bot

A Telegram bot that translates text and images into Uzbek using Google Gemini AI.

[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green.svg)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-blue.svg)](https://core.telegram.org/bots)

## Features

- **Text Translation** — Translates text messages from any language to Uzbek using Google Gemini
- **Image Translation (OCR)** — Extracts text from images and translates in a single API call
- **Forwarded Messages** — Translate forwarded messages from other chats
- **Subscription System** — Free tier with limits + premium subscriptions via Telegram Stars
- **Admin Dashboard** — Web dashboard at `/admin` for monitoring usage, costs, and statistics
- **User Feedback** — Built-in feedback system via `/aloqa` command
- **Rate Limiting** — Per-user daily token limits and request throttling

## How It Works

The bot uses Google Gemini's multimodal capabilities to:

1. **Translation**: Detect source language → Translate to Uzbek (skipped if already Uzbek)
2. **Image OCR**: Extract text from images → Detect language → Translate

All processing happens in minimal API calls for speed and cost efficiency.

## Prerequisites

- Python 3.12+
- [Telegram Bot Token](https://t.me/BotFather)
- [Google Gemini API Key](https://ai.google.dev/)
- Public URL for webhook (VPS, cloud, or ngrok for local dev)

## Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/bittabola/tarjimon.git
   cd tarjimon
   ```

2. **Set up environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Install and run**
   ```bash
   pip install -r requirements.txt
   python webhook.py
   ```

The bot starts a FastAPI server on port 8080 and registers the webhook with Telegram.

## Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `GEMINI_MODEL_NAME` | Yes | Model name (e.g., `gemini-2.0-flash`) |
| `WEBHOOK_URL` | Yes | Public webhook URL (e.g., `https://example.com/webhook`) |
| `WEBHOOK_SECRET` | Yes | Random secret for webhook validation |
| `ADMIN_USERNAME` | No | Admin dashboard username (default: `admin`) |
| `ADMIN_PASSWORD` | No | Admin dashboard password |
| `TARJIMON_DB_PATH` | No | Database directory (default: `data/sqlite_data`) |
| `TARJIMON_LOG_PATH` | No | Log directory (default: `logs`) |
| `FEEDBACK_BOT_TOKEN` | No | Separate bot for feedback relay |
| `FEEDBACK_ADMIN_ID` | No | Admin Telegram user ID for feedback |
| `FEEDBACK_WEBHOOK_SECRET` | Conditional | Required when feedback relay is enabled; secret for `/feedback_webhook` |

## Deployment

### Docker (Recommended)

```bash
# Build and run
docker compose up -d --build

# View logs
docker compose logs -f tarjimon
```

For full production deployment with Nginx and SSL, see [DEPLOYMENT.md](DEPLOYMENT.md).

### Local Development

Use ngrok to expose your local server:

```bash
ngrok http 8080
```

Set `WEBHOOK_URL` to the ngrok URL, then run `python webhook.py`.

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and usage instructions |
| `/subscribe` | View subscription plans and current usage limits |
| `/aloqa` | Send feedback to the admin |

## Usage Limits

### Free Tier (resets every 30 days)
- 10 translations
- 20,000 tokens per day

### Premium Tier (350 Telegram Stars)
- 50 translations
- 30-day subscription period

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/health` | GET | Detailed health check with database status |
| `/webhook` | POST | Telegram webhook receiver |
| `/feedback_webhook` | POST | Feedback bot webhook |
| `/admin` | GET | Admin dashboard (HTTP Basic Auth) |

## Project Structure

```
tarjimon/
├── webhook.py           # FastAPI entry point, bot initialization
├── handlers/            # Telegram bot handlers
│   ├── __init__.py      # Re-exports all handlers
│   ├── common.py        # Shared utilities (Gemini client, helpers)
│   ├── translation.py   # Text and image translation
│   ├── subscription.py  # Payments and subscriptions
│   └── feedback.py      # User feedback system
├── config.py            # Configuration and environment loading
├── constants.py         # All limits and magic numbers
├── strings.py           # User-facing strings (Uzbek)
├── database.py          # SQLite database management
├── user_management.py   # Session and rate limiting
├── utils.py             # General utilities
├── admin_dashboard.py   # FastAPI admin routes
├── prompts/             # Gemini prompt templates
│   └── translation.md
└── data/sqlite_data/    # Database files (gitignored)
```

## Tech Stack

- **Runtime**: Python 3.12+
- **Bot Framework**: [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 22.x
- **Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn
- **AI**: [Google Gemini API](https://ai.google.dev/) via `google-genai`
- **Database**: SQLite
- **Image Processing**: Pillow
- **HTTP Client**: httpx

## Development

### Linting and Formatting

```bash
# Install ruff
pip install ruff

# Check for issues
ruff check .

# Auto-format
ruff format .

# Type checking (optional)
pip install mypy
mypy .
```

### Code Style

See [AGENTS.md](AGENTS.md) for detailed code style guidelines:
- Google-style docstrings
- Modern Python 3.12+ type hints (`list[str]`, `dict[str, int]`, `X | None`)
- Imports: stdlib → third-party → local
- Constants in frozen dataclasses

## Contributing

1. Fork the repository
2. Create a feature branch
3. Follow the code style in [AGENTS.md](AGENTS.md)
4. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Google Gemini](https://ai.google.dev/) for AI capabilities
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) for the excellent bot framework
