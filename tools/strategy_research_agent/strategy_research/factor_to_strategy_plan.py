#!/usr/bin/env python3
"""Convert factor research evidence into guarded strategy hypotheses."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from factor_research_protocol import FACTOR_EVENT_METHOD_VERSION
from repo_paths import find_repo_root
from research_artifact_index import current_target_report
from research_target import load_current_research_target, target_mismatch_reason


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
EVENT_INDEX = AGENT_ROOT / "event_studies/factor_candidate_event_study_index.json"
OUTPUT_DIR = AGENT_ROOT / "factors"
LATEST_JSON = OUTPUT_DIR / "latest_factor_strategy_plan.json"
LATEST_MD = OUTPUT_DIR / "latest_factor_strategy_plan.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_current_event_report() -> tuple[Path | None, dict[str, Any]]:
    if not EVENT_INDEX.exists():
        return None, {}
    try:
        path = current_target_report(EVENT_INDEX)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, {}
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path, load_json(path)


def validate_event_report(
    event_report: dict[str, Any], target: Any
) -> str | None:
    if not event_report:
        return "No indexed current-target factor event report exists."
    if event_report.get("factor_event_method_version") != FACTOR_EVENT_METHOD_VERSION:
        return "Factor event report uses an obsolete method version."
    mismatch = target_mismatch_reason(
        event_report.get("research_target") or {}, target
    )
    if mismatch:
        return mismatch
    if (event_report.get("regime_label") or None) != target.regime_label:
        return "Factor event report regime does not match the current allocator."
    allowed_families = set(target.family_codes)
    for item in event_report.get("validated_events") or []:
        event_families = set(item.get("strategy_family_codes") or [])
        if not event_families or not event_families.issubset(allowed_families):
            return "Validated event contains a missing or wrong-family assignment."
    return None


def build_payload() -> dict[str, Any]:
    target = load_current_research_target()
    event_path, event_report = load_current_event_report()
    blocked_reason = validate_event_report(event_report, target)
    regime_label = target.regime_label
    candidates = [] if blocked_reason else event_report.get("validated_events", [])
    hypotheses = []
    for index, item in enumerate(candidates, start=1):
        definition = item["event_definition"]
        gross = item["gross_edge"]
        cost = item["realistic_cost"]
        hypotheses.append(
            {
                "hypothesis_id": f"factor_edge_{index:03d}",
                "status": "ready_for_explainable_strategy_hypothesis",
                "pair": item["pair"],
                "timeframe": item["timeframe"],
                "side": item["side"],
                "factor": definition["factor"],
                "factor_domain": definition["domain"],
                "event_definition": definition,
                "source": "validated_family_factor_composite_event",
                "source_event_id": item["event_id"],
                "strategy_family_codes": item.get("strategy_family_codes") or [],
                "knowledge_cards": item.get("knowledge_cards") or [],
                "regime_label": regime_label,
                "regime_windows": [row["window"] for row in item.get("independent_window_evidence") or []],
                "evidence": {
                    "sample": gross["independent_events"],
                    "mean_gross_pct": gross["mean_return_pct"],
                    "mean_after_fee_pct": cost["mean_after_fee_pct"],
                    "gross_win_rate": gross["win_rate"],
                    "win_rate": cost["win_rate"],
                    "mfe_mae_ratio": gross["mfe_mae_ratio"],
                },
                "strategy_generation_gate": "Generate one explainable hypothesis only. Preserve both the family structural event and frozen factor condition; concrete code still requires unchanged causal logic and full Freqtrade validation.",
            }
        )
    return {
        "generated_at_utc": now_utc(),
        "research_only": True,
        "factor_candidate_event_report": rel(event_path) if event_path else None,
        "factor_candidate_event_generated_at_utc": event_report.get("generated_at_utc"),
        "research_target": target.as_dict(),
        "regime_label": regime_label,
        "hypotheses": hypotheses,
        "summary": {
            "factor_candidates": len(candidates),
            "strategy_hypotheses": len(hypotheses),
            "verdict": (
                "blocked_stale_or_wrong_target_event"
                if blocked_reason
                else "ready_for_explainable_strategy_hypothesis"
                if hypotheses
                else "no_validated_event_to_synthesize"
            ),
        },
        "blocked_reason": (
            blocked_reason
            if blocked_reason
            else None
            if hypotheses
            else "No family-factor composite passed structural, gross-edge, realistic-cost, independent-window, and runtime-compatibility gates."
        ),
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Factor-To-Strategy Plan",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Factor candidate event report: `{payload.get('factor_candidate_event_report')}`",
        f"- Verdict: `{payload['summary']['verdict']}`",
        f"- Regime label: `{payload.get('regime_label') or 'all'}`",
        "",
        "## Hypotheses",
        "",
        "| ID | Families | Pair | TF | Domain/Factor | Side | Sample | Gross % | Mean After Fee % | Win Rate | Status |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for item in payload["hypotheses"]:
        evidence = item["evidence"]
        lines.append(
            "| {hypothesis_id} | {families} | {pair} | {timeframe} | {factor_domain}/{factor} | {side} | {sample} | {mean_gross_pct} | {mean_after_fee_pct} | {win_rate} | {status} |".format(
                families=",".join(item["strategy_family_codes"]),
                sample=evidence["sample"],
                mean_gross_pct=evidence["mean_gross_pct"],
                mean_after_fee_pct=evidence["mean_after_fee_pct"],
                win_rate=evidence["win_rate"],
                **item,
            )
        )
    if not payload["hypotheses"]:
        lines.append("| none | - | - | - | - | - | 0 | 0 | 0 | 0 | blocked |")
    lines.extend(
        [
            "",
            "## Contract",
            "",
            "- This is a Strategy Agent sub-flow, not a separate Agent.",
            "- The input is the indexed current allocator-target factor-event report, never an all-history latest pointer.",
            "- Passing factor rows remained supporting evidence until a predeclared family structure plus frozen factor condition passed again.",
            "- If blocked, redesign factors, improve data, or run negative controls instead of generating another class from theory.",
        ]
    )
    if payload.get("blocked_reason"):
        lines.extend(["", f"Blocked reason: {payload['blocked_reason']}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    timestamp = payload["generated_at_utc"]
    json_path = OUTPUT_DIR / f"factor_strategy_plan_{timestamp}.json"
    md_path = OUTPUT_DIR / f"factor_strategy_plan_{timestamp}.md"
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    json_path.write_text(text, encoding="utf-8")
    LATEST_JSON.write_text(text, encoding="utf-8")
    write_markdown(md_path, payload)
    LATEST_MD.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Wrote {rel(LATEST_JSON)}")
    print(f"Wrote {rel(LATEST_MD)}")


if __name__ == "__main__":
    main()
