#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
