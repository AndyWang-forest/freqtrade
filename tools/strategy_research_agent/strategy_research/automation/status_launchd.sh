#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" || ! -d "$ROOT/.git" || ! -d "$ROOT/user_data/strategy_research" ]]; then
  echo "Could not locate freqtrade repo root from $SCRIPT_DIR" >&2
  exit 2
fi
AUTOMATION_DIR="$ROOT/user_data/strategy_research/automation"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLISTS=("$AUTOMATION_DIR"/com.wangsen.freqtrade.strategy-research.*.plist)
CURRENT_LOGS=()

for plist in "${PLISTS[@]}"; do
  [[ -f "$plist" ]] || continue
  label="$(basename "$plist" .plist)"
  echo "== $label =="
  if launchctl print "gui/$(id -u)/$label" >/tmp/freqtrade-strategy-research-launchd-status.txt 2>&1; then
    rg "state =|last exit code|program =|path =" /tmp/freqtrade-strategy-research-launchd-status.txt || true
  else
    echo "not installed"
  fi
  installed_plist="$LAUNCH_AGENTS_DIR/$label.plist"
  if [[ -f "$installed_plist" ]]; then
    for key in StandardOutPath StandardErrorPath; do
      log_path="$(plutil -extract "$key" raw "$installed_plist" 2>/dev/null || true)"
      if [[ -n "$log_path" ]]; then
        CURRENT_LOGS+=("$log_path")
      fi
    done
  fi
  echo
done

echo "== recent logs =="
if (( ${#CURRENT_LOGS[@]} == 0 )); then
  echo "none (no installed Agent plist exposes a log path)"
else
  for log in "${CURRENT_LOGS[@]}"; do
    [[ -e "$log" ]] || continue
    echo "-- $log --"
    tail -20 "$log"
  done
fi
