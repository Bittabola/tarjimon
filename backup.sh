#!/bin/bash
# =============================================================================
# Tarjimon Database Backup Script
# =============================================================================
# Backs up SQLite database to Storj (S3-compatible)
#
# Setup on VPS:
# 1. Install rclone: curl https://rclone.org/install.sh | sudo bash
# 2. Configure Storj remote:
#    rclone config
#    - Choose 'n' for new remote
#    - Name: storj
#    - Type: s3 (option 5)
#    - Provider: Other (option 35 or similar)
#    - Access Key ID: (from Storj dashboard)
#    - Secret Access Key: (from Storj dashboard)
#    - Endpoint: gateway.storjshare.io
#    - Leave other options as default
# 3. Create bucket in Storj dashboard: tarjimon-backups
# 4. Make executable: chmod +x backup.sh
# 5. Add to cron: crontab -e
#    0 3 * * * /path/to/tarjimon/backup.sh >> /path/to/tarjimon/logs/backup.log 2>&1
# =============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="${TARJIMON_DB_PATH:-$SCRIPT_DIR/data/sqlite_data}"
DB_FILE="$DB_PATH/tracking_data.db"
BACKUP_DIR="$SCRIPT_DIR/backups"
STORJ_REMOTE="${STORJ_REMOTE:-storj}"
STORJ_BUCKET="${STORJ_BUCKET:-tarjimon-backups}"
RETENTION_DAYS=30

# Timestamp for backup filename
TIMESTAMP=$(date -u +"%Y-%m-%d_%H-%M-%S")
BACKUP_NAME="tarjimon_backup_${TIMESTAMP}.db"

# Logging function
log() {
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] $1"
}

# Check if database exists
if [ ! -f "$DB_FILE" ]; then
    log "ERROR: Database file not found: $DB_FILE"
    exit 1
fi

# Create local backup directory
mkdir -p "$BACKUP_DIR"

# Create backup using SQLite's backup command (safe for running database)
log "Starting backup of $DB_FILE"
sqlite3 "$DB_FILE" ".backup '$BACKUP_DIR/$BACKUP_NAME'"

if [ ! -f "$BACKUP_DIR/$BACKUP_NAME" ]; then
    log "ERROR: Backup file was not created"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_DIR/$BACKUP_NAME" | cut -f1)
log "Local backup created: $BACKUP_NAME ($BACKUP_SIZE)"

# Compress the backup
gzip "$BACKUP_DIR/$BACKUP_NAME"
COMPRESSED_NAME="${BACKUP_NAME}.gz"
COMPRESSED_SIZE=$(du -h "$BACKUP_DIR/$COMPRESSED_NAME" | cut -f1)
log "Compressed backup: $COMPRESSED_NAME ($COMPRESSED_SIZE)"

# Upload to Storj (if rclone is available and configured)
if command -v rclone &> /dev/null; then
    if rclone lsd "$STORJ_REMOTE:" &> /dev/null; then
        # Upload backup
        rclone copy "$BACKUP_DIR/$COMPRESSED_NAME" "$STORJ_REMOTE:$STORJ_BUCKET/"
        log "Uploaded to Storj: $STORJ_REMOTE:$STORJ_BUCKET/$COMPRESSED_NAME"
        
        # Clean up old backups in Storj (keep last 30 days)
        log "Cleaning up old Storj backups (keeping last $RETENTION_DAYS days)..."
        rclone delete "$STORJ_REMOTE:$STORJ_BUCKET" --min-age "${RETENTION_DAYS}d" 2>/dev/null || true
        
    else
        log "WARNING: Storj remote not configured. Backup saved locally only."
        log "Run 'rclone config' to set up Storj remote."
    fi
else
    log "WARNING: rclone not installed. Backup saved locally only."
    log "Install rclone: curl https://rclone.org/install.sh | sudo bash"
fi

# Clean up old local backups (keep last 7 days locally)
log "Cleaning up old local backups (keeping last 7 days)..."
find "$BACKUP_DIR" -name "tarjimon_backup_*.db.gz" -mtime +7 -delete 2>/dev/null || true

# Summary
LOCAL_COUNT=$(find "$BACKUP_DIR" -name "tarjimon_backup_*.db.gz" | wc -l)
log "Backup complete. Local backups: $LOCAL_COUNT"
