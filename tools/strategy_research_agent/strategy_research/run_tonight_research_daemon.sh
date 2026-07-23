#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" || ! -d "$REPO_ROOT/.git" || ! -d "$REPO_ROOT/user_data/strategy_research" || ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
  echo "Could not locate freqtrade repo root from $SCRIPT_DIR" >&2
  exit 2
fi
cd "$REPO_ROOT"

DURATION_HOURS="${DURATION_HOURS:-12}"
CYCLE_MINUTES="${CYCLE_MINUTES:-45}"
MAX_CONSECUTIVE_FAILURES="${MAX_CONSECUTIVE_FAILURES:-3}"
PAIR_SCOPE="${PAIR_SCOPE:-research_all}"
START_TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$REPO_ROOT/user_data/strategy_research/daemon_runs/$START_TS"
LATEST_LINK="$REPO_ROOT/user_data/strategy_research/daemon_runs/latest"
SUMMARY="$RUN_DIR/summary.md"
STATE="$RUN_DIR/state.jsonl"
STOP_FILE="$RUN_DIR/STOP"
ARTIFACT_ROOT="$RUN_DIR/artifacts"

mkdir -p "$RUN_DIR" "$ARTIFACT_ROOT"
rm -f "$LATEST_LINK"
ln -s "$RUN_DIR" "$LATEST_LINK"

START_EPOCH="$(date +%s)"
DURATION_SECONDS="${DURATION_SECONDS:-$((DURATION_HOURS * 3600))}"
CYCLE_SECONDS="${CYCLE_SECONDS:-$((CYCLE_MINUTES * 60))}"

cat > "$SUMMARY" <<EOF
# Tonight Research Daemon

