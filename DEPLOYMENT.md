# Deploying Tarjimon Bot to VPS with Docker

This guide covers deploying the Tarjimon Telegram bot to a VPS using Docker.

## Prerequisites

- VPS with Docker and Docker Compose installed
- SSH access to the VPS
- Domain name (optional, for webhook with SSL)

## Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram Bot API token from @BotFather |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `GEMINI_MODEL_NAME` | Yes | Gemini model (e.g., `gemini-2.0-flash`) |
| `WEBHOOK_URL` | Yes | Public URL for webhook (e.g., `https://example.com/webhook`) |
| `WEBHOOK_SECRET` | Yes | Random secret string for webhook validation |
| `SUPADATA_API_KEY` | No | Supadata API key for YouTube transcripts |
| `ADMIN_USERNAME` | No | Admin dashboard username (default: `admin`) |
| `ADMIN_PASSWORD` | No | Admin dashboard password (required to access `/admin`) |
| `FEEDBACK_BOT_TOKEN` | No | Separate bot token for feedback feature (from @BotFather) |
| `FEEDBACK_ADMIN_ID` | No | Your Telegram user ID to receive feedback messages |
| `FEEDBACK_WEBHOOK_SECRET` | Conditional | Required when feedback feature is enabled; validates `/feedback_webhook` |

## Deployment Steps

### 1. Connect to VPS

```bash
ssh root@your-vps-ip
```

### 2. Create Project Directory

```bash
mkdir -p /opt/tarjimon
cd /opt/tarjimon
```

### 3. Clone Repository

```bash
git clone https://github.com/bittabola/tarjimon.git .
```

### 4. Create Environment File

```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL_NAME=gemini-2.0-flash
WEBHOOK_URL=https://your-domain.com/webhook
WEBHOOK_SECRET=your_random_secret_string
SUPADATA_API_KEY=your_supadata_api_key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_admin_password
TARJIMON_DB_PATH=/app/data/sqlite_data
TARJIMON_LOG_PATH=/app/logs
# Optional: Feedback feature (requires separate bot)
FEEDBACK_BOT_TOKEN=your_feedback_bot_token
FEEDBACK_ADMIN_ID=your_telegram_user_id
FEEDBACK_WEBHOOK_SECRET=your_feedback_webhook_secret
EOF
```

Replace placeholder values with actual credentials.

### 5. Create docker-compose.yaml

```bash
cat > docker-compose.yaml << 'EOF'
services:
  tarjimon:
    build: .
    container_name: tarjimon-bot
    restart: unless-stopped
    ports:
      - "8080:8080"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
EOF
```

### 6. Create Data Directories

```bash
mkdir -p data/sqlite_data logs
```

### 7. Build and Start

```bash
docker compose up -d --build
```

### 8. Verify Deployment

```bash
docker compose ps
docker compose logs -f tarjimon
```

## Reverse Proxy Setup (Nginx + SSL)

### Install Nginx and Certbot

```bash
apt update
apt install nginx certbot python3-certbot-nginx -y
```

### Configure Nginx

```bash
cat > /etc/nginx/sites-available/tarjimon << 'EOF'
server {
    listen 80;
    server_name your-domain.com;

    location /webhook {
        proxy_pass http://127.0.0.1:8080/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -s /etc/nginx/sites-available/tarjimon /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### Enable SSL

```bash
certbot --nginx -d your-domain.com
```

## Management Commands

| Task | Command |
|------|---------|
| View logs | `docker compose logs -f tarjimon` |
| Restart | `docker compose restart tarjimon` |
| Stop | `docker compose down` |
| Update & redeploy | `git pull && docker compose up -d --build` |
| Enter container | `docker exec -it tarjimon-bot bash` |

## Updating the Bot

```bash
cd /opt/tarjimon
git pull
docker compose up -d --build
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Container won't start | Check logs: `docker compose logs tarjimon` |
| Webhook not receiving | Verify `WEBHOOK_URL` is publicly accessible |
| Database errors | Ensure `data/sqlite_data` directory exists |
| Missing env vars | Verify `.env` file contains all required variables |

## File Structure

```
/opt/tarjimon/
├── .env                 # Environment variables (not in git)
├── docker-compose.yaml  # Docker Compose config
├── Dockerfile
├── data/
│   └── sqlite_data/     # Persistent database
├── logs/                # Application logs
└── [source files]
```

## Security Notes

- Never commit `.env` file to git
- Use strong random string for `WEBHOOK_SECRET`
- Keep VPS firewall enabled (ports 22, 80, 443)
- Regularly update system and Docker images

## Admin Dashboard

The bot includes an admin dashboard at `/admin` for monitoring usage and statistics.

To enable it:
1. Set `ADMIN_PASSWORD` in your `.env` file
2. Optionally set `ADMIN_USERNAME` (defaults to `admin`)
3. Access via `https://your-domain.com/admin`

The dashboard uses HTTP Basic Authentication.
