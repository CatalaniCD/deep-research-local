#!/usr/bin/env bash
# Debug run: verbose Python/uvicorn/Express logs + API auto-reload on code changes.
# Usage: ./run-debug.sh  (equivalent to ./run.sh --debug)
set -euo pipefail
cd "$(dirname "$0")"
exec ./run.sh --debug
