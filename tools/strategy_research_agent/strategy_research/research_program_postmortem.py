#!/usr/bin/env python3
"""Build a conservative E1-E41 research-program postmortem.

The report separates unavailable data from failed edge, preserves retained
research assets, and measures family/evidence saturation.  It never generates
strategy code or changes registry, dry-run, or live configuration.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from family_exit_risk_contract import canonical_family_id
from repo_paths import find_repo_root
from strategy_taxonomy import STRATEGY_TAXONOMY, classify_strategy_family


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
EVENT_DIR = AGENT_ROOT / "event_studies"
OUTPUT_DIR = AGENT_ROOT / "postmortems"
LATEST_JSON = OUTPUT_DIR / "latest_research_program_postmortem.json"
LATEST_MD = OUTPUT_DIR / "latest_research_program_postmortem.md"
REGISTRY_JSON = AGENT_ROOT / "strategy_registry.json"
FACTOR_JSON = AGENT_ROOT / "factors/latest_factor_research.json"
E1_REPORT = AGENT_ROOT / "reports/latest_e1_sol15m_q20_next_confirm_strategy_experiment.md"

EXPERIMENT_RANGE = range(1, 42)
EDGE_FAILURES = {
    "gross_fail",
    "cost_killed",
    "validation_reversal",
    "robustness_fail",
    "execution_incompatible",
}
NON_EDGE_BLOCKERS = {"data_blocked", "sample_or_causality"}

FAMILY_CODE_ALIASES = {
    "A1": "downtrend_failed_bounce_short",
    "A2": "uptrend_failed_pullback_long",
    "B": "range_upper_reversion_short",
    "B1": "range_upper_reversion_short",
    "B2": "range_lower_reversion_long",
    "C1": "downtrend_pullback_short",
    "C2": "uptrend_pullback_long",
    "D1": "downside_breakout_continuation_short",
    "D2": "upside_breakout_continuation_long",
    "E": "volatility_compression_directional_expansion",
    "F": "defense_no_trade",
}


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
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def scalars(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                yield from scalars(item)
            else:
                yield str(key), item
    elif isinstance(value, list):
        for item in value:
            yield from scalars(item)


def compact_text(payload: dict[str, Any]) -> str:
    values = [f"{key}={value}" for key, value in scalars(payload)]
    return " ".join(values).lower()


def generated_key(path: Path, payload: dict[str, Any]) -> tuple[str, float, str]:
    generated = str(
        payload.get("generated_at_utc")
        or payload.get("generated_utc")
        or payload.get("created_at_utc")
        or ""
    )
    return generated, path.stat().st_mtime, path.name


def experiment_artifacts(number: int) -> list[Path]:
    pattern = re.compile(rf"(?:latest_)?e{number}(?:_|$)", re.IGNORECASE)
    matches = [path for path in EVENT_DIR.glob("*.json") if pattern.match(path.name)]
    pointers = [path for path in matches if path.name.lower().startswith("latest_")]
    return sorted(pointers or matches)


def choose_artifact(number: int) -> tuple[Path | None, dict[str, Any], list[Path]]:
    paths = experiment_artifacts(number)
    loaded = [(path, load_json(path)) for path in paths]
    loaded = [(path, payload) for path, payload in loaded if payload]
    if not loaded:
        return None, {}, paths
    path, payload = max(loaded, key=lambda item: generated_key(item[0], item[1]))
    return path, payload, paths


def normalize_family(value: Any, payload: dict[str, Any]) -> str:
    raw = canonical_family_id(str(value or "").strip())
    raw = FAMILY_CODE_ALIASES.get(raw.upper(), raw)
    if raw in STRATEGY_TAXONOMY:
        return raw
    inferred = classify_strategy_family(
        payload.get("experiment"),
        payload.get("candidate"),
        payload.get("event_contract"),
        payload.get("hypothesis"),
    )
    return inferred if inferred in STRATEGY_TAXONOMY else "defense_no_trade"


def infer_family(payload: dict[str, Any]) -> str:
    return normalize_family(
        payload.get("strategy_family") or payload.get("family"),
        payload,
    )


def mechanism_cluster(payload: dict[str, Any]) -> str:
    text = compact_text(payload)
    clusters = (
        ("options_surface", ("option", "skew", "risk_reversal", "rr25", "bvol")),
        ("l1_order_book", ("bookticker", "l1", "replenish", "order_book", "pressure_persistence")),
        ("liquidation_flow", ("force_order", "liquidation")),
        ("spot_perp_flow", ("spot_led", "spot_perp", "taker_handoff")),
        ("derivatives_positioning", ("funding", "open_interest", "oi_", "top_trader", "basis", "mark_dislocation")),
        ("cross_asset_synchronization", ("cross_sectional", "leader", "laggard", "synchronization", "breadth")),
        ("session_liquidity", ("session", "calendar_cohort")),
        ("volatility_term_structure", ("term_inflection", "volatility_term")),
        ("compression_price_action", ("compression", "q20", "mother_bar", "breakout", "breakdown", "boundary_retest", "expansion")),
    )
    for name, tokens in clusters:
        if any(token in text for token in tokens):
            return name
    return "other_explainable_mechanism"


def data_source_cluster(payload: dict[str, Any], mechanism: str) -> str:
    text = compact_text(payload)
    if any(token in text for token in ("bookticker", "l1", "order_book")):
        return "binance_l1"
    if any(token in text for token in ("force_order", "liquidation")):
        return "binance_force_order"
    if any(token in text for token in ("option", "skew", "risk_reversal", "bvol")):
        return "options_volatility_aux"
    if any(token in text for token in ("funding", "open_interest", "top_trader", "basis", "mark_price")):
        return "binance_derivatives_aux"
    if "spot" in text and "perp" in text:
        return "binance_spot_perp_aux"
    if mechanism in {"compression_price_action", "cross_asset_synchronization", "session_liquidity"}:
        return "futures_ohlcv"
    return "mixed_or_unspecified"


def bool_at(payload: dict[str, Any], *path: str) -> bool | None:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value if isinstance(value, bool) else None


def numeric_values(payload: dict[str, Any], keys: set[str]) -> list[float]:
    values: list[float] = []
    for key, value in scalars(payload):
        if key not in keys or isinstance(value, bool):
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            pass
    return values


def stage_for(payload: dict[str, Any], number: int, text: str) -> str:
    if number in {1, 33}:
        return "risk_gate"
    phase = str(payload.get("phase") or "").lower()
    if any(token in text for token in ("collection", "collector_ready", "event_pending")):
        return "source"
    if "validation" in phase or "locked_validation" in phase or "validation" in text:
        return "locked_validation"
    if bool_at(payload, "sample_gate", "passed") is False and payload.get("outcomes_read") is False:
        return "sample"
    if "execution" in text or "single_slot" in text:
        return "freqtrade_alignment"
    if "gross_average_margin_positive" in text or "gross_edge" in text:
        return "gross_edge"
    if "realistic_total_positive" in text or "cost" in text:
        return "realistic_cost"
    return "event_study"


def outcome_for(payload: dict[str, Any], number: int) -> tuple[str, str]:
    text = compact_text(payload)
    decision = str(payload.get("decision") or payload.get("status") or "")
    sample_pass = bool_at(payload, "sample_gate", "passed")
    gate_pass = bool_at(payload, "gate", "passed")
    outcome_pass = bool_at(payload, "outcome_gate", "passed")
    synthesis = payload.get("strategy_synthesis_allowed") is True
    outcomes_read = payload.get("outcomes_read")

    if number == 1:
        return "risk_gate", "retained E1 high-vol research candidate; activation remains router-gated"
    if number == 33:
        return "risk_gate", "frozen E33 breakthrough asset; strategy synthesis passed but promotion risk gate remains"
    if number == 23:
        return "data_blocked", "prospective sample pending; outcomes remain unread"
    if number == 32:
        return "execution_incompatible", "development edge retained for future replication; single-slot pair coverage was incomplete"
    if any(token in text for token in ("blocked_by_public", "data_unavailable", "coverage", "continue_collection", "event_pending", "sample_pending")) and outcomes_read is not True:
        return "data_blocked", decision or "required causal data/sample is not yet available"
    if any(token in text for token in ("causality_gate", "blind_sample")) and outcomes_read is not True:
        return "sample_or_causality", decision or "blind sample or causality gate failed before outcomes"
    if sample_pass is False and outcomes_read is not True:
        return "sample_or_causality", decision or "sample gate failed before outcomes"
    if any(token in text for token in ("single_slot", "execution_incompatible", "atomic")) and not synthesis:
        return "execution_incompatible", decision or "event evidence did not map safely to executable trades"
    if synthesis or gate_pass is True or outcome_pass is True:
        return "validated_or_near_success", decision or "development/event gate passed"

    phase = str(payload.get("phase") or "").lower()
    if "validation" in phase or "locked_validation" in phase or "validation" in decision.lower():
        return "validation_reversal", decision or "locked/held-out validation failed"

    gross_values = numeric_values(payload, {"gross_account_pct", "gross_total_pct", "gross_avg_margin_pct", "gross_average_margin_pct"})
    realistic_values = numeric_values(payload, {"realistic_total_pct", "total_account_pct"})
    if "gross_average_margin_positive" in text or (gross_values and max(gross_values) <= 0):
        return "gross_fail", decision or "gross expectancy was non-positive"
    if realistic_values and max(realistic_values) <= 0 and (not gross_values or max(gross_values) > 0):
        return "cost_killed", decision or "positive/unknown gross evidence did not survive realistic costs"
    if gate_pass is False or outcome_pass is False or "reject" in decision.lower() or "close_" in decision.lower():
        return "robustness_fail", decision or "event failed robustness or outcome gate"
    return "unknown", decision or "no machine-readable final outcome"


def e1_record(registry: dict[str, Any]) -> dict[str, Any]:
    strategies = registry.get("strategies") or []
    strategy = next((item for item in strategies if str(item.get("name", "")).startswith("E1")), {})
    evidence = strategy.get("evidence") or {}
    return {
        "experiment_id": "E1",
        "experiment_number": 1,
        "title": strategy.get("name") or "E1 compression expansion baseline",
        "strategy_family": "volatility_compression_directional_expansion",
        "family_code": "E",
        "stage": "risk_gate",
        "outcome": "risk_gate",
        "detail": evidence.get("decision") or "retained high-vol-only research candidate",
        "lifecycle": "retained_research_candidate",
        "outcomes_read": True,
        "mechanism_cluster": "compression_price_action",
        "data_source_cluster": "futures_ohlcv",
        "primary_artifact": rel(REGISTRY_JSON),
        "supporting_artifacts": [rel(E1_REPORT)] if E1_REPORT.exists() else [],
        "artifact_count": 1,
    }


def record_for(number: int, registry: dict[str, Any]) -> dict[str, Any]:
    if number == 1:
        return e1_record(registry)
    path, payload, supporting = choose_artifact(number)
    if path is None:
        return {
            "experiment_id": f"E{number}",
            "experiment_number": number,
            "title": "missing local artifact",
            "strategy_family": "defense_no_trade",
            "family_code": "F",
            "stage": "source",
            "outcome": "data_blocked",
            "detail": "No E-numbered JSON artifact was found; do not infer an edge result.",
            "lifecycle": "missing_evidence",
            "outcomes_read": False,
            "mechanism_cluster": "unknown",
            "data_source_cluster": "unknown",
            "primary_artifact": None,
            "supporting_artifacts": [],
            "artifact_count": 0,
        }
    family = infer_family(payload)
    family_code = str(STRATEGY_TAXONOMY.get(family, {}).get("code") or "F")
    text = compact_text(payload)
    outcome, detail = outcome_for(payload, number)
    lifecycle = "closed"
    if number == 33:
        lifecycle = "frozen_research_asset"
    elif number in {23, 32}:
        lifecycle = "wait_for_new_prospective_data"
    elif outcome == "validated_or_near_success":
        lifecycle = "development_pass_needs_next_gate"
    elif outcome in NON_EDGE_BLOCKERS:
        lifecycle = "blocked_without_edge_verdict"
    mechanism = mechanism_cluster(payload)
    return {
        "experiment_id": f"E{number}",
        "experiment_number": number,
        "title": payload.get("experiment") or payload.get("experiment_id") or path.stem,
        "strategy_family": family,
        "family_code": family_code,
        "stage": stage_for(payload, number, text),
        "outcome": outcome,
        "detail": detail,
        "lifecycle": lifecycle,
        "outcomes_read": payload.get("outcomes_read"),
        "mechanism_cluster": mechanism,
        "data_source_cluster": data_source_cluster(payload, mechanism),
        "primary_artifact": rel(path),
        "supporting_artifacts": [rel(item) for item in supporting if item != path],
        "artifact_count": len(supporting),
    }


def add_evidence_novelty(records: list[dict[str, Any]]) -> None:
    seen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    streaks: dict[str, tuple[tuple[str, str] | None, int]] = {}
    for record in records:
        family = record["strategy_family"]
        evidence_key = (record["mechanism_cluster"], record["data_source_cluster"])
        record["new_evidence_generation"] = evidence_key not in seen[family]
        seen[family].add(evidence_key)
        previous_key, previous_count = streaks.get(family, (None, 0))
        if record["outcome"] in EDGE_FAILURES:
            count = previous_count + 1 if previous_key == evidence_key else 1
            streaks[family] = (evidence_key, count)
            record["same_evidence_failure_streak"] = count
        elif record["outcome"] in NON_EDGE_BLOCKERS:
            record["same_evidence_failure_streak"] = previous_count if previous_key == evidence_key else 0
        else:
            streaks[family] = (None, 0)
            record["same_evidence_failure_streak"] = 0


def factor_screen_summary() -> dict[str, Any]:
    payload = load_json(FACTOR_JSON)
    evaluations = payload.get("evaluations") or []
    return {
        "artifact": rel(FACTOR_JSON),
        "evaluations": len(evaluations) or int((payload.get("summary") or {}).get("evaluations") or 0),
        "unique_factors": len({str(item.get("factor")) for item in evaluations if item.get("factor")}),
        "gross_candidates": int((payload.get("summary") or {}).get("gross_candidates") or 0),
        "edge_candidates": int((payload.get("summary") or {}).get("edge_candidates") or 0),
        "verdict": (payload.get("summary") or {}).get("verdict"),
    }


def family_summaries(records: list[dict[str, Any]], registry: dict[str, Any]) -> list[dict[str, Any]]:
    registry_counts = Counter(
        canonical_family_id(item.get("family"))
        for item in registry.get("strategies") or []
        if item.get("family")
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["strategy_family"]].append(record)
    rows: list[dict[str, Any]] = []
    for family, taxonomy in STRATEGY_TAXONOMY.items():
        items = grouped.get(family, [])
        max_streak = max((int(item.get("same_evidence_failure_streak") or 0) for item in items), default=0)
        edge_failures = sum(item["outcome"] in EDGE_FAILURES for item in items)
        retained = [item["experiment_id"] for item in items if item["lifecycle"] in {"retained_research_candidate", "frozen_research_asset"}]
        suspended = max_streak >= 3
        concentration_saturated = len(items) >= 12 and bool(retained)
        rows.append(
            {
                "strategy_family": family,
                "family_code": taxonomy["code"],
                "direction": taxonomy["direction"],
                "experiments": len(items),
                "edge_failures": edge_failures,
                "non_edge_blockers": sum(item["outcome"] in NON_EDGE_BLOCKERS for item in items),
                "retained_assets": retained,
                "registry_strategies": registry_counts.get(family, 0),
                "max_same_evidence_failure_streak": max_streak,
                "suspended_same_evidence": suspended,
                "portfolio_coverage_saturated": concentration_saturated,
                "saturation_reason": (
                    "three consecutive edge-readable failures reused the same mechanism and data source"
                    if suspended
                    else (
                        "family already has a retained asset and dominates the experiment budget"
                        if concentration_saturated
                        else "not saturated"
                    )
                ),
            }
        )
    return rows


def build_payload() -> dict[str, Any]:
    registry = load_json(REGISTRY_JSON)
    records = [record_for(number, registry) for number in EXPERIMENT_RANGE]
    add_evidence_novelty(records)
    family_rows = family_summaries(records, registry)
    outcome_counts = Counter(item["outcome"] for item in records)
    stage_counts = Counter(item["stage"] for item in records)
    retained_assets = [
        {
            "experiment_id": item["experiment_id"],
            "lifecycle": item["lifecycle"],
            "strategy_family": item["strategy_family"],
            "detail": item["detail"],
        }
        for item in records
        if item["lifecycle"] in {"retained_research_candidate", "frozen_research_asset", "wait_for_new_prospective_data"}
    ]
    return {
        "generated_at_utc": now_utc(),
        "schema_version": 1,
        "research_only": True,
        "scope": "E1-E41",
        "policy": {
            "data_blocked_is_not_edge_failure": True,
            "prospective_outcomes_remain_unread_until_sample_gate": True,
            "same_evidence_failure_limit": 3,
            "portfolio_concentration_is_deprioritization_not_edge_rejection": True,
        },
        "summary": {
            "expected_experiments": 41,
            "indexed_experiments": sum(item["artifact_count"] > 0 for item in records),
            "missing_experiments": [item["experiment_id"] for item in records if item["artifact_count"] == 0],
            "outcome_counts": dict(sorted(outcome_counts.items())),
            "stage_counts": dict(sorted(stage_counts.items())),
        },
        "factor_screen": factor_screen_summary(),
        "retained_or_waiting_assets": retained_assets,
        "family_matrix": family_rows,
        "experiments": records,
        "decision": {
            "generate_new_strategy_now": False,
            "freeze_assets": ["E1", "E33"],
            "wait_for_new_data": ["E23", "E32"],
            "next_step": "Run the independent research-family allocator; do not select the next family from current deployment state alone.",
        },
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    factor = payload["factor_screen"]
    lines = [
        "# E1-E41 Research Program Postmortem",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Indexed experiments: `{summary['indexed_experiments']}/41`",
        f"- Missing experiment artifacts: `{', '.join(summary['missing_experiments']) or 'none'}`",
        f"- Current factor screen: `{factor['evaluations']}` evaluations / `{factor['unique_factors']}` independent factor names / `{factor['edge_candidates']}` final edge candidates.",
        "- Data/sample blockers are not counted as failed edge.",
        "- This report does not authorize strategy synthesis, dry-run, registry, or live changes.",
        "",
        "## Retained And Waiting Assets",
        "",
        "| Experiment | Lifecycle | Family | Decision |",
        "|---|---|---|---|",
    ]
    for item in payload["retained_or_waiting_assets"]:
        lines.append(f"| {item['experiment_id']} | {item['lifecycle']} | {item['strategy_family']} | {item['detail']} |")
    lines.extend(
        [
            "",
            "## Family Matrix",
            "",
            "| Code | Family | Experiments | Edge failures | Non-edge blockers | Registry | Retained | Same-evidence streak | State |",
            "|---|---|---:|---:|---:|---:|---|---:|---|",
        ]
    )
    for item in payload["family_matrix"]:
        state = "suspended_same_evidence" if item["suspended_same_evidence"] else ("coverage_saturated" if item["portfolio_coverage_saturated"] else "open")
        lines.append(
            f"| {item['family_code']} | {item['strategy_family']} | {item['experiments']} | {item['edge_failures']} | "
            f"{item['non_edge_blockers']} | {item['registry_strategies']} | {', '.join(item['retained_assets']) or '-'} | "
            f"{item['max_same_evidence_failure_streak']} | {state} |"
        )
    lines.extend(
        [
            "",
            "## Experiment Funnel",
            "",
            "| ID | Family | Stage | Outcome | Lifecycle | Mechanism | Data | New evidence | Evidence |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in payload["experiments"]:
        evidence = f"`{item['primary_artifact']}`" if item["primary_artifact"] else "missing"
        lines.append(
            f"| {item['experiment_id']} | {item['family_code']} | {item['stage']} | {item['outcome']} | {item['lifecycle']} | "
            f"{item['mechanism_cluster']} | {item['data_source_cluster']} | {item['new_evidence_generation']} | {evidence} |"
        )
    lines.extend(
        [
            "",
            "## Program Decision",
            "",
            "Pause adjacent strategy generation. Preserve E1/E33, keep E23/E32 waiting for genuinely new prospective evidence, and let the independent research allocator choose the next under-covered family.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(payload: dict[str, Any]) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = payload["generated_at_utc"]
    json_path = OUTPUT_DIR / f"research_program_postmortem_{timestamp}.json"
    md_path = OUTPUT_DIR / f"research_program_postmortem_{timestamp}.md"
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    json_path.write_text(rendered, encoding="utf-8")
    LATEST_JSON.write_text(rendered, encoding="utf-8")
    write_markdown(md_path, payload)
    LATEST_MD.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    payload = build_payload()
    json_path, md_path = write_outputs(payload)
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print("Decision: pause adjacent strategy generation; run research allocator next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
