#!/bin/sh
set -eu

exec python -m uvicorn backend.app.main:app \
  --host 0.0.0.0 \
  --port "${BACKEND_PORT:-15200}" \
  --proxy-headers \
  --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
