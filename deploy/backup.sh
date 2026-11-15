#!/usr/bin/env bash
# Tägliche Sicherung von Datenbank und Medien (cron 3:45), 14 Tage aufbewahren.
set -euo pipefail
cd /opt/creative-factory
mkdir -p backups
stamp=$(date +%F)
docker exec creative-factory-app python -c "import sqlite3; s=sqlite3.connect('/data/app.db'); d=sqlite3.connect('/data/backup-tmp.db'); s.backup(d); d.close()"
mv data/backup-tmp.db "backups/app-$stamp.db"
tar --exclude='data/app.db*' -czf "backups/medien-$stamp.tar.gz" data
find backups -type f -mtime +14 -delete
