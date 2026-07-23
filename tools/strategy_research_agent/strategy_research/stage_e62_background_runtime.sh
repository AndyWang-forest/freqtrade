#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" || ! -d "$ROOT/.git" || ! -d "$ROOT/user_data/strategy_research" || ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Could not locate freqtrade repo root from $SCRIPT_DIR" >&2
  exit 2
fi

STAGE_ROOT="${E62_STAGE_ROOT:-$HOME/Library/Application Support/FreqtradeStrategyResearch/e62-runtime}"
SOURCE_RESEARCH="$ROOT/user_data/strategy_research"
STAGE_RESEARCH="$STAGE_ROOT/user_data/strategy_research"
STAGE_TOOLS="$STAGE_ROOT/tools/strategy_research_agent/strategy_research"
SOURCE_PYTHON="$ROOT/.venv/bin/python"
STAGE_PYTHON="$STAGE_ROOT/.venv/bin/python"

mkdir -p \
  "$STAGE_RESEARCH/preregistrations" \
  "$STAGE_RESEARCH/regime_windows" \
  "$STAGE_ROOT/user_data/data/binance/futures_aux/force_order_market_v2" \
  "$STAGE_TOOLS" \
  "$STAGE_ROOT/logs"

for name in \
  collect_e61_market_force_orders.py \
  summarize_e61_force_order_inventory.py \
  summarize_e62_force_order_sample.py \
  run_e62_stage_cycle.sh; do
  cp "$SOURCE_RESEARCH/$name" "$STAGE_RESEARCH/$name"
done
cp "$ROOT/tools/strategy_research_agent/strategy_research/cost_model.py" \
  "$STAGE_TOOLS/cost_model.py"
cp "$SOURCE_RESEARCH/regime_windows/latest_regime_windows.json" \
  "$STAGE_RESEARCH/regime_windows/latest_regime_windows.json"

prereg_count=0
for prereg in \
  "$SOURCE_RESEARCH"/preregistrations/e61_market_stream_force_order_migration_v2_*.json \
  "$SOURCE_RESEARCH"/preregistrations/e62_direct_force_order_exhaustion_continuation_v1_*.json; do
  [[ -f "$prereg" ]] || continue
  cp "$prereg" "$STAGE_RESEARCH/preregistrations/$(basename "$prereg")"
  prereg_count=$((prereg_count + 1))
done
if (( prereg_count < 2 )); then
  echo "Frozen E61/E62 preregistrations are required before staging." >&2
  exit 2
fi

if [[ ! -x "$STAGE_PYTHON" ]]; then
  "$SOURCE_PYTHON" -m venv "$STAGE_ROOT/.venv"
fi
websockets_version="$($SOURCE_PYTHON -c 'import websockets; print(websockets.__version__)')"
if ! "$STAGE_PYTHON" -c "import websockets; assert websockets.__version__ == '$websockets_version'" 2>/dev/null; then
  "$STAGE_PYTHON" -m pip install --disable-pip-version-check --quiet \
    "websockets==$websockets_version"
fi

chmod +x "$STAGE_RESEARCH/run_e62_stage_cycle.sh"
echo "$STAGE_ROOT"
