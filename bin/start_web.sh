#!/usr/bin/env bash
# Production entrypoint (Render, Fly, etc.). Ensures config exists when config.yml is gitignored locally.
set -euo pipefail
if [[ ! -f config.yml ]]; then
  echo "[start_web] No config.yml; using config.fno.quick.yml (override with Secret File or CONFIG_PATH)."
  cp config.fno.quick.yml config.yml
fi
exec gunicorn web_app:app \
  --bind "0.0.0.0:${PORT:-5000}" \
  --workers 1 \
  --threads 2 \
  --timeout 120 \
  --access-logfile -
