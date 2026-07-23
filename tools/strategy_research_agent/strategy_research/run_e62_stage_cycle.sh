#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STAGE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$STAGE_ROOT/.venv/bin/python"
CONTROL_SECONDS="${E62_CONTROL_SECONDS:-20}"
COLLECTION_SECONDS="${E62_COLLECTION_SECONDS:-1800}"
STATE_DIR="$STAGE_ROOT/state"
LOCK_DIR="$STATE_DIR/.collection.lock"
READINESS="$STAGE_ROOT/user_data/strategy_research/event_studies/latest_e62_force_order_sample_readiness.json"

mkdir -p "$STATE_DIR"

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
    return 0
  fi
  local existing_pid=""
  if [[ -f "$LOCK_DIR/pid" ]]; then
    existing_pid="$(<"$LOCK_DIR/pid")"
  fi
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "E62 staged collector already running as pid $existing_pid; skipping."
    return 1
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

if ! acquire_lock; then
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

cd "$STAGE_ROOT"

if [[ -f "$READINESS" ]] && "$PYTHON" - "$READINESS" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gates = payload.get("development_count_gates") or {}
raise SystemExit(0 if gates and all(gates.values()) else 1)
PY
then
  echo "E62 staged count gate is complete; collection remains paused."
  exit 0
fi

echo "== E62 staged blind collection =="
echo "control_seconds=$CONTROL_SECONDS collection_seconds=$COLLECTION_SECONDS"
"$PYTHON" user_data/strategy_research/collect_e61_market_force_orders.py \
  --collect \
  --control-seconds "$CONTROL_SECONDS" \
  --duration-seconds "$COLLECTION_SECONDS"
"$PYTHON" user_data/strategy_research/summarize_e61_force_order_inventory.py
"$PYTHON" user_data/strategy_research/summarize_e62_force_order_sample.py

date -u +'%Y-%m-%dT%H:%M:%SZ' >"$STATE_DIR/last_completed_utc.txt"
echo "E62 staged collection cycle completed. Outcomes remain unread."
