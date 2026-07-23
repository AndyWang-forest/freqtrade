#!/usr/bin/env bash
set -euo pipefail

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" || ! -d "$ROOT/.git" || ! -d "$ROOT/user_data/strategy_research" ]]; then
  echo "Could not locate freqtrade repo root from $SCRIPT_DIR" >&2
  exit 2
fi
AUTOMATION_DIR="$ROOT/user_data/strategy_research/automation"
PLISTS=("$AUTOMATION_DIR"/com.wangsen.freqtrade.strategy-research.*.plist)
RETIRED_LABELS=(
  "com.wangsen.freqtrade.strategy-research.daily"
  "com.wangsen.freqtrade.strategy-research.weekly-aux"
)

for source_path in "${PLISTS[@]}"; do
  [[ -f "$source_path" ]] || continue
  plist="$(basename "$source_path")"
  target_path="$LAUNCH_AGENTS_DIR/$plist"
  launchctl bootout "gui/$(id -u)" "$target_path" >/dev/null 2>&1 || true
  rm -f "$target_path"
  echo "Uninstalled ${plist%.plist}"
done

for label in "${RETIRED_LABELS[@]}"; do
  target_path="$LAUNCH_AGENTS_DIR/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u)" "$target_path" >/dev/null 2>&1 || true
  rm -f "$target_path"
done

echo "Strategy research launchd jobs uninstalled."
