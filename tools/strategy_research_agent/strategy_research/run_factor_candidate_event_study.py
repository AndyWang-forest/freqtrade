#!/usr/bin/env python3
"""Promote factor rows into explicit event definitions, never strategy code.

This is the bridge between typed factor discovery and strategy synthesis.  It
reads only the indexed current-target factor report, records gross edge before
costs, requires independent regime episodes, and emits event hypotheses that a
later Freqtrade implementation must still validate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from factor_research import (
    _annotate_windows,
    _feature_frames,
    add_forward_labels,
    decluster_sample,
    regime_window_protocol,
    side_score,
)
from factor_research_protocol import (
    FACTOR_EVENT_METHOD_VERSION,
    FAMILY_COMPOSITE_REQUIRED,
    GROSS_FACTOR_COMPOSITION_ALLOWED,
)
from regime_window_builder import regime_entry_mask
from repo_paths import find_repo_root
from research_artifact_index import current_target_report, publish_report
from research_target import (
    ResearchTarget,
    load_current_research_target,
    target_mismatch_reason,
)
from run_event_study import EVENT_FAMILIES, add_indicators, event_masks


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
FACTOR_INDEX = AGENT_ROOT / "factors/factor_research_index.json"
OUTPUT_DIR = AGENT_ROOT / "event_studies"
LATEST_JSON = OUTPUT_DIR / "latest_factor_candidate_event_study.json"
LATEST_MD = OUTPUT_DIR / "latest_factor_candidate_event_study.md"
INDEX_JSON = OUTPUT_DIR / "factor_candidate_event_study_index.json"

NATIVE_STRATEGY_DATA_REQUIREMENTS = {
    "ohlcv",
    "ohlcv_proxy",
    "fee_slippage_model",
    "ohlcv_research_all",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_factor_report(target: ResearchTarget) -> tuple[Path, dict[str, Any]]:
    if not FACTOR_INDEX.exists():
        raise FileNotFoundError(f"Missing factor report index: {rel(FACTOR_INDEX)}")
    path = current_target_report(FACTOR_INDEX)
    if not path.is_absolute():
        path = REPO_ROOT / path
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("regime_label"):
        raise ValueError("Current factor report is not regime-targeted.")
    if (payload.get("research_target") or {}).get("action") != "research":
        raise ValueError("Current factor report was not authorized by the router.")
    mismatch = target_mismatch_reason(payload.get("research_target") or {}, target)
    if mismatch:
        raise ValueError(f"Current factor report is stale or mis-scoped: {mismatch}")
    if payload.get("regime_label") != target.regime_label:
        raise ValueError("Current factor report regime does not match the allocator target.")
    return path, payload


def execution_compatibility(data_requirement: str | None) -> dict[str, Any]:
    compatible = data_requirement in NATIVE_STRATEGY_DATA_REQUIREMENTS
    return {
        "compatible": compatible,
        "data_requirement": data_requirement,
        "reason": (
            "available from completed OHLCV/informative-pair data inside Freqtrade"
            if compatible
            else "requires an explicit causal runtime data adapter before Freqtrade strategy synthesis"
        ),
    }


def compatible_structural_events(family_codes: list[str], side: str) -> list[str]:
    target = set(family_codes)
    return sorted(
        event
        for event, families in EVENT_FAMILIES.items()
        if target & families and ("short" if event.endswith("_short") else "long") == side
    )


def factor_mask(frame: pd.DataFrame, definition: dict[str, Any]) -> pd.Series:
    column = definition.get("factor_column") or definition["factor"]
    threshold = definition.get("quantile_threshold")
    if column not in frame.columns or threshold is None:
        return pd.Series(False, index=frame.index)
    if definition["tail"] == "high":
        return frame[column] >= float(threshold)
    return frame[column] <= float(threshold)


def factor_can_enter_composition(event: dict[str, Any]) -> bool:
    """Allow gross-positive context into composition without standalone authority."""
    return (
        GROSS_FACTOR_COMPOSITION_ALLOWED
        and (event.get("gross_edge") or {}).get("gate") == "pass"
    )


def supporting_factor_event(
    item: dict[str, Any], index: int, factor: dict[str, Any]
) -> dict[str, Any]:
    definition = {
        "factor": item["factor"],
        "factor_column": item.get("factor_column") or item["factor"],
        "domain": item["domain"],
        "data_requirement": item.get("data_requirement"),
        "tail": item["tail"],
        "quantile_threshold": item.get("quantile_threshold"),
        "quantile_threshold_source": item.get("quantile_threshold_source"),
        "quantile_calibration_sample": item.get("quantile_calibration_sample"),
        "development_window": item.get("development_window"),
        "validation_windows": item.get("validation_windows") or [],
        "horizon_bars": item["horizon_bars"],
        "causality": "factor value is available on the completed entry candle; auxiliary records are strictly prior",
    }
    return {
        "event_id": f"supporting_factor_event_{index:03d}",
        "event_kind": "supporting_factor",
        "status": (
            "supporting_factor_edge"
            if item.get("verdict") == "edge_candidate"
            else item.get("verdict")
        ),
        "pair": item["pair"],
        "timeframe": item["timeframe"],
        "side": item["side"],
        "strategy_family_codes": list(factor.get("target_family_codes") or []),
        "regime_label": factor["regime_label"],
        "event_definition": definition,
        "knowledge_cards": item.get("knowledge_cards") or [],
        "gross_edge": {
            "gate": item["gross_gate"],
            "raw_events": item["raw_sample"],
            "independent_events": item["independent_sample"],
            "mean_return_pct": item["mean_forward_return_pct"],
            "win_rate": item["gross_win_rate"],
            "mfe_mae_ratio": item["mfe_mae_ratio"],
        },
        "realistic_cost": {
            "gate": item["cost_gate"],
            "mean_after_fee_pct": item["mean_after_fee_pct"],
            "win_rate": item["win_rate"],
        },
        "independent_window_evidence": item.get("window_evidence") or [],
        "execution_compatibility": execution_compatibility(item.get("data_requirement")),
        "strategy_generation_allowed": False,
        "next_gate": "combine with a predeclared family structural event, freeze the factor threshold, and repeat independent-window validation",
    }


def composite_events(
    supporting: dict[str, Any],
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    definition = supporting["event_definition"]
    timeframe = supporting["timeframe"]
    horizon = int(definition["horizon_bars"])
    regime_label = supporting["regime_label"]
    protocol = regime_window_protocol(regime_label)
    prepared = add_indicators(frame, timeframe)
    prepared = add_forward_labels(prepared, horizon)
    prepared = _annotate_windows(prepared, regime_label, horizon, timeframe)
    structures = event_masks(prepared)
    regime_mask = regime_entry_mask(prepared, regime_label, horizon, timeframe)
    prepared = prepared.loc[regime_mask].copy()
    frozen_factor = factor_mask(prepared, definition).fillna(False)
    compatibility = supporting["execution_compatibility"]
    rows: list[dict[str, Any]] = []
    for event_name in compatible_structural_events(
        supporting["strategy_family_codes"], supporting["side"]
    ):
        structure = structures[event_name].reindex(prepared.index).fillna(False)
        raw = prepared.loc[structure & frozen_factor].dropna(
            subset=["forward_return", "long_mfe", "long_mae", "short_mfe", "short_mae"]
        )
        sample = decluster_sample(raw, horizon)
        score = side_score(
            sample,
            supporting["side"],
            raw_sample=len(raw),
            regime_label=regime_label,
            window_roles=protocol["window_roles"],
        )
        statistical_pass = score["verdict"] == "edge_candidate"
        final = statistical_pass and compatibility["compatible"]
        if final:
            status = "family_composite_edge_candidate"
        elif statistical_pass:
            status = "execution_incompatible"
        else:
            status = score["verdict"]
        family_codes = sorted(
            set(supporting["strategy_family_codes"]) & EVENT_FAMILIES[event_name]
        )
        rows.append(
            {
                "event_id": f"{supporting['event_id']}__{event_name}",
                "source_factor_event_id": supporting["event_id"],
                "event_kind": "family_factor_composite",
                "status": status,
                "pair": supporting["pair"],
                "timeframe": timeframe,
                "side": supporting["side"],
                "strategy_family_codes": family_codes,
                "regime_label": regime_label,
                "event_definition": {
                    **definition,
                    "structural_event": event_name,
                    "composition": "predeclared family structure AND frozen factor condition on the completed candle",
                },
                "knowledge_cards": supporting["knowledge_cards"],
                "gross_edge": {
                    "gate": score["gross_gate"],
                    "raw_events": score["raw_sample"],
                    "independent_events": score["independent_sample"],
                    "mean_return_pct": score["mean_forward_return_pct"],
                    "win_rate": score["gross_win_rate"],
                    "mfe_mae_ratio": score["mfe_mae_ratio"],
                },
                "realistic_cost": {
                    "gate": score["cost_gate"],
                    "mean_after_fee_pct": score["mean_after_fee_pct"],
                    "win_rate": score["win_rate"],
                },
                "independent_window_evidence": score["window_evidence"],
                "execution_compatibility": compatibility,
                "strategy_generation_allowed": final,
                "next_gate": (
                    "translate the unchanged composite event into one explainable strategy hypothesis, then run full Freqtrade validation"
                    if final
                    else "do not generate strategy code; redesign the causal event or implement and validate the required runtime data adapter"
                ),
            }
        )
    return rows


def build_payload() -> dict[str, Any]:
    target = load_current_research_target()
    if target.action == "no_trade":
        return {
            "generated_at_utc": now_utc(),
            "research_only": True,
            "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION,
            "family_composite_required": FAMILY_COMPOSITE_REQUIRED,
            "pair_scope": "allocator_target",
            "regime_label": None,
            "research_target": target.as_dict(),
            "factor_report": None,
            "gate_sequence": [
                "allocator_target_available",
                "development_window_threshold_freeze",
                "gross_edge",
                "realistic_cost",
                "independent_regime_windows",
                "family_structural_composition",
                "execution_compatibility",
            ],
            "events": [],
            "supporting_factor_events": [],
            "composite_events": [],
            "validated_events": [],
            "summary": {
                "gross_factor_candidates": 0,
                "supporting_factor_candidates": 0,
                "composite_events": 0,
                "composite_statistical_candidates": 0,
                "validated_events": 0,
                "verdict": "blocked_by_allocator_no_research_target",
            },
            "blocked_reason": target.reason,
        }
    factor_path, factor = load_factor_report(target)
    supporting_events: list[dict[str, Any]] = []
    for index, item in enumerate(factor.get("gross_candidates") or [], start=1):
        supporting_events.append(supporting_factor_event(item, index, factor))

    frames: dict[tuple[str, str], pd.DataFrame] = {}
    composites: list[dict[str, Any]] = []
    for item in supporting_events:
        if not factor_can_enter_composition(item):
            continue
        key = (item["pair"], item["timeframe"])
        if key not in frames:
            loaded, _ = _feature_frames([item["pair"]], item["timeframe"])
            if item["pair"] not in loaded:
                continue
            frames[key] = loaded[item["pair"]]
        composites.extend(composite_events(item, frames[key]))

    events = supporting_events + composites
    validated = [item for item in composites if item["strategy_generation_allowed"]]
    verdict = "event_candidates_ready" if validated else "no_validated_factor_event"
    return {
        "generated_at_utc": now_utc(),
        "research_only": True,
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION,
        "family_composite_required": FAMILY_COMPOSITE_REQUIRED,
        "pair_scope": factor["pair_scope"],
        "regime_label": factor["regime_label"],
        "research_target": factor.get("research_target"),
        "factor_report": rel(factor_path),
        "gate_sequence": [
            "development_window_threshold_freeze",
            "gross_edge",
            "realistic_cost",
            "independent_regime_windows",
            "family_structural_composition",
            "execution_compatibility",
        ],
        "events": events,
        "supporting_factor_events": supporting_events,
        "composite_events": composites,
        "validated_events": validated,
        "summary": {
            "gross_factor_candidates": len(supporting_events),
            "supporting_factor_candidates": sum(
                item["status"] == "supporting_factor_edge" for item in supporting_events
            ),
            "composite_events": len(composites),
            "composite_statistical_candidates": sum(
                item["status"]
                in {"family_composite_edge_candidate", "execution_incompatible"}
                for item in composites
            ),
            "validated_events": len(validated),
            "verdict": verdict,
        },
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Factor Candidate Event Study",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Factor report: `{payload['factor_report']}`",
        f"- Regime: `{payload['regime_label']}`",
        f"- Verdict: `{payload['summary']['verdict']}`",
        "",
        "| Event | Kind | Pair | TF | Structure + Factor | Side | Independent | Gross % | Net % | Windows | Runtime | Status | Strategy code? |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for item in payload["events"]:
        definition = item["event_definition"]
        gross = item["gross_edge"]
        cost = item["realistic_cost"]
        lines.append(
            f"| {item['event_id']} | {item['event_kind']} | {item['pair']} | {item['timeframe']} | "
            f"{definition.get('structural_event', 'supporting-only')} + {definition['domain']}/{definition['factor']}:{definition['tail']} | "
            f"{item['side']} | {gross['independent_events']} | {gross['mean_return_pct']} | "
            f"{cost['mean_after_fee_pct']} | {len(item['independent_window_evidence'])} | "
            f"{'ready' if item['execution_compatibility']['compatible'] else 'adapter required'} | {item['status']} | "
            f"{'yes, hypothesis only' if item['strategy_generation_allowed'] else 'no'} |"
        )
    if not payload["events"]:
        lines.append("| none | - | - | - | - | - | 0 | 0 | 0 | 0 | - | blocked | no |")
    lines.extend(
        [
            "",
            "## Contract",
            "",
            "- Gross edge is inspected before costs; a cheap-looking net result cannot hide absent signal edge.",
            "- Overlapping candles are de-clustered into independent episodes.",
            "- Factor thresholds are frozen on the earliest home episode; later episodes are validation only.",
            "- A gross-positive factor may enter structural composition even when its isolated cost or replication gate fails; it still cannot authorize strategy synthesis by itself.",
            "- Strategy synthesis requires a predeclared family structure AND the frozen factor condition to pass the same independent-window gates.",
            "- Auxiliary factors also require an explicit causal Freqtrade runtime adapter before code generation.",
            "- Only `family_composite_edge_candidate` rows may enter the strategy-hypothesis plan.",
            "- This artifact never modifies strategy registry, dry-run, live config, or fixed futures risk.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    stamp = payload["generated_at_utc"]
    json_path = OUTPUT_DIR / f"factor_candidate_event_study_{stamp}.json"
    md_path = OUTPUT_DIR / f"factor_candidate_event_study_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(md_path, payload)
    publish_report(
        payload=payload,
        json_path=json_path,
        md_path=md_path,
        index_path=INDEX_JSON,
        artifact_type="factor_candidate_event_study",
        generic_latest_json=LATEST_JSON,
        generic_latest_md=LATEST_MD,
        publish_current=True,
    )
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(payload["summary"]["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
