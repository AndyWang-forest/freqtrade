#!/usr/bin/env python3
"""Resolve E62 blind sample gates without opening OHLCV values or outcomes."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from build_e62_causal_regime_labels import (
    AMENDMENT_GLOB,
    RESEARCH_ROOT,
    load_preregistration,
    sha256,
    verify_amendment,
)
from summarize_e61_force_order_inventory import (
    EXPECTED_PAIRS,
    RECEIPT_ROOT,
    REPO_ROOT,
    REPORT_ROOT,
    audit_receipt,
    event_key,
    relative,
)
from summarize_e62_force_order_sample import (
    audit_source_hashes,
    count_snapshot,
    decluster,
    evaluate_count_gates,
    parse_utc,
)


LABEL_PATH = RESEARCH_ROOT / "regime_windows/latest_e62_causal_daily_regime_labels.json"
THREE_MINUTES = pd.Timedelta(minutes=3)
PAIR_TO_DATA_STEM = {
    "BTCUSDT": "BTC_USDT_USDT",
    "ETHUSDT": "ETH_USDT_USDT",
    "SOLUSDT": "SOL_USDT_USDT",
    "BNBUSDT": "BNB_USDT_USDT",
    "XRPUSDT": "XRP_USDT_USDT",
}


def utc_tag() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def first_3m_open_strictly_after(event_time_ms: int) -> pd.Timestamp:
    event = pd.Timestamp(event_time_ms, unit="ms", tz="UTC")
    return event.floor(THREE_MINUTES) + THREE_MINUTES


def build_regime_episodes(
    daily_labels: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected = sorted(
        (
            row
            for row in daily_labels
            if start_date <= parse_date(row["effective_date_utc"]) <= end_date
        ),
        key=lambda row: row["effective_date_utc"],
    )
    episodes: list[dict[str, Any]] = []
    date_to_episode: dict[str, int] = {}
    previous_date: date | None = None
    previous_state: str | None = None
    for row in selected:
        effective_date = parse_date(row["effective_date_utc"])
        state = str(row["state_key"])
        contiguous = (
            previous_date is not None
            and effective_date == previous_date + timedelta(days=1)
            and state == previous_state
        )
        if not contiguous:
            episodes.append(
                {
                    "episode_id": len(episodes) + 1,
                    "state_key": state,
                    "start_utc_date": effective_date.isoformat(),
                    "end_utc_date": effective_date.isoformat(),
                    "days": 1,
                    "independent_events": 0,
                }
            )
        else:
            episode = episodes[-1]
            episode["end_utc_date"] = effective_date.isoformat()
            episode["days"] += 1
        date_to_episode[effective_date.isoformat()] = episodes[-1]["episode_id"]
        previous_date = effective_date
        previous_state = state
    return episodes, date_to_episode


def regime_episode_gate(
    rows: list[dict[str, Any]],
    daily_labels: list[dict[str, Any]],
    minimum_events: int,
) -> dict[str, Any]:
    if not rows:
        return {
            "label_coverage_pass": False,
            "eligible_episode_count": 0,
            "episodes": [],
            "missing_event_dates": [],
        }
    event_dates = [datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC).date() for row in rows]
    episodes, date_to_episode = build_regime_episodes(
        daily_labels,
        min(event_dates),
        max(event_dates),
    )
    episode_by_id = {episode["episode_id"]: episode for episode in episodes}
    missing_dates: set[str] = set()
    for event_date in event_dates:
        key = event_date.isoformat()
        episode_id = date_to_episode.get(key)
        if episode_id is None:
            missing_dates.add(key)
            continue
        episode_by_id[episode_id]["independent_events"] += 1
    for episode in episodes:
        episode["eligible"] = episode["independent_events"] >= minimum_events
    eligible = sum(bool(episode["eligible"]) for episode in episodes)
    return {
        "label_coverage_pass": not missing_dates,
        "eligible_episode_count": eligible,
        "events_per_episode_min": minimum_events,
        "episodes": episodes,
        "missing_event_dates": sorted(missing_dates),
    }


def load_3m_dates() -> tuple[dict[str, set[int]], dict[str, dict[str, Any]]]:
    date_sets: dict[str, set[int]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    data_dir = REPO_ROOT / "user_data/data/binance/futures"
    for symbol in EXPECTED_PAIRS:
        path = data_dir / f"{PAIR_TO_DATA_STEM[symbol]}-3m-futures.feather"
        if not path.exists():
            raise FileNotFoundError(f"missing E62 3m timestamp source: {relative(path)}")
        frame = pd.read_feather(path, columns=["date"])
        dates = pd.to_datetime(frame["date"], utc=True).sort_values().drop_duplicates()
        # Feather preserves the source timestamp unit (currently milliseconds).
        # Normalize explicitly before converting to epoch millis so ms/us/ns
        # inputs all produce the same blind coverage keys.
        epoch_ms = pd.DatetimeIndex(dates).as_unit("ns").asi8 // 1_000_000
        date_sets[symbol] = set(epoch_ms.tolist())
        metadata[symbol] = {
            "path": relative(path),
            "sha256": sha256(path),
            "rows": len(dates),
            "first_open_utc": dates.iloc[0].isoformat() if len(dates) else None,
            "last_open_utc": dates.iloc[-1].isoformat() if len(dates) else None,
            "columns_read": ["date"],
        }
    return date_sets, metadata


def timestamp_coverage_gate(
    rows: list[dict[str, Any]],
    date_sets: dict[str, set[int]],
) -> dict[str, Any]:
    pair_counts: dict[str, Counter[str]] = {symbol: Counter() for symbol in EXPECTED_PAIRS}
    missing_examples: list[dict[str, str]] = []
    for row in rows:
        symbol = row["symbol"]
        entry_open = first_3m_open_strictly_after(int(row["event_time_ms"]))
        diagnostic_open = entry_open + pd.Timedelta(minutes=60)
        available = date_sets[symbol]
        entry_ms = int(entry_open.timestamp() * 1000)
        diagnostic_ms = int(diagnostic_open.timestamp() * 1000)
        entry_present = entry_ms in available
        diagnostic_present = diagnostic_ms in available
        pair_counts[symbol]["events"] += 1
        pair_counts[symbol]["entry_open_present"] += int(entry_present)
        pair_counts[symbol]["plus_60m_open_present"] += int(diagnostic_present)
        if (not entry_present or not diagnostic_present) and len(missing_examples) < 10:
            missing_examples.append(
                {
                    "symbol": symbol,
                    "entry_open_utc": entry_open.isoformat(),
                    "plus_60m_open_utc": diagnostic_open.isoformat(),
                    "missing": (
                        "entry_and_plus_60m"
                        if not entry_present and not diagnostic_present
                        else "entry"
                        if not entry_present
                        else "plus_60m"
                    ),
                }
            )
    complete = all(
        counts["events"] == counts["entry_open_present"]
        and counts["events"] == counts["plus_60m_open_present"]
        for counts in pair_counts.values()
    )
    return {
        "passed": complete,
        "events_checked": len(rows),
        "pair_counts": {symbol: dict(pair_counts[symbol]) for symbol in EXPECTED_PAIRS},
        "missing_examples": missing_examples,
        "ohlcv_columns_read": ["date"],
        "candle_values_read": False,
    }


def validate_label_artifact(
    payload: dict[str, Any],
    prereg_path: Path,
    prereg: dict[str, Any],
    amendment_path: Path,
) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "experiment_id": payload.get("experiment_id") == "E62",
        "research_only": payload.get("research_only") is True,
        "outcomes_unread": payload.get("outcomes_read") is False,
        "completed_days_only": payload.get("completed_utc_days_only") is True,
        "next_day_effective": int(payload.get("effective_lag_days") or 0) == 1,
        "original_preregistration": payload.get("original_preregistration_sha256")
        == sha256(prereg_path),
        "frozen_manifest": payload.get("frozen_regime_manifest_sha256")
        == prereg["source_hashes"]["regime_manifest_sha256"],
        "amendment": payload.get("amendment_sha256") == sha256(amendment_path),
    }
    source_checks: list[dict[str, Any]] = []
    for source in payload.get("data_sources") or []:
        path = REPO_ROOT / source["path"]
        observed = sha256(path) if path.exists() else None
        passed = observed == source.get("sha256")
        source_checks.append(
            {
                "path": source.get("path"),
                "expected": source.get("sha256"),
                "observed": observed,
                "passed": passed,
            }
        )
    checks["data_source_hashes"] = bool(source_checks) and all(
        row["passed"] for row in source_checks
    )
    daily_labels = payload.get("daily_labels")
    checks["daily_labels"] = isinstance(daily_labels, list) and bool(daily_labels)
    if not all(checks.values()):
        failed = [key for key, passed in checks.items() if not passed]
        raise ValueError("invalid E62 causal label artifact: " + ", ".join(failed))
    verify_amendment(amendment_path, prereg_path, prereg)
    return {"checks": checks, "data_source_hash_checks": source_checks}


def prefix_diagnostics(
    rows: list[dict[str, Any]],
    end_date: date,
    development_contract: dict[str, Any],
    daily_labels: list[dict[str, Any]],
    date_sets: dict[str, set[int]],
) -> dict[str, Any]:
    prefix = [
        row
        for row in rows
        if datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC).date() <= end_date
    ]
    counts = count_snapshot(prefix)
    count_gates = evaluate_count_gates(counts, development_contract)
    regime = regime_episode_gate(
        prefix,
        daily_labels,
        int(development_contract["events_per_regime_episode_min"]),
    )
    regime_pass = regime["label_coverage_pass"] and regime["eligible_episode_count"] >= int(
        development_contract["data_derived_regime_episodes_min"]
    )
    coverage = timestamp_coverage_gate(prefix, date_sets)
    return {
        "end_utc_date": end_date.isoformat(),
        "counts": counts,
        "count_gates": count_gates,
        "data_derived_regime_episode_gate": {
            **regime,
            "episodes_required": int(development_contract["data_derived_regime_episodes_min"]),
            "passed": regime_pass,
        },
        "causal_3m_timestamp_coverage": coverage,
        "all_blind_gates_pass": (all(count_gates.values()) and regime_pass and coverage["passed"]),
    }


def completed_prefixes(rows: list[dict[str, Any]]) -> list[date]:
    today = datetime.now(UTC).date()
    return sorted(
        {
            datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC).date()
            for row in rows
            if datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC).date() < today
        }
    )


def build_report() -> tuple[Path, Path]:
    prereg_path, prereg = load_preregistration()
    amendment_paths = sorted((RESEARCH_ROOT / "preregistrations").glob(AMENDMENT_GLOB))
    if not amendment_paths:
        raise FileNotFoundError("E62 pre-unblind protocol amendment is missing")
    amendment_path = amendment_paths[-1]
    verify_amendment(amendment_path, prereg_path, prereg)
    if not LABEL_PATH.exists():
        raise FileNotFoundError(f"missing E62 causal labels: {relative(LABEL_PATH)}")
    labels_payload = json.loads(LABEL_PATH.read_text(encoding="utf-8"))
    label_audit = validate_label_artifact(
        labels_payload,
        prereg_path,
        prereg,
        amendment_path,
    )
    source_hash_checks = audit_source_hashes(prereg)
    source_hashes_pass = all(row["passed"] for row in source_hash_checks.values())

    receipts: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for receipt_path in sorted(RECEIPT_ROOT.glob("**/*.receipt.json")):
        audit, rows = audit_receipt(receipt_path)
        receipts.append(audit)
        all_rows.extend(rows)
    cutoff = parse_utc(prereg["freeze_cutoff_utc_exclusive"])
    post_freeze = [
        row for row in all_rows if datetime.fromtimestamp(row["event_time_ms"] / 1000, UTC) > cutoff
    ]
    unique_by_key = {event_key(row): row for row in post_freeze}
    duplicates = len(post_freeze) - len(unique_by_key)
    independent = decluster(list(unique_by_key.values()))
    all_receipts_pass = bool(receipts) and all(row["passed"] for row in receipts)

    date_sets, date_metadata = load_3m_dates()
    development = prereg["sample_contract"]["development_prefix"]
    diagnostics = [
        prefix_diagnostics(
            independent,
            end_date,
            development,
            labels_payload["daily_labels"],
            date_sets,
        )
        for end_date in completed_prefixes(independent)
    ]
    earliest_ready = next(
        (row for row in diagnostics if row["all_blind_gates_pass"]),
        None,
    )
    latest_completed = diagnostics[-1] if diagnostics else None
    blockers: list[str] = []
    if not source_hashes_pass:
        blockers.append("frozen_source_hash_mismatch")
    if not all_receipts_pass:
        blockers.append("one_or_more_immutable_receipts_failed")
    if duplicates:
        blockers.append("duplicate_post_freeze_event_keys_detected")
    if latest_completed is None:
        blockers.append("no_completed_post_freeze_utc_day")
    elif earliest_ready is None:
        blockers.extend(
            f"development_count_gate_not_met:{name}"
            for name, passed in latest_completed["count_gates"].items()
            if not passed
        )
        if not latest_completed["data_derived_regime_episode_gate"]["passed"]:
            blockers.append("data_derived_regime_episode_gate_not_met")
        if not latest_completed["causal_3m_timestamp_coverage"]["passed"]:
            blockers.append("causal_3m_timestamp_coverage_not_met")

    generated = utc_tag()
    ready = (
        earliest_ready is not None and source_hashes_pass and all_receipts_pass and duplicates == 0
    )
    payload = {
        "generated_at_utc": generated,
        "experiment_id": "E62",
        "status": "blind_pre_unblind_gate_audited",
        "research_only": True,
        "outcomes_read": False,
        "candle_values_read": False,
        "original_preregistration": relative(prereg_path),
        "amendment": relative(amendment_path),
        "causal_daily_labels": relative(LABEL_PATH),
        "frozen_source_hash_checks": source_hash_checks,
        "frozen_source_hashes_pass": source_hashes_pass,
        "label_artifact_audit": label_audit,
        "receipt_count": len(receipts),
        "all_receipts_pass": all_receipts_pass,
        "duplicate_post_freeze_event_keys": duplicates,
        "independent_post_freeze_events": len(independent),
        "three_minute_timestamp_sources": date_metadata,
        "latest_completed_prefix": latest_completed,
        "earliest_completed_pre_unblind_ready_prefix": earliest_ready,
        "development_prefix_freeze_allowed": ready,
        "development_outcome_read_allowed": False,
        "required_next_step_if_ready": (
            "freeze_the_reported_development_prefix_in_a_separate_immutable_lock_before_outcome_read"
            if ready
            else None
        ),
        "blockers": sorted(set(blockers)),
        "decision": (
            "freeze_development_prefix_before_unblind" if ready else "continue_blind_collection"
        ),
        "strategy_synthesis_allowed": False,
        "registry_allowed": False,
        "dryrun_permission": False,
    }
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_ROOT / f"e62_pre_unblind_audit_{generated}.json"
    md_path = REPORT_ROOT / f"e62_pre_unblind_audit_{generated}.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    latest = latest_completed or {}
    regime = latest.get("data_derived_regime_episode_gate") or {}
    coverage = latest.get("causal_3m_timestamp_coverage") or {}
    lines = [
        "# E62 Pre-Unblind Audit",
        "",
        f"- Generated UTC: `{generated}`",
        f"- Frozen source hashes passed: `{source_hashes_pass}`",
        f"- Immutable receipts passed: `{all_receipts_pass}`",
        f"- Independent post-freeze events: `{len(independent)}`",
        f"- Latest completed prefix: `{latest.get('end_utc_date')}`",
        f"- Eligible regime episodes: `{regime.get('eligible_episode_count', 0)}`",
        f"- 3m timestamp coverage passed: `{coverage.get('passed', False)}`",
        "- Candle columns read by this audit: `date` only",
        "- Candle values read: `False`",
        "- Outcomes read: `False`",
        f"- Decision: `{payload['decision']}`",
        "",
        "## Latest Completed Prefix Gates",
        "",
        *[
            f"- `{name}`: `{'PASS' if passed else 'BLOCKED'}`"
            for name, passed in (latest.get("count_gates") or {}).items()
        ],
        f"- `data_derived_regime_episodes`: `{'PASS' if regime.get('passed') else 'BLOCKED'}`",
        f"- `causal_3m_timestamp_coverage`: `{'PASS' if coverage.get('passed') else 'BLOCKED'}`",
        "",
        "## Blockers",
        "",
        *(
            [f"- `{blocker}`" for blocker in payload["blockers"]]
            if payload["blockers"]
            else ["- None at the blind pre-unblind layer."]
        ),
        "",
        "This audit reads event receipts, causal daily labels, and only the 3m candle "
        "timestamp column. It does not read candle values, calculate event outcomes, "
        "select a direction, or synthesize strategy code.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    shutil.copy2(json_path, REPORT_ROOT / "latest_e62_pre_unblind_audit.json")
    shutil.copy2(md_path, REPORT_ROOT / "latest_e62_pre_unblind_audit.md")
    return json_path, md_path


def main() -> int:
    json_path, md_path = build_report()
    print(relative(json_path))
    print(relative(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
