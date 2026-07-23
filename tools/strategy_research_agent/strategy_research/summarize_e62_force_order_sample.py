#!/usr/bin/env python3
"""Audit frozen E62 sample readiness without reading price outcomes."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from summarize_e61_force_order_inventory import (
    EXPECTED_PAIRS,
    RECEIPT_ROOT,
    REPORT_ROOT,
    REPO_ROOT,
    audit_receipt,
    event_key,
    relative,
)


RESEARCH_ROOT = REPO_ROOT / "user_data/strategy_research"
PREREG_ROOT = RESEARCH_ROOT / "preregistrations"
PAIR_COOLDOWN = timedelta(minutes=15)
SOURCE_PATHS = {
    "e61_collector_sha256": RESEARCH_ROOT / "collect_e61_market_force_orders.py",
    "e61_inventory_auditor_sha256": (
        RESEARCH_ROOT / "summarize_e61_force_order_inventory.py"
    ),
    "cost_model_sha256": (
        REPO_ROOT
        / "tools/strategy_research_agent/strategy_research/cost_model.py"
    ),
    "regime_manifest_sha256": (
        RESEARCH_ROOT / "regime_windows/latest_regime_windows.json"
    ),
}


def utc_tag() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def load_preregistration() -> tuple[Path, dict[str, Any]]:
    paths = sorted(
        PREREG_ROOT.glob(
            "e62_direct_force_order_exhaustion_continuation_v1_*.json"
        )
    )
    if not paths:
        raise FileNotFoundError("E62 frozen preregistration is missing")
    path = paths[-1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != "E62":
        raise ValueError(f"unexpected experiment id in {path}")
    if payload.get("status") != "frozen_before_prospective_events":
        raise ValueError(f"E62 preregistration is not frozen: {path}")
    return path, payload


def audit_source_hashes(prereg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected = prereg["source_hashes"]
    results: dict[str, dict[str, Any]] = {}
    for key, path in SOURCE_PATHS.items():
        observed = sha256(path) if path.exists() else None
        results[key] = {
            "path": relative(path) if path.exists() else str(path),
            "exists": path.exists(),
            "expected": expected.get(key),
            "observed": observed,
            "passed": observed is not None and observed == expected.get(key),
        }
    return results


def decluster(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anchors: dict[str, datetime] = {}
    independent: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda value: (value["event_time_ms"], event_key(value))):
        event_time = datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC)
        previous = anchors.get(row["symbol"])
        if previous is not None and event_time < previous + PAIR_COOLDOWN:
            continue
        anchors[row["symbol"]] = event_time
        independent.append(row)
    return independent


def count_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    event_times = [
        datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC) for row in rows
    ]
    side_counts = Counter(row["liquidated_position_side"] for row in rows)
    pair_side_counts = Counter(
        (row["symbol"], row["liquidated_position_side"]) for row in rows
    )
    dates = sorted({value.strftime("%Y-%m-%d") for value in event_times})
    hours = sorted({value.strftime("%Y-%m-%dT%H:00Z") for value in event_times})
    return {
        "events": len(rows),
        "long_liquidation_events": side_counts["long"],
        "short_liquidation_events": side_counts["short"],
        "pairs_per_liquidation_side": {
            side: sum(pair_side_counts[(pair, side)] > 0 for pair in EXPECTED_PAIRS)
            for side in ["long", "short"]
        },
        "pair_side_counts": {
            pair: {
                side: pair_side_counts[(pair, side)] for side in ["long", "short"]
            }
            for pair in EXPECTED_PAIRS
        },
        "independent_utc_dates": len(dates),
        "utc_dates": dates,
        "observed_utc_hours": len(hours),
        "utc_hours": hours,
        "first_event_utc": min(event_times).isoformat() if event_times else None,
        "last_event_utc": max(event_times).isoformat() if event_times else None,
    }


def evaluate_count_gates(
    counts: dict[str, Any], contract: dict[str, Any]
) -> dict[str, bool]:
    pairs = counts["pairs_per_liquidation_side"]
    return {
        "independent_events": counts["events"]
        >= int(contract["independent_events_min"]),
        "long_liquidation_events": counts["long_liquidation_events"]
        >= int(contract["long_liquidation_events_min"]),
        "short_liquidation_events": counts["short_liquidation_events"]
        >= int(contract["short_liquidation_events_min"]),
        "pairs_per_liquidation_side": min(pairs.values(), default=0)
        >= int(contract["pairs_observed_per_liquidation_side_min"]),
        "independent_utc_dates": counts["independent_utc_dates"]
        >= int(contract["independent_utc_dates_min"]),
        "observed_utc_hours": counts["observed_utc_hours"]
        >= int(contract["observed_utc_hours_min"]),
    }


def completed_prefix(
    rows: list[dict[str, Any]], contract: dict[str, Any]
) -> tuple[str | None, dict[str, Any] | None, dict[str, bool] | None]:
    today = datetime.now(UTC).date()
    completed_dates = sorted(
        {
            datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC).date()
            for row in rows
            if datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC).date()
            < today
        }
    )
    for end_date in completed_dates:
        prefix = [
            row
            for row in rows
            if datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC).date()
            <= end_date
        ]
        counts = count_snapshot(prefix)
        gates = evaluate_count_gates(counts, contract)
        if all(gates.values()):
            return end_date.isoformat(), counts, gates
    return None, None, None


def build_report() -> tuple[Path, Path]:
    prereg_path, prereg = load_preregistration()
    cutoff = parse_utc(prereg["freeze_cutoff_utc_exclusive"])
    source_hash_checks = audit_source_hashes(prereg)

    receipt_paths = sorted(RECEIPT_ROOT.glob("**/*.receipt.json"))
    receipt_audits: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for path in receipt_paths:
        audit, rows = audit_receipt(path)
        receipt_audits.append(audit)
        all_rows.extend(rows)

    post_freeze_rows = [
        row
        for row in all_rows
        if datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC) > cutoff
    ]
    raw_keys = [event_key(row) for row in post_freeze_rows]
    unique_by_key = {event_key(row): row for row in post_freeze_rows}
    unique_rows = list(unique_by_key.values())
    independent_rows = decluster(unique_rows)
    counts = count_snapshot(independent_rows)
    development = prereg["sample_contract"]["development_prefix"]
    count_gates = evaluate_count_gates(counts, development)
    prefix_end, prefix_counts, prefix_count_gates = completed_prefix(
        independent_rows, development
    )

    all_receipts_pass = bool(receipt_audits) and all(
        audit["passed"] for audit in receipt_audits
    )
    source_hashes_pass = all(
        check["passed"] for check in source_hash_checks.values()
    )
    duplicates = len(raw_keys) - len(unique_by_key)
    blockers: list[str] = []
    if not all_receipts_pass:
        blockers.append("one_or_more_immutable_receipts_failed")
    if not source_hashes_pass:
        blockers.append("frozen_source_hash_mismatch")
    if duplicates:
        blockers.append("duplicate_post_freeze_event_keys_detected")
    blockers.extend(
        f"development_count_gate_not_met:{name}"
        for name, passed in count_gates.items()
        if not passed
    )
    if prefix_end is None:
        blockers.append("no_completed_utc_day_meets_all_blind_count_gates")
    blockers.extend(
        [
            "data_derived_regime_episode_gate_unresolved",
            "causal_3m_price_coverage_not_checked",
        ]
    )

    generated = utc_tag()
    payload = {
        "generated_at_utc": generated,
        "experiment_id": "E62",
        "status": "prospective_sample_readiness_audited_without_outcomes",
        "research_only": True,
        "preregistration": relative(prereg_path),
        "preregistration_status": prereg["status"],
        "freeze_cutoff_utc_exclusive": prereg["freeze_cutoff_utc_exclusive"],
        "receipt_count": len(receipt_audits),
        "all_receipts_pass": all_receipts_pass,
        "source_hash_checks": source_hash_checks,
        "source_hashes_pass": source_hashes_pass,
        "raw_post_freeze_events": len(post_freeze_rows),
        "unique_post_freeze_events": len(unique_rows),
        "duplicate_post_freeze_event_keys": duplicates,
        "declustering": prereg["event_contract"]["declustering"],
        "independent_event_counts": counts,
        "development_count_gates": count_gates,
        "earliest_completed_count_ready_prefix": {
            "end_utc_date": prefix_end,
            "counts": prefix_counts,
            "count_gates": prefix_count_gates,
        },
        "unresolved_blind_gates": {
            "data_derived_regime_episodes": (
                "The current regime manifest persists selected windows but not the "
                "causal daily-label series needed to count post-freeze episodes. "
                "This gate remains unresolved rather than inferred."
            ),
            "causal_3m_price_coverage": (
                "Not inspected at sample-acquisition stage; no OHLCV or return data "
                "was opened by this auditor."
            ),
        },
        "receipts": receipt_audits,
        "blockers": blockers,
        "decision": "continue_blind_collection" if blockers else "sample_ready",
        "outcomes_read": False,
        "strategy_synthesis_allowed": False,
        "registry_allowed": False,
        "dryrun_permission": False,
    }

    json_path = REPORT_ROOT / f"e62_force_order_sample_readiness_{generated}.json"
    md_path = REPORT_ROOT / f"e62_force_order_sample_readiness_{generated}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pair_rows = counts["pair_side_counts"]
    lines = [
        "# E62 Prospective Sample Readiness",
        "",
        f"- Frozen cutoff, exclusive: `{prereg['freeze_cutoff_utc_exclusive']}`",
        f"- Immutable receipts audited: `{len(receipt_audits)}`",
        f"- All receipts passed: `{all_receipts_pass}`",
        f"- Frozen source hashes passed: `{source_hashes_pass}`",
        f"- Raw post-freeze events: `{len(post_freeze_rows)}`",
        f"- Unique post-freeze events: `{len(unique_rows)}`",
        f"- Independent 15m pair episodes: `{counts['events']}`",
        f"- UTC dates / hours: `{counts['independent_utc_dates']}` / "
        f"`{counts['observed_utc_hours']}`",
        "- Outcomes read: `False`",
        f"- Decision: `{payload['decision']}`",
        "",
        "## Independent Pair and Side Coverage",
        "",
        "| pair | long liquidation | short liquidation |",
        "|---|---:|---:|",
        *[
            f"| `{pair}` | {pair_rows[pair]['long']} | "
            f"{pair_rows[pair]['short']} |"
            for pair in EXPECTED_PAIRS
        ],
        "",
        "## Frozen Development Count Gates",
        "",
        *[
            f"- `{name}`: `{'PASS' if passed else 'BLOCKED'}`"
            for name, passed in count_gates.items()
        ],
        "",
        "## Blockers",
        "",
        *([f"- `{value}`" for value in blockers] if blockers else ["- None."]),
        "",
        "The data-derived regime-episode and causal 3m coverage gates remain "
        "explicitly unresolved. This audit did not open OHLCV, calculate returns, "
        "choose a direction, or synthesize a strategy.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    shutil.copy2(json_path, REPORT_ROOT / "latest_e62_force_order_sample_readiness.json")
    shutil.copy2(md_path, REPORT_ROOT / "latest_e62_force_order_sample_readiness.md")
    return json_path, md_path


def main() -> int:
    json_path, md_path = build_report()
    print(relative(json_path))
    print(relative(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
