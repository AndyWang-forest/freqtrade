#!/usr/bin/env python3
"""Audit cumulative E61 force-order segments without reading price outcomes."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = REPO_ROOT / "user_data/strategy_research"
RECEIPT_ROOT = RESEARCH_ROOT / "data_receipts/force_order_market_v2"
REPORT_ROOT = RESEARCH_ROOT / "event_studies"
EXPECTED_SCHEMA = "binance_usdtm_force_order_snapshot_v2"
EXPECTED_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def utc_tag() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def event_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["symbol"],
        row["event_time_ms"],
        row["trade_time_ms"],
        row["force_order_side"],
        row["original_qty"],
        row["accumulated_filled_qty"],
        row["average_price"],
    )


def audit_receipt(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    segment = REPO_ROOT / receipt["segment"]
    checks = {
        "segment_exists": segment.exists(),
        "segment_sha256_matches": False,
        "segment_bytes_match": False,
        "row_count_matches": False,
        "pair_counts_match": False,
        "side_counts_match": False,
        "rows_valid": False,
    }
    rows: list[dict[str, Any]] = []
    valid = True
    if segment.exists():
        checks["segment_sha256_matches"] = sha256(segment) == receipt["segment_sha256"]
        checks["segment_bytes_match"] = segment.stat().st_size == int(receipt["segment_bytes"])
        with gzip.open(segment, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    valid = valid and row["event_schema"] == EXPECTED_SCHEMA
                    valid = valid and int(row["symbol_type"]) == 1
                    valid = valid and row["symbol"] in EXPECTED_PAIRS
                    valid = valid and row["liquidated_position_side"] in {"long", "short"}
                    event_key(row)
                    rows.append(row)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    valid = False
        checks["row_count_matches"] = len(rows) == int(receipt["rows"])
        observed_pair_counts = Counter(row["symbol"] for row in rows)
        observed_side_counts = Counter(
            row["liquidated_position_side"] for row in rows
        )
        checks["pair_counts_match"] = {
            pair: observed_pair_counts[pair] for pair in EXPECTED_PAIRS
        } == receipt["pair_counts"]
        checks["side_counts_match"] = {
            side: observed_side_counts[side] for side in ["long", "short"]
        } == receipt["liquidated_side_counts"]
        checks["rows_valid"] = valid and bool(rows)
    result = {
        "receipt": relative(path),
        "receipt_sha256": sha256(path),
        "segment": receipt["segment"],
        "rows": len(rows),
        "checks": checks,
        "passed": all(checks.values()),
    }
    return result, rows


def build_report() -> tuple[Path, Path]:
    receipt_paths = sorted(RECEIPT_ROOT.glob("**/*.receipt.json"))
    audits: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for path in receipt_paths:
        audit, segment_rows = audit_receipt(path)
        audits.append(audit)
        rows.extend(segment_rows)

    keys = [event_key(row) for row in rows]
    unique_keys = set(keys)
    pair_counts = Counter(row["symbol"] for row in rows)
    side_counts = Counter(row["liquidated_position_side"] for row in rows)
    pair_side_counts = Counter(
        (row["symbol"], row["liquidated_position_side"]) for row in rows
    )
    event_times = [datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC) for row in rows]
    utc_dates = sorted({value.strftime("%Y-%m-%d") for value in event_times})
    utc_hours = sorted({value.strftime("%Y-%m-%dT%H:00Z") for value in event_times})
    all_receipts_pass = bool(audits) and all(audit["passed"] for audit in audits)

    acquisition_blockers: list[str] = []
    if not all_receipts_pass:
        acquisition_blockers.append("one_or_more_immutable_receipts_failed")
    if len(utc_dates) < 3:
        acquisition_blockers.append("fewer_than_three_independent_utc_dates")
    if len(utc_hours) < 12:
        acquisition_blockers.append("fewer_than_twelve_observed_utc_hours")
    if set(pair_counts) != set(EXPECTED_PAIRS):
        acquisition_blockers.append("not_all_fixed_pairs_observed")
    if set(side_counts) != {"long", "short"}:
        acquisition_blockers.append("not_both_liquidated_sides_observed")
    if len(keys) != len(unique_keys):
        acquisition_blockers.append("duplicate_snapshot_keys_detected")

    readiness = (
        "freeze_separate_outcome_preregistration_before_return_read"
        if not acquisition_blockers
        else "continue_unchanged_prospective_acquisition"
    )
    generated = utc_tag()
    payload = {
        "generated_at_utc": generated,
        "experiment_id": "E61",
        "status": "cumulative_force_order_inventory_audited",
        "research_only": True,
        "receipt_count": len(audits),
        "all_receipts_pass": all_receipts_pass,
        "raw_rows": len(rows),
        "unique_snapshot_keys": len(unique_keys),
        "duplicate_snapshot_keys": len(keys) - len(unique_keys),
        "first_event_utc": min(event_times).isoformat() if event_times else None,
        "last_event_utc": max(event_times).isoformat() if event_times else None,
        "utc_dates": utc_dates,
        "utc_hours": utc_hours,
        "pair_counts": {pair: pair_counts[pair] for pair in EXPECTED_PAIRS},
        "liquidated_side_counts": {
            side: side_counts[side] for side in ["long", "short"]
        },
        "pair_side_counts": {
            pair: {
                side: pair_side_counts[(pair, side)] for side in ["long", "short"]
            }
            for pair in EXPECTED_PAIRS
        },
        "receipts": audits,
        "acquisition_readiness_diagnostic": {
            "purpose": "coverage diagnostic only; not a trading promotion gate",
            "blockers": acquisition_blockers,
            "decision": readiness,
        },
        "outcomes_read": False,
        "strategy_synthesis_allowed": False,
        "registry_allowed": False,
        "dryrun_permission": False,
    }
    json_path = REPORT_ROOT / f"e61_force_order_inventory_{generated}.json"
    md_path = REPORT_ROOT / f"e61_force_order_inventory_{generated}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# E61 Cumulative Force-Order Inventory",
        "",
        f"- Immutable receipts: `{len(audits)}`",
        f"- All receipts passed: `{all_receipts_pass}`",
        f"- Raw snapshots: `{len(rows)}`",
        f"- Unique snapshots: `{len(unique_keys)}`",
        f"- UTC dates represented: `{len(utc_dates)}`",
        f"- UTC hours represented: `{len(utc_hours)}`",
        "- Outcomes read: `False`",
        f"- Decision: `{readiness}`",
        "",
        "## Pair and Side Coverage",
        "",
        "| pair | long liquidation | short liquidation | total |",
        "|---|---:|---:|---:|",
        *[
            f"| `{pair}` | {pair_side_counts[(pair, 'long')]} | "
            f"{pair_side_counts[(pair, 'short')]} | {pair_counts[pair]} |"
            for pair in EXPECTED_PAIRS
        ],
        "",
        "## Acquisition Blockers",
        "",
        *(
            [f"- `{blocker}`" for blocker in acquisition_blockers]
            if acquisition_blockers
            else ["- None at the descriptive coverage layer."]
        ),
        "",
        "This audit does not calculate post-event returns. A separate frozen outcome "
        "contract is required before any exhaustion or continuation test.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    shutil.copy2(json_path, REPORT_ROOT / "latest_e61_force_order_inventory.json")
    shutil.copy2(md_path, REPORT_ROOT / "latest_e61_force_order_inventory.md")
    return json_path, md_path


def main() -> int:
    json_path, md_path = build_report()
    print(relative(json_path))
    print(relative(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
