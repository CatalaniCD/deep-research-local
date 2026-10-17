#!/usr/bin/env bash
# Run the full local stack: FastAPI (:8000) + Express UI (:3000).
# Usage: ./run.sh [--debug]
set -euo pipefail
cd "$(dirname "$0")"

DEBUG_MODE=0
if [ "${1:-}" = "--debug" ]; then DEBUG_MODE=1; fi

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-3000}"

# Load secrets (gitignored). Prefer .env, fall back to .dev.vars.
if [ -f .env ]; then
  set -a; source .env; set +a
elif [ -f .dev.vars ]; then
  echo "Note: using .dev.vars (consider copying to .env)"
  set -a; source .dev.vars; set +a
else
  echo "No .env found. Copy .env.example to .env and fill keys."
  exit 1
fi

export PYTHONPATH=.
export API_TARGET="http://127.0.0.1:${API_PORT}"

UVICORN_FLAGS=""
# shellcheck disable=SC2034
if [ "$DEBUG_MODE" = 1 ]; then
  export LOG_LEVEL=DEBUG
  export DEBUG="express:*"
  UVICORN_FLAGS="--log-level debug --reload"
  echo "DEBUG mode: verbose logs + API auto-reload"
fi

# Python env
if [ ! -x .venv/bin/python ]; then
  echo "Creating venv..."
  uv venv .venv
fi
if ! .venv/bin/python -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "Installing Python deps..."
  uv pip install -p .venv/bin/python -e . 2>/dev/null || \
    uv pip install -p .venv/bin/python fastapi "uvicorn>=0.30" langgraph langchain langchain-core langchain-openai langchain-anthropic "tavily-python>=0.5" pydantic python-dotenv
fi

# Node deps
if [ ! -d ui/node_modules ]; then
  echo "Installing UI deps..."
  (cd ui && npm install)
fi

cleanup() {
  kill "$API_PID" "$UI_PID" 2>/dev/null || true
  # uvicorn --reload spawns a child worker that survives kill of the parent
  pkill -f "uvicorn deep_research.server:app.*${API_PORT}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting API on :${API_PORT}..."
# shellcheck disable=SC2086
.venv/bin/python -m uvicorn deep_research.server:app --host 127.0.0.1 --port "$API_PORT" $UVICORN_FLAGS &
API_PID=$!

# Wait for API health (max ~30s)
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null || { echo "API failed to start"; exit 1; }

echo "Starting UI on :${UI_PORT}..."
UI_PORT="$UI_PORT" API_TARGET="$API_TARGET" NODE_OPTIONS="--no-deprecation" node ui/server.js &
UI_PID=$!

echo "Ready: UI http://127.0.0.1:${UI_PORT} -> API ${API_TARGET}"
wait
