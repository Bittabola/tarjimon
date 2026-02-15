"""Admin dashboard module for Tarjimon bot."""

import secrets
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
from config import (
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    NET_REVENUE_PER_STAR,
    REVENUE_PER_VIDEO_MINUTE,
    REVENUE_PER_TRANSLATION,
    logger,
)
from constants import PRICING_CONSTANTS
from database import DatabaseManager

# Security
security = HTTPBasic()

# Router for dashboard endpoints
router = APIRouter(prefix="/admin", tags=["admin"])


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials."""
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin dashboard not configured. Set ADMIN_PASSWORD environment variable.",
        )

    is_username_correct = secrets.compare_digest(
        credentials.username.encode("utf-8"), ADMIN_USERNAME.encode("utf-8")
    )
    is_password_correct = secrets.compare_digest(
        credentials.password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")
    )

    if not (is_username_correct and is_password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# --- Helper Functions ---


def get_date_range(days: int = 30) -> tuple[str, str]:
    """Get ISO date strings for a date range."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).isoformat()
    end = now.isoformat()
    return start, end


def format_currency(amount: float) -> str:
    """Format a number as currency."""
    if amount >= 1:
        return f"${amount:.2f}"
    elif amount >= 0.01:
        return f"${amount:.3f}"
    else:
        return f"${amount:.6f}"


# Timezone constants for dashboard display
TZ_EDMONTON = ZoneInfo("America/Edmonton")
TZ_TASHKENT = ZoneInfo("Asia/Tashkent")


def format_timestamp_dual_tz(iso_timestamp: str | None) -> str:
    """Format an ISO timestamp for display in both Edmonton and Tashkent timezones.

    Args:
        iso_timestamp: ISO format timestamp string (UTC)

    Returns:
        HTML string with both timezones stacked
    """
    if not iso_timestamp:
        return "-"

    try:
        # Parse ISO timestamp (stored as UTC)
        dt_utc = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

        # Convert to both timezones
        dt_edmonton = dt_utc.astimezone(TZ_EDMONTON)
        dt_tashkent = dt_utc.astimezone(TZ_TASHKENT)

        # Format: "Mon DD HH:MM"
        fmt = "%b %d %H:%M"
        edmonton_str = dt_edmonton.strftime(fmt)
        tashkent_str = dt_tashkent.strftime(fmt)

        return f'<span title="Edmonton / Tashkent">{edmonton_str}<br><span class="tz-secondary">{tashkent_str}</span></span>'
    except Exception:
        # Fallback to simple format if parsing fails
        return iso_timestamp[:19].replace("T", " ") if iso_timestamp else "-"


# --- Dashboard Data Functions ---


