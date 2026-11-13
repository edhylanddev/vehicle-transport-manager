#!/usr/bin/env bash
# Nightly backup of the database and uploaded photos/signatures/receipts.
# cron:  15 2 * * *  /opt/vtm/deploy/backup.sh
set -euo pipefail
DEST=/var/backups/vtm
STAMP=$(date +%F)
mkdir -p "$DEST"
mysqldump --single-transaction vtm | gzip > "$DEST/vtm-db-$STAMP.sql.gz"
tar -czf "$DEST/vtm-uploads-$STAMP.tar.gz" -C /var/lib/vtm uploads
find "$DEST" -type f -mtime +30 -delete
