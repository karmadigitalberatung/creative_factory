#!/usr/bin/env bash
# Vom Mac aus: Code auf den VPS spielen und die App neu bauen. Daten und .env bleiben auf dem Server.
set -euo pipefail
cd "$(dirname "$0")/.."
# Deploy-Schlüssel (in hPanel beim VPS hinterlegt) nehmen, wenn er da ist; sonst die normale SSH-Konfiguration.
SSH=(ssh)
[ -f "$HOME/.ssh/karma_deploy" ] && SSH=(ssh -i "$HOME/.ssh/karma_deploy" -o IdentitiesOnly=yes)
rsync -az --delete -e "${SSH[*]}" --exclude .git --exclude data --exclude .env --exclude backups --exclude __pycache__ --exclude .DS_Store --exclude .claude --exclude vorlage \
  ./ root@186.241.26.77:/opt/creative-factory/
"${SSH[@]}" root@186.241.26.77 "cd /opt/creative-factory/deploy && docker compose up -d --build && docker image prune -f >/dev/null && docker compose ps"
