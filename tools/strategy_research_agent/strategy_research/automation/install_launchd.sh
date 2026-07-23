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
LOG_DIR="$ROOT/user_data/strategy_research/reports/automation"
PLISTS=("$AUTOMATION_DIR"/com.wangsen.freqtrade.strategy-research.*.plist)
RETIRED_LABELS=(
  "com.wangsen.freqtrade.strategy-research.daily"
  "com.wangsen.freqtrade.strategy-research.weekly-aux"
)

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

for label in "${RETIRED_LABELS[@]}"; do
  retired_path="$LAUNCH_AGENTS_DIR/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u)" "$retired_path" >/dev/null 2>&1 || true
  rm -f "$retired_path"
done

for source_path in "${PLISTS[@]}"; do
  [[ -f "$source_path" ]] || continue
  plist="$(basename "$source_path")"
  target_path="$LAUNCH_AGENTS_DIR/$plist"
  cp "$source_path" "$target_path"
  source_program="$(plutil -extract ProgramArguments.0 raw "$target_path")"
  program_suffix="${source_program#*/user_data/strategy_research/}"
  program_path="$ROOT/user_data/strategy_research/$program_suffix"
  job_root="$ROOT"
  job_log_dir="$LOG_DIR"
  if [[ "${plist%.plist}" == "com.wangsen.freqtrade.strategy-research.e62-force-order" ]]; then
    stage_root="$("$ROOT/user_data/strategy_research/stage_e62_background_runtime.sh")"
    program_path="$stage_root/user_data/strategy_research/run_e62_stage_cycle.sh"
    job_root="$stage_root"
    job_log_dir="$stage_root/logs"
    mkdir -p "$job_log_dir"
  fi
  stdout_name="$(basename "$(plutil -extract StandardOutPath raw "$target_path")")"
  stderr_name="$(basename "$(plutil -extract StandardErrorPath raw "$target_path")")"
  plutil -replace ProgramArguments -json "[\"$program_path\"]" "$target_path"
  plutil -replace WorkingDirectory -string "$job_root" "$target_path"
  plutil -replace StandardOutPath -string "$job_log_dir/$stdout_name" "$target_path"
  plutil -replace StandardErrorPath -string "$job_log_dir/$stderr_name" "$target_path"
  plutil -lint "$target_path" >/dev/null
  launchctl bootout "gui/$(id -u)" "$target_path" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$target_path"
  launchctl enable "gui/$(id -u)/${plist%.plist}"
  echo "Installed ${plist%.plist}"
done

echo "Strategy research launchd jobs installed."
echo "Check status with: $AUTOMATION_DIR/status_launchd.sh"
