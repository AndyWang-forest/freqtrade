#!/usr/bin/env python3
"""Publish the bounded research program after the E1-E62 audit."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
POSTMORTEM = AGENT_ROOT / "postmortems/latest_research_program_postmortem.json"
E62_READINESS = AGENT_ROOT / "event_studies/latest_e62_force_order_sample_readiness.json"
OUTPUT_DIR = AGENT_ROOT / "program_reset"
LATEST_JSON = OUTPUT_DIR / "latest_research_program_reset.json"
LATEST_MD = OUTPUT_DIR / "latest_research_program_reset.md"

E62_COUNT_TARGETS = {
    "independent_events": 80,
    "long_liquidation_events": 20,
    "short_liquidation_events": 20,
    "pairs_per_liquidation_side": 3,
    "independent_utc_dates": 3,
    "observed_utc_hours": 12,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def e62_counts(payload: dict[str, Any]) -> dict[str, int]:
    counts = payload.get("independent_event_counts") or {}
    pairs = counts.get("pairs_per_liquidation_side") or {}
    return {
        "independent_events": int(counts.get("events") or 0),
        "long_liquidation_events": int(counts.get("long_liquidation_events") or 0),
        "short_liquidation_events": int(counts.get("short_liquidation_events") or 0),
        "pairs_per_liquidation_side": min(
            int(pairs.get("long") or 0), int(pairs.get("short") or 0)
        ),
        "independent_utc_dates": int(counts.get("independent_utc_dates") or 0),
        "observed_utc_hours": int(counts.get("observed_utc_hours") or 0),
    }


def build_payload() -> dict[str, Any]:
    postmortem = load_json(POSTMORTEM)
    if postmortem.get("scope") != "E1-E62":
        raise ValueError("E1-E62 postmortem must be rebuilt before the program reset")
    readiness = load_json(E62_READINESS)
    counts = e62_counts(readiness)
    count_gaps = {
        name: max(0, target - counts.get(name, 0))
        for name, target in E62_COUNT_TARGETS.items()
    }
    count_ready = bool(readiness) and all(gap == 0 for gap in count_gaps.values())
    outcomes_read = readiness.get("outcomes_read") is True
    if outcomes_read and not count_ready:
        raise ValueError("E62 outcomes were read before the frozen count gate completed")
    return {
        "generated_at_utc": now_utc(),
        "schema_version": 1,
        "research_only": True,
        "program_scope": "E1-E62",
        "program_assessment": {
            "new_validated_strategy_output": False,
            "verdict": "strategy_output_failure_requires_research_reset",
            "interpretation": "Many completed rounds produced useful negative evidence and retained assets, but did not produce a newly validated strategy. More adjacent OHLCV threshold variants are not authorized.",
            "experiment_buckets": (postmortem.get("summary") or {}).get(
                "program_buckets", {}
            ),
        },
        "e62_background_acquisition": {
            "occupies_active_strategy_research": False,
            "outcomes_read": outcomes_read,
            "current_counts": counts,
            "count_targets": E62_COUNT_TARGETS,
            "count_gaps": count_gaps,
            "count_gate_ready": count_ready,
            "decision": (
                "advance_to_frozen_causal_outcome_build"
                if count_ready
                else "continue_cross_day_blind_background_collection"
            ),
            "additional_frozen_gates": [
                "at_least_two_data_derived_regime_episodes",
                "causal_3m_price_coverage_through_60m",
                "locked_validation_reserve_after_development_prefix",
            ],
        },
        "active_research_axis": {
            "name": "liquidation_context_causal_response",
            "inputs": [
                "direct_binance_force_order",
                "open_interest",
                "funding_rate",
                "perpetual_basis_or_mark_dislocation",
                "causal_next_3m_price_response",
            ],
            "purpose": "Separate liquidation continuation from exhaustion using observable positioning context rather than another OHLCV threshold grid.",
            "strategy_code_allowed_now": False,
        },
        "strategy_synthesis_contract": {
            "positive_new_event_study_required": True,
            "positive_independent_home_regime_windows_required": 2,
            "realistic_cost_must_remain_positive": True,
            "causal_freqtrade_runtime_compatibility_required": True,
            "max_structural_variants_per_unchanged_mechanism": 3,
            "three_failed_variants_quarantine_mechanism": True,
            "adjacent_ohlcv_threshold_tuning_authorized": False,
        },
        "operational_separation": {
            "registry_and_router_continue_unchanged": True,
            "deployment_permission_is_not_strategy_generation_authority": True,
            "background_data_acquisition_is_not_an_active_experiment_slot": True,
        },
        "source_artifacts": {
            "postmortem": rel(POSTMORTEM),
            "e62_readiness": rel(E62_READINESS) if readiness else None,
        },
    }


def write_outputs(payload: dict[str, Any]) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    e62 = payload["e62_background_acquisition"]
    assessment = payload["program_assessment"]
    lines = [
        "# Research Program Reset",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Scope: `{payload['program_scope']}`",
        f"- Verdict: `{assessment['verdict']}`",
        "- New validated strategy output: `False`",
        "- Adjacent OHLCV threshold tuning: `blocked`",
        "- Registry/router operation: `unchanged`",
        "",
        "## E62 Background Acquisition",
        "",
        f"- Active strategy-research slot: `{e62['occupies_active_strategy_research']}`",
        f"- Count gate ready: `{e62['count_gate_ready']}`",
        f"- Decision: `{e62['decision']}`",
        "",
        "| Gate | Current | Target | Gap |",
        "|---|---:|---:|---:|",
    ]
    for name, target in e62["count_targets"].items():
        lines.append(
            f"| `{name}` | {e62['current_counts'][name]} | {target} | {e62['count_gaps'][name]} |"
        )
    lines.extend(
        [
            "",
            "## Active Evidence Axis",
            "",
            "`force-order + OI + funding + basis/mark dislocation + causal next-3m response`",
            "",
            "No strategy class may be generated until a new family-factor composite event is net-positive under realistic costs in two independent home-regime windows and has a causal Freqtrade runtime path. One unchanged mechanism gets at most three structural variants; three failures quarantine it.",
            "",
        ]
    )
    LATEST_MD.write_text("\n".join(lines), encoding="utf-8")
    return LATEST_JSON, LATEST_MD


def main() -> int:
    payload = build_payload()
    json_path, md_path = write_outputs(payload)
    print(rel(json_path))
    print(rel(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
