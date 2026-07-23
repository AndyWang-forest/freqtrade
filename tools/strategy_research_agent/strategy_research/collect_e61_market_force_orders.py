#!/usr/bin/env python3
"""Audit Binance's migrated futures WebSocket path and collect force orders.

E61 is a research-data experiment. It compares the legacy and current market
stream paths with a high-frequency aggTrade control, then collects direct
USDT-M forceOrder snapshots from the current endpoint. It never calculates
returns or creates a strategy.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import websockets


REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = REPO_ROOT / "user_data/strategy_research"
DATA_ROOT = REPO_ROOT / "user_data/data/binance/futures_aux/force_order_market_v2"
RECEIPT_ROOT = RESEARCH_ROOT / "data_receipts/force_order_market_v2"
REPORT_ROOT = RESEARCH_ROOT / "event_studies"
PREREG_PATH = (
    RESEARCH_ROOT
    / "preregistrations/e61_market_stream_force_order_migration_v2_20260723.json"
)

EXPERIMENT_ID = "E61"
EVENT_SCHEMA = "binance_usdtm_force_order_snapshot_v2"
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
STREAMS = [f"{pair.lower()}@forceOrder" for pair in PAIRS]
LEGACY_CONTROL_URL = (
    "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade"
)
MARKET_CONTROL_URL = (
    "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"
)
MARKET_FORCE_URL = (
    "wss://fstream.binance.com/market/stream?streams=" + "/".join(STREAMS)
)
OFFICIAL_DOC = (
    "https://developers.binance.com/en/docs/catalog/"
    "core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--prepare", action="store_true")
    actions.add_argument("--collect", action="store_true")
    actions.add_argument("--verify-receipt", type=Path)
    parser.add_argument("--control-seconds", type=int, default=20)
    parser.add_argument("--duration-seconds", type=int, default=600)
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_tag(value: datetime | None = None) -> str:
    return (value or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes() -> dict[str, str]:
    source = Path(__file__).resolve()
    return {relative(source): sha256(source)}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def prepare_preregistration() -> Path:
    created = utc_now()
    payload = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "created_at_utc": created.isoformat(),
        "freeze_cutoff_utc_exclusive": created.isoformat(),
        "status": "frozen_before_collection",
        "research_only": True,
        "question": (
            "Did Binance's futures WebSocket market-path migration invalidate "
            "E39 transport evidence, and can the current endpoint collect direct "
            "USDT-M forceOrder snapshots with immutable receipts?"
        ),
        "novelty_boundary": {
            "new_evidence_fingerprint": (
                "current /market/stream endpoint plus explicit UM st=1 validation"
            ),
            "supersedes_transport_only": "E39 legacy /stream endpoint",
            "does_not_retest": [
                "E39 market outcomes",
                "E60 aggregate OI reversal",
                "E12/E13 OI-taker-funding proxies",
            ],
        },
        "source_hashes": source_hashes(),
        "source_evidence": {
            "official_documentation": OFFICIAL_DOC,
            "documented_current_base": "wss://fstream.binance.com/market/stream",
            "documented_force_stream": "{symbol}@forceOrder",
            "documented_symbol_type": "st=1 means USDT-M; st=2 means COIN-M",
            "observed_current_payload_location": "data.o.st",
        },
        "pre_outcome_implementation_correction": {
            "superseded_preregistration": (
                "user_data/strategy_research/preregistrations/"
                "e61_market_stream_force_order_migration_v1_20260723.json"
            ),
            "superseded_run": (
                "user_data/strategy_research/event_studies/"
                "e61_market_stream_force_order_migration_20260723T031802Z.json"
            ),
            "observation": (
                "The first frozen run received 37 forceOrder messages but rejected "
                "all of them because symbol type was read from data.st. A public "
                "key-only payload audit showed the current field at data.o.st."
            ),
            "correction": "Read symbol type from order st; no other rule changed.",
            "returns_or_strategy_outcomes_read": False,
            "pair_side_threshold_horizon_cost_or_risk_changed": False,
        },
        "collection_contract": {
            "pairs": PAIRS,
            "legacy_control_url": LEGACY_CONTROL_URL,
            "market_control_url": MARKET_CONTROL_URL,
            "market_force_url": MARKET_FORCE_URL,
            "control_stream": "btcusdt@aggTrade",
            "control_purpose": "transport only; never a strategy factor",
            "event_schema": EVENT_SCHEMA,
            "accept_symbol_type": 1,
            "stream_semantics": (
                "latest liquidation-order snapshot per symbol per 1000ms, "
                "not a complete order tape"
            ),
            "credentials_required": False,
            "trading_endpoint_used": False,
        },
        "gates": {
            "market_control_events_min": 1,
            "invalid_force_events": 0,
            "receipt_hash_verification": True,
            "force_event_optional_for_transport_audit": True,
            "force_event_required_for_future_event_study": True,
        },
        "outcomes_read": False,
        "strategy_synthesis_allowed": False,
        "registry_allowed": False,
        "dryrun_permission": False,
    }
    if PREREG_PATH.exists():
        existing = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
        if existing.get("source_hashes") != payload["source_hashes"]:
            raise RuntimeError("Frozen E61 preregistration does not match source")
        return PREREG_PATH
    atomic_json(PREREG_PATH, payload)
    return PREREG_PATH


def load_preregistration() -> dict[str, Any]:
    if not PREREG_PATH.exists():
        raise RuntimeError("Run --prepare before E61 collection")
    payload = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    if payload.get("status") != "frozen_before_collection":
        raise RuntimeError("E61 preregistration is not frozen")
    if payload.get("source_hashes") != source_hashes():
        raise RuntimeError("E61 collector source drifted after preregistration")
    return payload


def decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal {name}: {value}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"invalid decimal {name}: {value}")
    return result


def canonical_force_event(message: str) -> dict[str, Any]:
    wrapper = json.loads(message)
    stream = str(wrapper["stream"])
    payload = wrapper["data"]
    order = payload["o"]
    symbol = str(order["s"])
    side = str(order["S"])
    symbol_type = int(order["st"])
    if stream not in STREAMS or symbol not in PAIRS:
        raise ValueError(f"unexpected stream payload: {stream}/{symbol}")
    if payload.get("e") != "forceOrder" or side not in {"BUY", "SELL"}:
        raise ValueError("unexpected force-order event type or side")
    if symbol_type != 1:
        raise ValueError(f"non-USDT-M force order: st={symbol_type}")

    original_qty = decimal(order["q"], "original_qty")
    price = decimal(order["p"], "price")
    average_price = decimal(order["ap"], "average_price")
    accumulated_qty = decimal(order["z"], "accumulated_filled_qty")
    execution_price = average_price if average_price > 0 else price
    return {
        "event_schema": EVENT_SCHEMA,
        "collector_received_ns": time.time_ns(),
        "stream": stream,
        "event_time_ms": int(payload["E"]),
        "symbol": symbol,
        "symbol_type": symbol_type,
        "force_order_side": side,
        "liquidated_position_side": "long" if side == "SELL" else "short",
        "order_type": str(order["o"]),
        "time_in_force": str(order["f"]),
        "original_qty": str(original_qty),
        "price": str(price),
        "average_price": str(average_price),
        "order_status": str(order["X"]),
        "last_filled_qty": str(decimal(order["l"], "last_filled_qty")),
        "accumulated_filled_qty": str(accumulated_qty),
        "trade_time_ms": int(order["T"]),
        "executed_quote_notional": str(execution_price * accumulated_qty),
    }


async def control_count(name: str, url: str, seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    count = 0
    first_event: dict[str, Any] | None = None
    error: str | None = None
    try:
        async with websockets.connect(
            url,
            open_timeout=15,
            ping_interval=None,
            close_timeout=2,
            max_size=1_000_000,
        ) as websocket:
            while time.monotonic() - started < seconds:
                remaining = max(seconds - (time.monotonic() - started), 0.1)
                try:
                    raw = await asyncio.wait_for(
                        websocket.recv(), timeout=min(2.0, remaining)
                    )
                except asyncio.TimeoutError:
                    continue
                wrapper = json.loads(raw)
                payload = wrapper.get("data", wrapper)
                if payload.get("e") == "aggTrade":
                    count += 1
                    if first_event is None:
                        first_event = {
                            "stream": wrapper.get("stream"),
                            "symbol": payload.get("s"),
                            "symbol_type": payload.get("st"),
                        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "name": name,
        "url": url,
        "duration_seconds": round(time.monotonic() - started, 6),
        "events": count,
        "first_event": first_event,
        "error": error,
    }


async def collect_force_orders(seconds: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deadline = time.monotonic() + seconds
    started = utc_now()
    rows: list[dict[str, Any]] = []
    attempts = 0
    successful_connections = 0
    connected_seconds = 0.0
    invalid_events = 0
    reconnect_errors: list[str] = []
    while time.monotonic() < deadline:
        attempts += 1
        connected_at: float | None = None
        try:
            async with websockets.connect(
                MARKET_FORCE_URL,
                open_timeout=15,
                ping_interval=None,
                close_timeout=2,
                max_size=1_000_000,
            ) as websocket:
                successful_connections += 1
                connected_at = time.monotonic()
                while time.monotonic() < deadline:
                    remaining = max(deadline - time.monotonic(), 0.1)
                    try:
                        message = await asyncio.wait_for(
                            websocket.recv(), timeout=min(5.0, remaining)
                        )
                    except asyncio.TimeoutError:
                        continue
                    try:
                        rows.append(canonical_force_event(message))
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        invalid_events += 1
        except Exception as exc:
            reconnect_errors.append(f"{type(exc).__name__}: {exc}")
            if time.monotonic() < deadline:
                await asyncio.sleep(min(2.0, deadline - time.monotonic()))
        finally:
            if connected_at is not None:
                connected_seconds += max(
                    min(time.monotonic(), deadline) - connected_at, 0.0
                )
    finished = utc_now()
    return rows, {
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat(),
        "requested_duration_seconds": seconds,
        "observed_duration_seconds": (finished - started).total_seconds(),
        "connection_attempts": attempts,
        "successful_connections": successful_connections,
        "connected_seconds": connected_seconds,
        "connection_coverage": connected_seconds / max(seconds, 1),
        "invalid_events": invalid_events,
        "reconnect_errors": reconnect_errors,
    }


def write_segment(
    rows: list[dict[str, Any]], prereg: dict[str, Any]
) -> tuple[Path | None, Path | None]:
    if not rows:
        return None, None
    now = utc_now()
    day = now.strftime("%Y-%m-%d")
    stem = f"forceorder_market_v2_{utc_tag(now)}_{uuid.uuid4().hex[:10]}"
    directory = DATA_ROOT / day
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / f"{stem}.ndjson.gz"
    part_path = final_path.with_suffix(final_path.suffix + ".part")
    with gzip.open(part_path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    with part_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(part_path, final_path)

    pair_counts = {pair: 0 for pair in PAIRS}
    side_counts = {"long": 0, "short": 0}
    for row in rows:
        pair_counts[row["symbol"]] += 1
        side_counts[row["liquidated_position_side"]] += 1
    receipt_path = RECEIPT_ROOT / day / f"{stem}.receipt.json"
    atomic_json(
        receipt_path,
        {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "event_schema": EVENT_SCHEMA,
            "created_at_utc": utc_now().isoformat(),
            "segment": relative(final_path),
            "segment_sha256": sha256(final_path),
            "segment_bytes": final_path.stat().st_size,
            "rows": len(rows),
            "pair_counts": pair_counts,
            "liquidated_side_counts": side_counts,
            "collector_source_sha256": prereg["source_hashes"],
            "preregistration": relative(PREREG_PATH),
            "preregistration_sha256": sha256(PREREG_PATH),
            "outcomes_read": False,
        },
    )
    return final_path, receipt_path


def verify_receipt(path: Path) -> dict[str, Any]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    segment = REPO_ROOT / receipt["segment"]
    rows = 0
    valid = True
    pair_counts = {pair: 0 for pair in PAIRS}
    side_counts = {"long": 0, "short": 0}
    if segment.exists():
        with gzip.open(segment, "rt", encoding="utf-8") as handle:
            for line in handle:
                rows += 1
                try:
                    row = json.loads(line)
                    valid = valid and row["event_schema"] == EVENT_SCHEMA
                    valid = valid and row["symbol_type"] == 1
                    valid = valid and row["symbol"] in pair_counts
                    valid = valid and row["liquidated_position_side"] in side_counts
                    pair_counts[row["symbol"]] += 1
                    side_counts[row["liquidated_position_side"]] += 1
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    valid = False
    checks = {
        "segment_exists": segment.exists(),
        "sha256_matches": segment.exists()
        and sha256(segment) == receipt["segment_sha256"],
        "bytes_match": segment.exists()
        and segment.stat().st_size == int(receipt["segment_bytes"]),
        "rows_match": rows == int(receipt["rows"]),
        "pair_counts_match": pair_counts == receipt["pair_counts"],
        "side_counts_match": side_counts == receipt["liquidated_side_counts"],
        "rows_valid": valid and rows > 0,
    }
    return {
        "receipt": relative(path),
        "rows": rows,
        "checks": checks,
        "passed": all(checks.values()),
        "blockers": [name for name, passed in checks.items() if not passed],
    }


def write_report(
    controls: dict[str, Any],
    rows: list[dict[str, Any]],
    collection: dict[str, Any],
    segment: Path | None,
    receipt: Path | None,
) -> tuple[Path, Path]:
    verification = verify_receipt(receipt) if receipt else None
    pair_counts = {pair: 0 for pair in PAIRS}
    side_counts = {"long": 0, "short": 0}
    for row in rows:
        pair_counts[row["symbol"]] += 1
        side_counts[row["liquidated_position_side"]] += 1
    market_control_passed = controls["market"]["events"] > 0
    legacy_blackhole_observed = (
        controls["legacy"]["events"] == 0 and controls["market"]["events"] > 0
    )
    transport_passed = (
        market_control_passed
        and collection["successful_connections"] > 0
        and collection["connection_coverage"] >= 0.90
        and collection["invalid_events"] == 0
    )
    event_path_ready = bool(rows) and bool(verification and verification["passed"])
    status = (
        "current_force_order_path_event_receipt_verified"
        if event_path_ready
        else "current_market_path_verified_force_event_pending"
        if transport_passed
        else "market_path_transport_failed"
    )
    decision = (
        "continue_unchanged_prospective_force_order_collection"
        if transport_passed
        else "repair_current_market_path_before_collection"
    )
    tag = utc_tag()
    json_path = REPORT_ROOT / f"e61_market_stream_force_order_migration_{tag}.json"
    md_path = REPORT_ROOT / f"e61_market_stream_force_order_migration_{tag}.md"
    payload = {
        "generated_at_utc": tag,
        "experiment_id": EXPERIMENT_ID,
        "status": status,
        "decision": decision,
        "research_only": True,
        "protocol_controls": controls,
        "legacy_blackhole_observed": legacy_blackhole_observed,
        "e39_transport_evidence_status": "superseded_by_current_endpoint_audit",
        "force_collection": collection,
        "force_events": len(rows),
        "pair_counts": pair_counts,
        "liquidated_side_counts": side_counts,
        "segment": relative(segment) if segment else None,
        "receipt": relative(receipt) if receipt else None,
        "receipt_verification": verification,
        "transport_passed": transport_passed,
        "event_path_ready": event_path_ready,
        "outcomes_read": False,
        "strategy_synthesis_allowed": False,
        "registry_allowed": False,
        "dryrun_permission": False,
    }
    atomic_json(json_path, payload)
    lines = [
        "# E61 Binance Market-Stream Migration Audit",
        "",
        f"- Status: `{status}`",
        f"- Legacy aggTrade control events: `{controls['legacy']['events']}`",
        f"- Current market aggTrade control events: `{controls['market']['events']}`",
        f"- Legacy blackhole observed: `{legacy_blackhole_observed}`",
        f"- Current forceOrder connection coverage: `{collection['connection_coverage']:.2%}`",
        f"- Direct USDT-M forceOrder events: `{len(rows)}`",
        f"- Invalid forceOrder events: `{collection['invalid_events']}`",
        f"- Receipt verified: `{bool(verification and verification['passed'])}`",
        "- Outcomes read: `False`",
        "",
        "## Pair Coverage",
        "",
        "| pair | snapshots |",
        "|---|---:|",
        *[f"| `{pair}` | {pair_counts[pair]} |" for pair in PAIRS],
        "",
        "## Interpretation",
        "",
        "- The legacy E39 `/stream` transport evidence is superseded; it cannot prove liquidation sparsity.",
        "- The current `/market/stream` control must pass before forceOrder zero counts are treated as sparse-market evidence.",
        "- Collection and receipt success are data-infrastructure evidence only, not trading edge.",
        f"- Decision: `{decision}`",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    shutil.copy2(json_path, REPORT_ROOT / "latest_e61_market_stream_force_order_migration.json")
    shutil.copy2(md_path, REPORT_ROOT / "latest_e61_market_stream_force_order_migration.md")
    return json_path, md_path


async def run_collection(control_seconds: int, duration_seconds: int) -> tuple[Path, Path]:
    prereg = load_preregistration()
    if control_seconds < 10 or duration_seconds < 30:
        raise ValueError("E61 requires at least 10s control and 30s force collection")
    legacy, market = await asyncio.gather(
        control_count("legacy", LEGACY_CONTROL_URL, control_seconds),
        control_count("market", MARKET_CONTROL_URL, control_seconds),
    )
    rows, collection = await collect_force_orders(duration_seconds)
    segment, receipt = write_segment(rows, prereg)
    return write_report(
        {"legacy": legacy, "market": market},
        rows,
        collection,
        segment,
        receipt,
    )


def main() -> int:
    args = parse_args()
    if args.prepare:
        print(relative(prepare_preregistration()))
        return 0
    if args.verify_receipt is not None:
        result = verify_receipt(args.verify_receipt.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 2
    json_path, md_path = asyncio.run(
        run_collection(args.control_seconds, args.duration_seconds)
    )
    print(relative(json_path))
    print(relative(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