- Started UTC: \`$START_TS\`
- Duration hours: \`$DURATION_HOURS\`
- Cycle minutes: \`$CYCLE_MINUTES\`
- Pair scope: \`$PAIR_SCOPE\`
- Safety: research-only, no dry-run/live config edits, no automatic PR.
- Stop file: \`$STOP_FILE\`

## Cycles

| cycle | UTC | mode | status | note |
|---:|---|---|---|---|
EOF

log_state() {
  local cycle="$1"
  local mode="$2"
  local status="$3"
  local note="$4"
  local ts
  local safe_note
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  safe_note="${note//\"/ }"
  printf '{"ts":"%s","cycle":%s,"mode":"%s","status":"%s","note":"%s"}\n' \
    "$ts" "$cycle" "$mode" "$status" "$safe_note" >> "$STATE"
  printf '| %s | `%s` | `%s` | `%s` | %s |\n' "$cycle" "$ts" "$mode" "$status" "$note" >> "$SUMMARY"
}

run_cmd() {
  local cycle="$1"
  local name="$2"
  shift 2
  local log="$RUN_DIR/cycle_$(printf '%03d' "$cycle")_${name}.log"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] RUN $name: $*" | tee -a "$log"
  "$@" >> "$log" 2>&1
}

tracked_tree_clean() {
  git diff --quiet && git diff --cached --quiet
}

mode_for_cycle() {
  local cycle="$1"
  case $(( (cycle - 1) % 8 )) in
    0) echo "agent_brain" ;;
    1) echo "factor_research" ;;
    2) echo "event_study" ;;
    3) echo "factor_to_strategy" ;;
    4) echo "mature_researcher_queue" ;;
    5) echo "execute_mature_researcher" ;;
    6) echo "post_run_attribution" ;;
    7) echo "family_risk_gate" ;;
  esac
}

run_mode() {
  local cycle="$1"
  local mode="$2"
  case "$mode" in
    agent_brain)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --agent-brain --extra-agent-arg --pair-scope --extra-agent-arg "$PAIR_SCOPE"
      ;;
    factor_research)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --factor-research --extra-agent-arg --pair-scope --extra-agent-arg "$PAIR_SCOPE"
      ;;
    event_study)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --event-study --extra-agent-arg --pair-scope --extra-agent-arg "$PAIR_SCOPE"
      ;;
    factor_to_strategy)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --factor-to-strategy --extra-agent-arg --pair-scope --extra-agent-arg "$PAIR_SCOPE"
      ;;
    mature_researcher_queue)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --mature-researcher-queue --extra-agent-arg --pair-scope --extra-agent-arg "$PAIR_SCOPE"
      ;;
    execute_mature_researcher)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --execute-mature-researcher --extra-agent-arg --pair-scope --extra-agent-arg "$PAIR_SCOPE"
      ;;
    post_run_attribution)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --post-run-attribution
      ;;
    family_risk_gate)
      run_cmd "$cycle" "$mode" user_data/strategy_research/start_manual_research.sh --family-risk-gate
      ;;
    *)
      echo "Unknown mode: $mode" >&2
      return 2
      ;;
  esac
}

validate_pair_scope_reports() {
  local mode="$1"
  local report=""
  local label=""
  case "$mode" in
    agent_brain|factor_research|factor_to_strategy|mature_researcher_queue|execute_mature_researcher)
      report="user_data/strategy_research/factors/latest_factor_research.json"
      label="factor_research"
      ;;
    event_study)
      report="user_data/strategy_research/event_studies/latest_event_study.json"
      label="event_study"
      ;;
    *)
      return 0
      ;;
  esac
  "$REPO_ROOT/.venv/bin/python" - "$report" "$PAIR_SCOPE" "$label" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected = sys.argv[2]
label = sys.argv[3]
if not path.exists():
    raise SystemExit(f"{label} pair-scope validation failed: missing {path}")
payload = json.loads(path.read_text(encoding="utf-8"))
actual = payload.get("pair_scope")
if actual != expected:
    raise SystemExit(f"{label} pair-scope validation failed: expected {expected!r}, got {actual!r} in {path}")
PY
}

snapshot_artifacts() {
  local cycle="$1"
  local mode="$2"
  local dest="$ARTIFACT_ROOT/cycle_$(printf '%03d' "$cycle")_${mode}"
  local file
  local copied=0
  mkdir -p "$dest"
  for file in \
    user_data/strategy_research/factors/latest_* \
    user_data/strategy_research/event_studies/latest_* \
    user_data/strategy_research/family_risk_gate/latest_* \
    user_data/strategy_research/mature_researcher/latest_* \
    user_data/strategy_research/research_agendas/latest_* \
    user_data/strategy_research/research_memory/latest_* \
    user_data/strategy_research/strategy_library/latest_* \
    user_data/strategy_research/consolidation/latest_* \
    user_data/strategy_research/promotion_reports/latest_* \
    user_data/strategy_research/failure_attribution/latest_* \
    user_data/strategy_research/trade_behavior/latest_* \
    user_data/strategy_research/reports/latest_current_market_state_family_router.* \
    user_data/strategy_research/reports/agent_report_index.json \
    user_data/strategy_research/dashboard/index.html
  do
    [[ -f "$file" ]] || continue
    mkdir -p "$dest/$(dirname "$file")"
    cp "$file" "$dest/$file"
    copied="$((copied + 1))"
  done
  echo "artifacts: \`user_data/strategy_research/daemon_runs/$START_TS/artifacts/$(basename "$dest")\` ($copied files)"
}

cycle=1
consecutive_failures=0

while true; do
  now="$(date +%s)"
  elapsed="$((now - START_EPOCH))"
  if (( elapsed >= DURATION_SECONDS )); then
    log_state "$cycle" "stop" "complete" "duration reached"
    break
  fi
  if [[ -f "$STOP_FILE" ]]; then
    log_state "$cycle" "stop" "stopped" "STOP file detected"
    break
  fi
  if ! tracked_tree_clean; then
    log_state "$cycle" "preflight" "blocked" "tracked git changes detected; stopping to avoid mixing research with edits"
    break
  fi

  mode="$(mode_for_cycle "$cycle")"
  cycle_start="$(date +%s)"
  if run_cmd "$cycle" "preflight" user_data/strategy_research/start_manual_research.sh --preflight-only --extra-agent-arg --pair-scope --extra-agent-arg "$PAIR_SCOPE" \
    && run_cmd "$cycle" "router" user_data/strategy_research/start_manual_research.sh --current-market-router \
    && run_mode "$cycle" "$mode" \
    && validate_pair_scope_reports "$mode" \
    && run_cmd "$cycle" "research_memory" user_data/strategy_research/start_manual_research.sh --research-memory \
    && run_cmd "$cycle" "strategy_lineage" user_data/strategy_research/start_manual_research.sh --strategy-lineage \
    && run_cmd "$cycle" "quick" user_data/strategy_research/start_manual_research.sh --quick; then
    consecutive_failures=0
    artifact_note="$(snapshot_artifacts "$cycle" "$mode")"
    log_state "$cycle" "$mode" "ok" "cycle completed; $artifact_note"
  else
    consecutive_failures="$((consecutive_failures + 1))"
    log_state "$cycle" "$mode" "failed" "consecutive failures=$consecutive_failures"
    if (( consecutive_failures >= MAX_CONSECUTIVE_FAILURES )); then
      log_state "$cycle" "stop" "blocked" "max consecutive failures reached"
      break
    fi
  fi

  cycle="$((cycle + 1))"
  cycle_elapsed="$(( $(date +%s) - cycle_start ))"
  sleep_for="$((CYCLE_SECONDS - cycle_elapsed))"
  if (( sleep_for > 0 )); then
    sleep "$sleep_for"
  fi
done

END_TS="$(date -u +%Y%m%dT%H%M%SZ)"
{
  echo
  echo "## Closeout"
  echo
  echo "- Ended UTC: \`$END_TS\`"
  echo "- Latest report symlink: \`user_data/strategy_research/daemon_runs/latest\`"
  echo "- Artifact snapshots: \`user_data/strategy_research/daemon_runs/$START_TS/artifacts/\`"
  echo "- State JSONL: \`$STATE\`"
} >> "$SUMMARY"

cp "$SUMMARY" "$REPO_ROOT/user_data/strategy_research/daemon_runs/latest_12h_research_report.md"