def get_overview_stats(days: int = 30) -> dict:
    """Get overview statistics for the dashboard."""
    db_manager = DatabaseManager()
    start_date, _ = get_date_range(days)

    stats = {
        "total_requests": 0,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "premium_cost_usd": 0.0,
        "free_cost_usd": 0.0,
        "unique_users": 0,
        "total_revenue_stars": 0,
        "total_revenue_usd": 0.0,
        "net_revenue_usd": 0.0,
        "total_errors": 0,
        "premium_users": 0,
        "free_users": 0,
    }

    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Token usage stats with tier breakdown
            cursor.execute(
                """
                SELECT 
                    COUNT(*) as requests,
                    SUM(u.token_count) as tokens,
                    SUM(u.input_tokens) as input_tokens,
                    SUM(u.output_tokens) as output_tokens,
                    SUM(u.cost_usd) as cost,
                    COUNT(DISTINCT u.user_id) as users,
                    SUM(CASE WHEN s.tier = 'premium' THEN u.cost_usd ELSE 0 END) as premium_cost,
                    SUM(CASE WHEN s.tier != 'premium' OR s.tier IS NULL THEN u.cost_usd ELSE 0 END) as free_cost
                FROM api_token_usage u
                LEFT JOIN user_subscriptions s ON u.user_id = s.user_id
                WHERE u.timestamp_utc >= ?
            """,
                (start_date,),
            )
            row = cursor.fetchone()
            if row:
                stats["total_requests"] = row[0] or 0
                stats["total_tokens"] = row[1] or 0
                stats["total_input_tokens"] = row[2] or 0
                stats["total_output_tokens"] = row[3] or 0
                stats["total_cost_usd"] = row[4] or 0.0
                stats["unique_users"] = row[5] or 0
                stats["premium_cost_usd"] = row[6] or 0.0
                stats["free_cost_usd"] = row[7] or 0.0

            # Payment stats
            cursor.execute(
                """
                SELECT SUM(amount_stars) as stars
                FROM payment_history
                WHERE timestamp_utc >= ?
            """,
                (start_date,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                stats["total_revenue_stars"] = row[0]
                stats["total_revenue_usd"] = row[0] * PRICING_CONSTANTS.STARS_TO_USD
                stats["net_revenue_usd"] = row[0] * NET_REVENUE_PER_STAR

            # Error count
            cursor.execute(
                """
                SELECT COUNT(*) FROM errors_log WHERE timestamp_utc >= ?
            """,
                (start_date,),
            )
            row = cursor.fetchone()
            stats["total_errors"] = row[0] if row else 0

            # User tier counts
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                """
                SELECT tier, COUNT(*) as count
                FROM user_subscriptions
                WHERE expires_at > ?
                GROUP BY tier
            """,
                (now_iso,),
            )
            for row in cursor.fetchall():
                if row[0] == "premium":
                    stats["premium_users"] = row[1]
                else:
                    stats["free_users"] = row[1]

    except Exception as e:
        logger.error(f"Error getting overview stats: {e}")

    # Calculate profit/loss metrics
    # Premium P/L: net revenue from stars minus only premium users' API costs
    stats["premium_profit_loss"] = stats["net_revenue_usd"] - stats["premium_cost_usd"]
    # Overall P/L: net revenue minus all API costs (including free users)
    stats["overall_profit_loss"] = stats["net_revenue_usd"] - stats["total_cost_usd"]

    return stats


def get_errors_list(limit: int = 100, offset: int = 0) -> list[dict]:
    """Get list of recent errors."""
    db_manager = DatabaseManager()
    errors = []

    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp_utc, user_id, error_type, error_message, 
                       content_type, content_preview
                FROM errors_log
                ORDER BY timestamp_utc DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )

            for row in cursor.fetchall():
                errors.append(
                    {
                        "id": row[0],
                        "timestamp": row[1],
                        "user_id": row[2],
                        "error_type": row[3],
                        "error_message": row[4],
                        "content_type": row[5],
                        "content_preview": row[6],
                    }
                )
    except Exception as e:
        logger.error(f"Error getting errors list: {e}")

    return errors


