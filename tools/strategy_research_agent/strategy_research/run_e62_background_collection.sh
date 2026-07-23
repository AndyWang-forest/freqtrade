#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" || ! -d "$ROOT/.git" || ! -d "$ROOT/user_data/strategy_research" || ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Could not locate freqtrade repo root from $SCRIPT_DIR" >&2
  exit 2
fi

cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
CONTROL_SECONDS="${E62_CONTROL_SECONDS:-20}"
COLLECTION_SECONDS="${E62_COLLECTION_SECONDS:-1800}"
STATE_DIR="$ROOT/user_data/strategy_research/background/e62"
LOCK_DIR="$STATE_DIR/.collection.lock"
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
    echo "E62 background collector already running as pid $existing_pid; skipping."
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

if "$PYTHON" - <<'PY'
import json
from pathlib import Path

path = Path("user_data/strategy_research/event_studies/latest_e62_force_order_sample_readiness.json")
if not path.exists():
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
gates = payload.get("development_count_gates") or {}
raise SystemExit(0 if gates and all(gates.values()) else 1)
PY
then
  echo "E62 frozen count gate is already complete; background acquisition is paused."
  exit 0
fi

echo "== E62 blind background collection =="
echo "control_seconds=$CONTROL_SECONDS collection_seconds=$COLLECTION_SECONDS"
"$PYTHON" user_data/strategy_research/collect_e61_market_force_orders.py \
  --collect \
  --control-seconds "$CONTROL_SECONDS" \
  --duration-seconds "$COLLECTION_SECONDS"

echo "== E61 immutable receipt inventory =="
"$PYTHON" user_data/strategy_research/summarize_e61_force_order_inventory.py

echo "== E62 frozen sample readiness without outcomes =="
"$PYTHON" user_data/strategy_research/summarize_e62_force_order_sample.py

echo "== E1-E62 audit and bounded program status =="
"$PYTHON" user_data/strategy_research/research_program_postmortem.py
"$PYTHON" user_data/strategy_research/mechanism_variant_policy.py >/dev/null
"$PYTHON" user_data/strategy_research/research_program_reset.py

date -u +'%Y-%m-%dT%H:%M:%SZ' >"$STATE_DIR/last_completed_utc.txt"
echo "E62 background collection cycle completed. Outcomes remain unread."
