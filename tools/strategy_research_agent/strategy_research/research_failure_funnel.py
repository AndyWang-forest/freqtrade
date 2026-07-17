#!/usr/bin/env python3
"""Summarize the current research blockers without recycling stale strategies."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
OUTPUT_DIR = AGENT_ROOT / "failure_funnel"
LATEST_JSON = OUTPUT_DIR / "latest_research_failure_funnel.json"
LATEST_MD = OUTPUT_DIR / "latest_research_failure_funnel.md"

CATEGORIES = (
    "gross_fail",
    "cost_killed",
    "validation_reversal",
    "data_blocked",
    "execution_incompatible",
    "gate_semantic_block",
)

CURRENT_FACTOR = AGENT_ROOT / "factors/latest_factor_research.json"
CURRENT_FACTOR_EVENT = AGENT_ROOT / "event_studies/latest_factor_candidate_event_study.json"
CURRENT_ROUTER = AGENT_ROOT / "reports/latest_current_market_state_family_router.json"
CURRENT_ALLOCATION = AGENT_ROOT / "research_allocation/latest_research_family_allocator.json"
CURRENT_FAMILY_GATE = AGENT_ROOT / "family_risk_gate/latest_family_risk_gate.json"

MONITORED_STUDIES = {
    "B9": AGENT_ROOT / "event_studies/latest_b9_oi_buildup_failed_auction_reversal_validation.json",
    "B14": AGENT_ROOT / "event_studies/latest_b14_cross_sectional_funding_dispersion_pair_validation.json",
    "E23": AGENT_ROOT / "event_studies/latest_e23_prospective_shadow_monitor.json",
    "E35": AGENT_ROOT / "event_studies/latest_e35_e32_l1_flow_replication.json",
    "E36": AGENT_ROOT / "event_studies/latest_e36_prospective_l1_collection.json",
    "E37": AGENT_ROOT / "event_studies/latest_e37_l1_rotation_restart_integrity.json",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def entry(
    experiment: str,
    category: str,
    detail: str,
    source: Path,
    *,
    secondary_categories: list[str] | None = None,
    outcomes_read: bool | None = None,
) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise ValueError(f"Unsupported failure category: {category}")
    return {
        "experiment": experiment,
        "primary_category": category,
        "secondary_categories": secondary_categories or [],
        "detail": detail,
        "source": rel(source),
        "outcomes_read": outcomes_read,
    }


def classify_current_factor() -> list[dict[str, Any]]:
    factor = load_json(CURRENT_FACTOR)
    event = load_json(CURRENT_FACTOR_EVENT)
    rows: list[dict[str, Any]] = []
    summary = factor.get("summary", {})
    if factor and int(summary.get("gross_candidates") or 0) == 0:
        rows.append(
            entry(
                "CURRENT_FACTOR_DISCOVERY",
                "gross_fail",
                f"{summary.get('evaluations', 0)} evaluations produced no gross-edge candidate for the current allocator target.",
                CURRENT_FACTOR,
            )
        )
    event_summary = event.get("summary", {})
    if event and int(event_summary.get("validated_events") or 0) == 0:
        if int(event_summary.get("composite_statistical_candidates") or 0) > 0:
            category = "execution_incompatible"
            detail = "A family-factor composite retained statistical edge but lacks a validated causal Freqtrade runtime data path."
        elif int(event_summary.get("supporting_factor_candidates") or 0) > 0:
            category = "gross_fail"
            detail = "Supporting factor edge did not survive predeclared family-structure composition and independent-window validation."
        else:
            category = "gross_fail"
            detail = "No current factor candidate survived gross-edge and independent-event validation."
        rows.append(
            entry(
                "CURRENT_FACTOR_EVENT_GATE",
                category,
                detail,
                CURRENT_FACTOR_EVENT,
            )
        )
    return rows


def classify_monitored_studies() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for experiment, path in MONITORED_STUDIES.items():
        payload = load_json(path)
        if not payload:
            continue
        if experiment == "B9":
            rows.append(
                entry(
                    experiment,
                    "validation_reversal",
                    "Development edge did not replicate in the locked validation reserve.",
                    path,
                    outcomes_read=True,
                )
            )
        elif experiment == "B14":
            summary = payload.get("summary", {})
            rows.append(
                entry(
                    experiment,
                    "validation_reversal",
                    "The locked validation direction reversed and failed gross/realistic gates.",
                    path,
                    secondary_categories=["execution_incompatible"]
                    if summary.get("freqtrade_execution_compatible") is False
                    else [],
                    outcomes_read=True,
                )
            )
        elif experiment in {"E23", "E35", "E36", "E37"}:
            decision = payload.get("decision") or payload.get("status") or "sample_or_data_gate_pending"
            rows.append(
                entry(
                    experiment,
                    "data_blocked",
                    str(decision),
                    path,
                    outcomes_read=bool(payload.get("outcomes_read", False)),
                )
            )
    return rows


def classify_active_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for directory in (AGENT_ROOT / "candidates", AGENT_ROOT / "watchlist"):
        for path in sorted(directory.glob("*.json")):
            payload = load_json(path)
            if not payload:
                continue
            reasons = [str(value) for value in payload.get("reasons", [])]
            text = " ".join(reasons + [str(payload.get("risk_notes") or "")]).lower()
            categories: list[str] = []
            if any(token in text for token in ("stress_cost", "cost_not_robust", "cost-killed")):
                categories.append("cost_killed")
            if any(token in text for token in ("semantic", "permanent-pause", "runtime semantics")):
                categories.append("gate_semantic_block")
            if any(token in text for token in ("execution_incompatible", "single-slot", "atomic")):
                categories.append("execution_incompatible")
            if not categories:
                continue
            rows.append(
                entry(
                    str(payload.get("strategy") or path.stem),
                    categories[0],
                    "; ".join(reasons) or str(payload.get("risk_notes") or "candidate blocked"),
                    path,
                    secondary_categories=categories[1:],
                )
            )
    return rows


def build_payload(previous: dict[str, Any] | None = None) -> dict[str, Any]:
    router = load_json(CURRENT_ROUTER)
    allocation = load_json(CURRENT_ALLOCATION)
    factor = load_json(CURRENT_FACTOR)
    factor_event = load_json(CURRENT_FACTOR_EVENT)
    allocation_target = allocation.get("research_allocation") or {}
    if allocation_target.get("action") == "research":
        allocation_target = {
            "current_state": (allocation.get("deployment_permission") or {}).get("current_state"),
            "action": "research",
            "regime_label": allocation_target.get("regime_label"),
            "family_codes": [allocation_target.get("family_code")],
            "strategy_families": [allocation_target.get("selected_family")],
            "allowed_sides": allocation_target.get("allowed_sides") or [],
            "selection_source": "research_family_allocator",
        }
    else:
        allocation_target = {}
    factor_target = factor.get("research_target") or {}
    target = allocation_target or factor_target or router.get("deployment_target") or {}
    factor_signature = (
        factor_target.get("regime_label"),
        tuple(sorted(str(item) for item in factor_target.get("family_codes") or [])),
        tuple(sorted(str(item) for item in factor_target.get("allowed_sides") or [])),
    )
    target_signature = (
        target.get("regime_label"),
        tuple(sorted(str(item) for item in target.get("family_codes") or [])),
        tuple(sorted(str(item) for item in target.get("allowed_sides") or [])),
    )
    factor_target_stale = bool(allocation_target) and factor_signature != target_signature
    if factor_target_stale:
        current_factor_entries = [
            entry(
                "CURRENT_FACTOR_TARGET_MISMATCH",
                "data_blocked",
                "Latest factor/event artifacts target a previous allocation and must be rerun before they can diagnose the selected family.",
                CURRENT_FACTOR,
            )
        ]
    else:
        current_factor_entries = classify_current_factor()
    entries = current_factor_entries + classify_monitored_studies() + classify_active_candidates()
    counts = Counter(item["primary_category"] for item in entries)
    for item in entries:
        counts.update(item.get("secondary_categories", []))

    validated_events = 0 if factor_target_stale else int(factor_event.get("summary", {}).get("validated_events") or 0)
    synthesis_allowed = target.get("action") == "research" and validated_events > 0
    current_entries = [
        item for item in entries if str(item.get("experiment", "")).startswith("CURRENT_")
    ]
    current_counts = Counter(item["primary_category"] for item in current_entries)
    for item in current_entries:
        current_counts.update(item.get("secondary_categories", []))
    fingerprint_source = {
        "current_state": target.get("current_state"),
        "family_codes": target.get("family_codes", []),
        "allowed_sides": target.get("allowed_sides", []),
        "factor_verdict": factor.get("summary", {}).get("verdict"),
        "factor_event_verdict": factor_event.get("summary", {}).get("verdict"),
        "validated_factor_events": validated_events,
        "factor_target_stale": factor_target_stale,
        "current_categories": dict(sorted(current_counts.items())),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_source, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    previous_fingerprint = (previous or {}).get("current_target_decision", {}).get("blocker_fingerprint")
    blocker_changed = previous_fingerprint is not None and previous_fingerprint != fingerprint
    adjacent_allowed = synthesis_allowed and blocker_changed

    if not synthesis_allowed:
        decision = "stop_adjacent_variant_generation"
        next_action = (
            "Accumulate new causal evidence or required data for the allocator-selected family; "
            "do not generate another adjacent filter from the unchanged zero-gross-edge blocker."
        )
    elif not blocker_changed:
        decision = "hold_strategy_synthesis_until_blocker_changes"
        next_action = "Preserve the validated event and change the diagnosed blocker before synthesizing a neighboring variant."
    else:
        decision = "strategy_synthesis_may_resume"
        next_action = "Generate only strategies linked to the current validated factor event."

    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "schema_version": 1,
        "research_only": True,
        "categories": list(CATEGORIES),
        "entries": entries,
        "category_counts": dict(sorted(counts.items())),
        "current_target": target,
        "current_target_decision": {
            "decision": decision,
            "strategy_synthesis_allowed": synthesis_allowed,
            "adjacent_variant_generation_allowed": adjacent_allowed,
            "blocker_changed": blocker_changed,
            "blocker_fingerprint": fingerprint,
            "previous_blocker_fingerprint": previous_fingerprint,
            "validated_factor_events": validated_events,
            "factor_target_stale": factor_target_stale,
            "next_action": next_action,
        },
        "prospective_data_policy": {
            "e23_outcomes_must_remain_unread_until_sample_gate": True,
            "e32_e36_outcomes_must_remain_unread_until_sample_gate": True,
        },
        "source_artifacts": {
            "current_router": rel(CURRENT_ROUTER),
            "research_allocator": rel(CURRENT_ALLOCATION),
            "current_factor": rel(CURRENT_FACTOR),
            "current_factor_event": rel(CURRENT_FACTOR_EVENT),
            "family_risk_gate": rel(CURRENT_FAMILY_GATE),
        },
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    decision = payload["current_target_decision"]
    lines = [
        "# Research Failure Funnel",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Current decision: `{decision['decision']}`",
        f"- Strategy synthesis allowed: `{decision['strategy_synthesis_allowed']}`",
        f"- Adjacent variant generation allowed: `{decision['adjacent_variant_generation_allowed']}`",
        f"- Blocker changed: `{decision['blocker_changed']}`",
        "",
        "## Current Next Action",
        "",
        decision["next_action"],
        "",
        "## Funnel",
        "",
        "| Experiment | Primary blocker | Secondary blockers | Outcomes read | Evidence | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for item in payload["entries"]:
        lines.append(
            "| {experiment} | {primary_category} | {secondary} | {outcomes_read} | `{source}` | {detail} |".format(
                secondary=", ".join(item.get("secondary_categories", [])) or "-",
                **item,
            )
        )
    lines.extend(["", "## Category Counts", ""])
    for category in CATEGORIES:
        lines.append(f"- `{category}`: {payload['category_counts'].get(category, 0)}")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = payload["generated_at_utc"]
    json_path = OUTPUT_DIR / f"research_failure_funnel_{timestamp}.json"
    md_path = OUTPUT_DIR / f"research_failure_funnel_{timestamp}.md"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    json_path.write_text(text, encoding="utf-8")
    LATEST_JSON.write_text(text, encoding="utf-8")
    write_markdown(md_path, payload)
    LATEST_MD.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Decision: {payload['current_target_decision']['decision']}")


def main() -> None:
    previous = load_json(LATEST_JSON)
    write_outputs(build_payload(previous))


if __name__ == "__main__":
    main()