def get_requests_list(limit: int = 100, offset: int = 0) -> list[dict]:
    """Get list of recent requests with cost and amortized revenue info.

    Followup costs are aggregated into their parent YouTube video's P/L.
    Uses a single optimized query with LEFT JOIN subquery for followup aggregation.
    """
    db_manager = DatabaseManager()
    requests = []

    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Single optimized query: JOIN main requests with aggregated followup costs
            cursor.execute(
                """
                SELECT
                    u.id, u.timestamp_utc, u.user_id, u.service_name,
                    u.token_count, u.input_tokens, u.output_tokens, u.cost_usd,
                    u.content_type, u.content_preview, u.video_duration_minutes,
                    s.tier as user_tier,
                    COALESCE(f.followup_cost, 0) as followup_cost,
                    COALESCE(f.followup_count, 0) as followup_count
                FROM api_token_usage u
                LEFT JOIN user_subscriptions s ON u.user_id = s.user_id
                LEFT JOIN (
                    SELECT parent_request_id,
                           SUM(cost_usd) as followup_cost,
                           COUNT(*) as followup_count
                    FROM api_token_usage
                    WHERE content_type = 'youtube_followup' AND parent_request_id IS NOT NULL
                    GROUP BY parent_request_id
                ) f ON u.id = f.parent_request_id
                WHERE u.content_type != 'youtube_followup'
                ORDER BY u.timestamp_utc DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )

            for row in cursor.fetchall():
                request_id = row[0]
                user_tier = row[11] or "free"
                cost = row[7] or 0.0
                content_type = row[8]
                content_preview = row[9]
                video_duration = row[10] or 0
                followup_cost = row[12] or 0.0
                followup_count = row[13] or 0

                # Total cost includes followup costs for YouTube videos
                total_cost = cost + followup_cost

                # Calculate amortized revenue for premium users
                revenue = 0.0
                if user_tier == "premium":
                    if content_type == "youtube" and video_duration > 0:
                        # YouTube: revenue per minute of video
                        revenue = video_duration * REVENUE_PER_VIDEO_MINUTE
                    elif content_type in ("text", "image", "image_with_caption"):
                        # Translation: flat rate per request
                        revenue = REVENUE_PER_TRANSLATION

                # Profit/Loss = revenue - total cost (including followups for videos)
                profit_loss = revenue - total_cost

                requests.append(
                    {
                        "id": request_id,
                        "timestamp": row[1],
                        "user_id": row[2],
                        "service_name": row[3],
                        "token_count": row[4],
                        "input_tokens": row[5] or 0,
                        "output_tokens": row[6] or 0,
                        "cost_usd": total_cost,  # Includes followup costs for videos
                        "content_type": content_type,
                        "content_preview": content_preview,
                        "video_duration_minutes": video_duration,
                        "user_tier": user_tier,
                        "revenue": revenue,
                        "profit_loss": profit_loss,
                        "followup_cost": followup_cost,
                        "followup_count": followup_count,
                    }
                )
    except Exception as e:
        logger.error(f"Error getting requests list: {e}")

    return requests


def get_user_profitability(days: int = 30) -> list[dict]:
    """Get profitability breakdown per premium user."""
    db_manager = DatabaseManager()
    users = []
    start_date, _ = get_date_range(days)

    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Get premium users with their costs and payments
            cursor.execute(
                """
                SELECT 
                    s.user_id,
                    s.tier,
                    COALESCE(SUM(u.cost_usd), 0) as total_cost,
                    COALESCE(SUM(u.token_count), 0) as total_tokens,
                    COUNT(u.id) as request_count,
                    (SELECT COALESCE(SUM(p.amount_stars), 0) 
                     FROM payment_history p 
                     WHERE p.user_id = s.user_id AND p.timestamp_utc >= ?) as stars_paid
                FROM user_subscriptions s
                LEFT JOIN api_token_usage u ON s.user_id = u.user_id AND u.timestamp_utc >= ?
                WHERE s.tier = 'premium'
                GROUP BY s.user_id, s.tier
                ORDER BY total_cost DESC
            """,
                (start_date, start_date),
            )

            for row in cursor.fetchall():
                stars_paid = row[5] or 0
                revenue = stars_paid * NET_REVENUE_PER_STAR
                cost = row[2] or 0
                profit = revenue - cost

                users.append(
                    {
                        "user_id": row[0],
                        "tier": row[1],
                        "total_cost": cost,
                        "total_tokens": row[3],
                        "request_count": row[4],
                        "stars_paid": stars_paid,
                        "revenue_usd": revenue,
                        "profit_loss": profit,
                        "profitable": profit >= 0,
                    }
                )
    except Exception as e:
        logger.error(f"Error getting user profitability: {e}")

    return users


def get_daily_stats(days: int = 30) -> list[dict]:
    """Get daily breakdown of usage, costs, revenue, and balance."""
    db_manager = DatabaseManager()
    daily = []
    start_date, _ = get_date_range(days)

    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Get daily stats with amortized revenue calculation
            # Revenue is calculated per-request based on content type and user tier
            cursor.execute(
                """
                SELECT 
                    DATE(u.timestamp_utc) as date,
                    COUNT(*) as requests,
                    SUM(u.token_count) as tokens,
                    SUM(u.cost_usd) as cost,
                    COUNT(DISTINCT u.user_id) as users,
                    -- YouTube video minutes (only for premium users)
                    SUM(CASE 
                        WHEN u.content_type = 'youtube' AND s.tier = 'premium' 
                        THEN COALESCE(u.video_duration_minutes, 0) 
                        ELSE 0 
                    END) as premium_video_minutes,
                    -- Translation count (only for premium users)
                    SUM(CASE 
                        WHEN u.content_type IN ('text', 'image', 'image_with_caption') AND s.tier = 'premium' 
                        THEN 1 
                        ELSE 0 
                    END) as premium_translations
                FROM api_token_usage u
                LEFT JOIN user_subscriptions s ON u.user_id = s.user_id
                WHERE u.timestamp_utc >= ?
                GROUP BY DATE(u.timestamp_utc)
                ORDER BY date DESC
            """,
                (start_date,),
            )

            for row in cursor.fetchall():
                cost = row[3] or 0.0
                premium_video_minutes = row[5] or 0
                premium_translations = row[6] or 0

                # Calculate amortized revenue
                revenue = (
                    premium_video_minutes * REVENUE_PER_VIDEO_MINUTE
                    + premium_translations * REVENUE_PER_TRANSLATION
                )
                balance = revenue - cost

                daily.append(
                    {
                        "date": row[0],
                        "requests": row[1],
                        "tokens": row[2] or 0,
                        "cost": cost,
                        "users": row[4],
                        "revenue": revenue,
                        "balance": balance,
                    }
                )
    except Exception as e:
        logger.error(f"Error getting daily stats: {e}")

    return daily


# --- HTML Templates ---


def render_base_html(title: str, content: str, active_tab: str = "overview") -> str:
    """Render base HTML template with navigation."""
    nav_items = [
        ("overview", "Overview", "/admin/"),
        ("errors", "Errors", "/admin/errors"),
        ("requests", "Requests", "/admin/requests"),
        ("users", "User Profitability", "/admin/users"),
        ("daily", "Daily Stats", "/admin/daily"),
    ]

    nav_html = ""
    for tab_id, tab_name, tab_url in nav_items:
        active_class = "active" if tab_id == active_tab else ""
        nav_html += (
            f'<a href="{tab_url}" class="nav-link {active_class}">{tab_name}</a>\n'
        )

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title} - Tarjimon Admin</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            color: #333;
            line-height: 1.6;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
        header {{ 
            background: #2c3e50;
            color: white;
            padding: 15px 20px;
            margin-bottom: 20px;
        }}
        header h1 {{ font-size: 1.5rem; font-weight: 600; }}
        nav {{ 
            display: flex;
            gap: 10px;
            margin-top: 10px;
            flex-wrap: wrap;
        }}
        .nav-link {{
            color: #bdc3c7;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 4px;
            transition: all 0.2s;
        }}
        .nav-link:hover {{ background: #34495e; color: white; }}
        .nav-link.active {{ background: #3498db; color: white; }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .stat-card h3 {{ 
            font-size: 0.85rem;
            color: #7f8c8d;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}
        .stat-card .value {{ 
            font-size: 1.75rem;
            font-weight: 700;
            color: #2c3e50;
        }}
        .stat-card .value.positive {{ color: #27ae60; }}
        .stat-card .value.negative {{ color: #e74c3c; }}
        
        table {{
            width: 100%;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border-collapse: collapse;
        }}
        th, td {{
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #ecf0f1;
        }}
        th {{
            background: #34495e;
            color: white;
            font-weight: 600;
            font-size: 0.85rem;
            text-transform: uppercase;
        }}
        tr:hover {{ background: #f8f9fa; }}
        
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .badge-premium {{ background: #f39c12; color: white; }}
        .badge-free {{ background: #95a5a6; color: white; }}
        .badge-error {{ background: #e74c3c; color: white; }}
        .badge-youtube {{ background: #c4302b; color: white; }}
        .badge-translation {{ background: #3498db; color: white; }}
        
        .text-muted {{ color: #7f8c8d; font-size: 0.85rem; }}
        .text-truncate {{ 
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .tz-secondary {{ color: #95a5a6; font-size: 0.8rem; }}
        .timestamp-cell {{ line-height: 1.3; }}
        
        .profit {{ color: #27ae60; font-weight: 600; }}
        .loss {{ color: #e74c3c; font-weight: 600; }}
        
        .section-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 15px;
            color: #2c3e50;
        }}
        
        @media (max-width: 768px) {{
            .container {{ padding: 10px; }}
            th, td {{ padding: 8px 10px; font-size: 0.85rem; }}
            .stat-card .value {{ font-size: 1.4rem; }}
        }}
    </style>
</head>
<body>
    <header>
        <div class="container">
            <h1>Tarjimon Admin Dashboard</h1>
            <nav>{nav_html}</nav>
        </div>
    </header>
    <main class="container">
        {content}
    </main>
</body>
</html>"""


# --- API Endpoints ---


@router.get("/", response_class=HTMLResponse)
async def dashboard_overview(
    username: str = Depends(verify_credentials),
    days: int = Query(default=30, ge=1, le=365),
):
    """Main dashboard overview."""
    stats = get_overview_stats(days)

    premium_pl_class = "positive" if stats["premium_profit_loss"] >= 0 else "negative"
    premium_pl_sign = "+" if stats["premium_profit_loss"] >= 0 else ""
    overall_pl_class = "positive" if stats["overall_profit_loss"] >= 0 else "negative"
    overall_pl_sign = "+" if stats["overall_profit_loss"] >= 0 else ""

    content = f"""
    <h2 class="section-title">Overview (Last {days} Days)</h2>
    
    <div class="stats-grid">
        <div class="stat-card">
            <h3>Total Requests</h3>
            <div class="value">{stats["total_requests"]:,}</div>
        </div>
        <div class="stat-card">
            <h3>Unique Users</h3>
            <div class="value">{stats["unique_users"]:,}</div>
        </div>
        <div class="stat-card">
            <h3>Premium Users</h3>
            <div class="value">{stats["premium_users"]:,}</div>
        </div>
        <div class="stat-card">
            <h3>Total Tokens</h3>
            <div class="value">{stats["total_tokens"]:,}</div>
        </div>
        <div class="stat-card">
            <h3>Input Tokens</h3>
            <div class="value">{stats["total_input_tokens"]:,}</div>
        </div>
        <div class="stat-card">
            <h3>Output Tokens</h3>
            <div class="value">{stats["total_output_tokens"]:,}</div>
        </div>
        <div class="stat-card">
            <h3>Revenue (Stars)</h3>
            <div class="value">{stats["total_revenue_stars"]:,} Stars</div>
        </div>
        <div class="stat-card">
            <h3>Net Revenue</h3>
            <div class="value positive">{format_currency(stats["net_revenue_usd"])}</div>
        </div>
        <div class="stat-card">
            <h3>Errors</h3>
            <div class="value negative">{stats["total_errors"]:,}</div>
        </div>
    </div>

    <h2 class="section-title">Costs & Profitability</h2>
    
    <div class="stats-grid">
        <div class="stat-card">
            <h3>Premium API Cost</h3>
            <div class="value negative">{format_currency(stats["premium_cost_usd"])}</div>
        </div>
        <div class="stat-card">
            <h3>Free API Cost</h3>
            <div class="value negative">{format_currency(stats["free_cost_usd"])}</div>
        </div>
        <div class="stat-card">
            <h3>Total API Cost</h3>
            <div class="value negative">{format_currency(stats["total_cost_usd"])}</div>
        </div>
        <div class="stat-card">
            <h3>Premium P/L</h3>
            <div class="value {premium_pl_class}">{premium_pl_sign}{format_currency(stats["premium_profit_loss"])}</div>
        </div>
        <div class="stat-card">
            <h3>Overall P/L</h3>
            <div class="value {overall_pl_class}">{overall_pl_sign}{format_currency(stats["overall_profit_loss"])}</div>
        </div>
    </div>
    
    <p class="text-muted">
        Pricing: Input ${PRICING_CONSTANTS.GEMINI_INPUT_PRICE_PER_M}/M tokens, Output+Thinking ${PRICING_CONSTANTS.GEMINI_OUTPUT_PRICE_PER_M}/M tokens (Gemini 2.5 Pro)<br>
        Premium P/L = Net Revenue - Premium API Cost | Overall P/L = Net Revenue - Total API Cost
    </p>
    """

    return render_base_html("Overview", content, "overview")


@router.get("/errors", response_class=HTMLResponse)
async def dashboard_errors(
    username: str = Depends(verify_credentials),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Errors list page."""
    errors = get_errors_list(limit, offset)

    rows = ""
    for err in errors:
        timestamp = format_timestamp_dual_tz(err["timestamp"])
        content_type = err["content_type"] or "-"
        preview = (err["content_preview"] or "-")[:50]

        rows += f"""
        <tr>
            <td class="timestamp-cell">{timestamp}</td>
            <td>{err["user_id"] or "-"}</td>
            <td><span class="badge badge-error">{err["error_type"]}</span></td>
            <td class="text-truncate">{err["error_message"][:100]}</td>
            <td>{content_type}</td>
            <td class="text-truncate text-muted">{preview}</td>
        </tr>
        """

    if not errors:
        rows = '<tr><td colspan="6" style="text-align:center;padding:40px;">No errors found</td></tr>'

    content = f"""
    <h2 class="section-title">Recent Errors</h2>
    <table>
        <thead>
            <tr>
                <th>Time (EDT/UZT)</th>
                <th>User ID</th>
                <th>Type</th>
                <th>Message</th>
                <th>Content Type</th>
                <th>Preview</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """

    return render_base_html("Errors", content, "errors")


@router.get("/requests", response_class=HTMLResponse)
async def dashboard_requests(
    username: str = Depends(verify_credentials),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Requests list page."""
    requests = get_requests_list(limit, offset)

    rows = ""
    for req in requests:
        timestamp = format_timestamp_dual_tz(req["timestamp"])
        tier_class = "badge-premium" if req["user_tier"] == "premium" else "badge-free"

        cost_html = f'<span class="loss">{format_currency(req["cost_usd"])}</span>'

        # Revenue display
        revenue = req.get("revenue", 0)
        if revenue > 0:
            revenue_html = f'<span class="profit">{format_currency(revenue)}</span>'
        else:
            revenue_html = "-"

        # P/L display
        profit_loss = req.get("profit_loss", 0)
        profit_class = "profit" if profit_loss >= 0 else "loss"
        profit_sign = "+" if profit_loss > 0 else ""
        profit_html = f'<span class="{profit_class}">{profit_sign}{format_currency(profit_loss)}</span>'

        # Content type with duration and followup info for YouTube
        content_type = req.get("content_type", "-") or "-"
        duration = req.get("video_duration_minutes", 0)
        followup_count = req.get("followup_count", 0)
        if content_type == "youtube" and duration > 0:
            if followup_count > 0:
                content_type = f"youtube ({duration}min +{followup_count}Q&A)"
            else:
                content_type = f"youtube ({duration}min)"

        preview = (req["content_preview"] or "-")[:30]

        rows += f"""
        <tr>
            <td class="timestamp-cell">{timestamp}</td>
            <td>{req["user_id"]}</td>
            <td><span class="badge {tier_class}">{req["user_tier"]}</span></td>
            <td>{content_type}</td>
            <td>{cost_html}</td>
            <td>{revenue_html}</td>
            <td>{profit_html}</td>
            <td class="text-truncate text-muted">{preview}</td>
        </tr>
        """

    if not requests:
        rows = '<tr><td colspan="8" style="text-align:center;padding:40px;">No requests found</td></tr>'

    content = f"""
    <h2 class="section-title">Recent Requests</h2>
    <table>
        <thead>
            <tr>
                <th>Time (EDT/UZT)</th>
                <th>User ID</th>
                <th>Tier</th>
                <th>Type</th>
                <th>Cost</th>
                <th>Revenue</th>
                <th>P/L</th>
                <th>Preview</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """

    return render_base_html("Requests", content, "requests")


@router.get("/users", response_class=HTMLResponse)
async def dashboard_users(
    username: str = Depends(verify_credentials),
    days: int = Query(default=30, ge=1, le=365),
):
    """User profitability page."""
    users = get_user_profitability(days)

    rows = ""
    for user in users:
        profit_class = "profit" if user["profitable"] else "loss"
        profit_sign = "+" if user["profit_loss"] >= 0 else ""

        rows += f"""
        <tr>
            <td>{user["user_id"]}</td>
            <td><span class="badge badge-premium">premium</span></td>
            <td>{user["request_count"]:,}</td>
            <td>{user["total_tokens"]:,}</td>
            <td class="loss">{format_currency(user["total_cost"])}</td>
            <td>{user["stars_paid"]:,} Stars</td>
            <td class="profit">{format_currency(user["revenue_usd"])}</td>
            <td class="{profit_class}">{profit_sign}{format_currency(user["profit_loss"])}</td>
        </tr>
        """

    if not users:
        rows = '<tr><td colspan="8" style="text-align:center;padding:40px;">No premium users found</td></tr>'

    content = f"""
    <h2 class="section-title">Premium User Profitability (Last {days} Days)</h2>
    <table>
        <thead>
            <tr>
                <th>User ID</th>
                <th>Tier</th>
                <th>Requests</th>
                <th>Tokens</th>
                <th>API Cost</th>
                <th>Paid</th>
                <th>Net Revenue</th>
                <th>Profit/Loss</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """

    return render_base_html("User Profitability", content, "users")


@router.get("/daily", response_class=HTMLResponse)
async def dashboard_daily(
    username: str = Depends(verify_credentials),
    days: int = Query(default=30, ge=1, le=365),
):
    """Daily stats page."""
    daily = get_daily_stats(days)

    rows = ""
    for day in daily:
        balance = day.get("balance", 0)
        balance_class = "profit" if balance >= 0 else "loss"
        balance_sign = "+" if balance > 0 else ""

        revenue = day.get("revenue", 0)
        revenue_html = format_currency(revenue) if revenue > 0 else "-"

        rows += f"""
        <tr>
            <td>{day["date"]}</td>
            <td>{day["requests"]:,}</td>
            <td>{day["users"]:,}</td>
            <td>{day["tokens"]:,}</td>
            <td class="loss">{format_currency(day["cost"])}</td>
            <td class="profit">{revenue_html}</td>
            <td class="{balance_class}">{balance_sign}{format_currency(balance)}</td>
        </tr>
        """

    if not daily:
        rows = '<tr><td colspan="7" style="text-align:center;padding:40px;">No data found</td></tr>'

    content = f"""
    <h2 class="section-title">Daily Statistics (Last {days} Days)</h2>
    <table>
        <thead>
            <tr>
                <th>Date</th>
                <th>Requests</th>
                <th>Users</th>
                <th>Tokens</th>
                <th>Cost</th>
                <th>Revenue</th>
                <th>Balance</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """

    return render_base_html("Daily Stats", content, "daily")


# --- JSON API Endpoints (for programmatic access) ---


@router.get("/api/overview")
async def api_overview(
    username: str = Depends(verify_credentials),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get overview stats as JSON."""
    return get_overview_stats(days)


@router.get("/api/errors")
async def api_errors(
    username: str = Depends(verify_credentials),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Get errors as JSON."""
    return get_errors_list(limit, offset)


@router.get("/api/requests")
async def api_requests(
    username: str = Depends(verify_credentials),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Get requests as JSON."""
    return get_requests_list(limit, offset)


@router.get("/api/users")
async def api_users(
    username: str = Depends(verify_credentials),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get user profitability as JSON."""
    return get_user_profitability(days)


@router.get("/api/daily")
async def api_daily(
    username: str = Depends(verify_credentials),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get daily stats as JSON."""
    return get_daily_stats(days)
