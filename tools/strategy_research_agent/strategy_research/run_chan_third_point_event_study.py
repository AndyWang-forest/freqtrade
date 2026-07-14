#!/usr/bin/env python3
"""Evaluate causal Chan third-point events before any strategy synthesis.

The study compares locked-hub third buy/sell events with a simple 24-hour
Donchian breakout/retest baseline.  It is research-only and never changes the
strategy registry, dry-run configuration, or live configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from chan_market_structure import (
    ChanStructure,
    ThirdPointEvent,
    compute_chan_structure,
    select_independent_positive_windows,
    structure_counts,
    unique_event_outcomes,
)
from cost_model import REALISTIC_SCENARIO, STRESS_SCENARIO, CostScenario, margin_cost_pct
from pair_universe import pair_to_stem, pairs_for_scope
from regime_window_builder import load_regime_manifest
from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
DATA_ROOT = REPO_ROOT / "user_data/data/binance/futures"
OUTPUT_DIR = REPO_ROOT / "user_data/strategy_research/event_studies"
LATEST_JSON = OUTPUT_DIR / "latest_chan_third_point_event_study.json"
LATEST_MD = OUTPUT_DIR / "latest_chan_third_point_event_study.md"
LATEST_SUMMARY = OUTPUT_DIR / "latest_chan_third_point_event_study_summary.csv"
LATEST_SIGNALS = OUTPUT_DIR / "latest_chan_third_point_event_study_signals.csv"
LATEST_TRADES = OUTPUT_DIR / "latest_chan_third_point_event_study_trades.csv"

TIMEFRAME = "15m"
LEVERAGE = 50.0
HORIZONS = {"1h": 4, "3h": 12, "8h": 32}
DECLUSTER_BARS = 32
DONCHIAN_LOOKBACK = 96
DONCHIAN_RETEST_BARS = 32
MIN_EVENT_SAMPLES = 12
MIN_POSITIVE_WINDOWS = 2


@dataclass(frozen=True)
class EventSignal:
    event: str
    side: Literal["long", "short"]
    signal_idx: int
    source: Literal["chan", "baseline"]
    details: dict[str, Any]


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pair(pair: str) -> tuple[pd.DataFrame, Path]:
    path = DATA_ROOT / f"{pair_to_stem(pair)}-{TIMEFRAME}-futures.feather"
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_feather(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.sort_values("date").reset_index(drop=True)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - frame["close"].shift(1)).abs(),
            (frame["low"] - frame["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_14"] = true_range.rolling(14, min_periods=14).mean()
    return frame, path


def _third_point_signals(events: list[ThirdPointEvent]) -> list[EventSignal]:
    return [
        EventSignal(
            event=event.event,
            side=event.side,
            signal_idx=event.signal_idx,
            source="chan",
            details={
                "retest_pivot_idx": event.retest_pivot_idx,
                "departure_pivot_idx": event.departure_pivot_idx,
                "hub_confirmed_idx": event.hub_confirmed_idx,
                "hub_lower": event.hub_lower,
                "hub_upper": event.hub_upper,
            },
        )
        for event in events
    ]


def donchian_retest_signals(frame: pd.DataFrame) -> list[EventSignal]:
    """Build a coarse causal breakout/retest baseline with the same entry timing."""

    prior_high = frame["high"].rolling(DONCHIAN_LOOKBACK).max().shift(1)
    prior_low = frame["low"].rolling(DONCHIAN_LOOKBACK).min().shift(1)
    active_long: tuple[int, float] | None = None
    active_short: tuple[int, float] | None = None
    signals: list[EventSignal] = []

    for idx in range(DONCHIAN_LOOKBACK, len(frame) - 1):
        row = frame.iloc[idx]
        atr = float(row["atr_14"])
        if pd.isna(atr) or atr <= 0:
            continue

        if active_long is not None:
            departure_idx, boundary = active_long
            if idx - departure_idx > DONCHIAN_RETEST_BARS or float(row["low"]) <= boundary:
                active_long = None
            elif float(row["low"]) <= boundary + atr and float(row["close"]) > boundary:
                signals.append(
                    EventSignal(
                        event="donchian_retest_long",
                        side="long",
                        signal_idx=idx,
                        source="baseline",
                        details={"departure_idx": departure_idx, "boundary": boundary},
                    )
                )
                active_long = None

        if active_short is not None:
            departure_idx, boundary = active_short
            if idx - departure_idx > DONCHIAN_RETEST_BARS or float(row["high"]) >= boundary:
                active_short = None
            elif float(row["high"]) >= boundary - atr and float(row["close"]) < boundary:
                signals.append(
                    EventSignal(
                        event="donchian_retest_short",
                        side="short",
                        signal_idx=idx,
                        source="baseline",
                        details={"departure_idx": departure_idx, "boundary": boundary},
                    )
                )
                active_short = None

        high_boundary = prior_high.iat[idx]
        low_boundary = prior_low.iat[idx]
        if (
            active_long is None
            and pd.notna(high_boundary)
            and float(row["close"]) > float(high_boundary)
        ):
            active_long = (idx, float(high_boundary))
        if (
            active_short is None
            and pd.notna(low_boundary)
            and float(row["close"]) < float(low_boundary)
        ):
            active_short = (idx, float(low_boundary))
    return signals


def decluster(signals: list[EventSignal], gap_bars: int = DECLUSTER_BARS) -> list[EventSignal]:
    selected: list[EventSignal] = []
    last_by_event: dict[str, int] = {}
    for signal in sorted(signals, key=lambda item: (item.signal_idx, item.event)):
        previous = last_by_event.get(signal.event)
        if previous is not None and signal.signal_idx - previous < gap_bars:
            continue
        selected.append(signal)
        last_by_event[signal.event] = signal.signal_idx
    return selected


def event_identity(event: ThirdPointEvent) -> tuple[Any, ...]:
    return (
        event.event,
        event.side,
        event.signal_idx,
        event.retest_pivot_idx,
        event.departure_pivot_idx,
        event.hub_confirmed_idx,
        round(event.hub_lower, 10),
        round(event.hub_upper, 10),
    )


def prefix_invariance_audit(frame: pd.DataFrame, full: ChanStructure) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for fraction in (0.40, 0.65, 0.85):
        cutoff = max(int(len(frame) * fraction), 1000)
        prefix = compute_chan_structure(frame.iloc[:cutoff])
        prefix_events = {event_identity(event) for event in prefix.events}
        full_events = {event_identity(event) for event in full.events if event.signal_idx < cutoff}
        missing = sorted(full_events - prefix_events)
        extra = sorted(prefix_events - full_events)
        checks.append(
            {
                "cutoff_rows": cutoff,
                "cutoff_date": str(frame.at[cutoff - 1, "date"]),
                "prefix_events": len(prefix_events),
                "full_events_available_by_cutoff": len(full_events),
                "passed": not missing and not extra,
                "missing_count": len(missing),
                "extra_count": len(extra),
            }
        )
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def active_window_bank(manifest: dict[str, Any], frame: pd.DataFrame) -> list[dict[str, Any]]:
    windows = [
        {
            "label": item["label"],
            "name": item["name"],
            "start": pd.Timestamp(item["start"], tz="UTC"),
            "end_exclusive": pd.Timestamp(item["end"], tz="UTC") + pd.Timedelta(days=1),
            "role": item.get("episode_role", "active"),
        }
        for item in manifest.get("windows", [])
        if item.get("status") == "active"
    ]
    windows.append(
        {
            "label": "all_history",
            "name": "all_history_diagnostic",
            "start": frame["date"].min(),
            "end_exclusive": frame["date"].max() + pd.Timedelta(minutes=15),
            "role": "diagnostic_only",
        }
    )
    return windows


def price_friction(scenario: CostScenario) -> float:
    return (2 * scenario.fee) + scenario.slippage_bps / 10000.0


def account_return_pct(price_return: float, scenario: CostScenario) -> float:
    return (price_return - price_friction(scenario)) * LEVERAGE * 100.0


def evaluate_signals(
    pair: str,
    frame: pd.DataFrame,
    signals: list[EventSignal],
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    max_horizon = max(HORIZONS.values())
    for window in windows:
        for signal in signals:
            entry_idx = signal.signal_idx + 1
            exit_idx = entry_idx + max_horizon - 1
            if exit_idx >= len(frame):
                continue
            signal_date = frame.at[signal.signal_idx, "date"]
            entry_date = frame.at[entry_idx, "date"]
            exit_date = frame.at[exit_idx, "date"]
            if signal_date < window["start"] or exit_date >= window["end_exclusive"]:
                continue
            entry = float(frame.at[entry_idx, "open"])
            sign = 1.0 if signal.side == "long" else -1.0
            forward = frame.iloc[entry_idx : exit_idx + 1]
            row: dict[str, Any] = {
                "window": window["name"],
                "regime_label": window["label"],
                "window_role": window["role"],
                "window_start": window["start"],
                "window_end_exclusive": window["end_exclusive"],
                "pair": pair,
                "event": signal.event,
                "source": signal.source,
                "side": signal.side,
                "signal_idx": signal.signal_idx,
                "signal_date": signal_date,
                "entry_idx": entry_idx,
                "entry_date": entry_date,
                "entry_open": entry,
                "details": json.dumps(signal.details, ensure_ascii=False, sort_keys=True),
            }
            for label, bars in HORIZONS.items():
                close = float(frame.at[entry_idx + bars - 1, "close"])
                gross = sign * (close / entry - 1.0)
                row[f"gross_price_ret_{label}"] = gross
                row[f"gross_account_pct_{label}"] = gross * LEVERAGE * 100.0
                row[f"realistic_account_pct_{label}"] = account_return_pct(
                    gross, REALISTIC_SCENARIO
                )
                row[f"stress_account_pct_{label}"] = account_return_pct(gross, STRESS_SCENARIO)
            if signal.side == "long":
                row["mfe_price_8h"] = float(forward["high"].max()) / entry - 1.0
                row["mae_price_8h"] = max(1.0 - float(forward["low"].min()) / entry, 0.0)
            else:
                row["mfe_price_8h"] = 1.0 - float(forward["low"].min()) / entry
                row["mae_price_8h"] = max(float(forward["high"].max()) / entry - 1.0, 0.0)
            trades.append(row)
    return trades


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "window",
        "regime_label",
        "window_role",
        "window_start",
        "window_end_exclusive",
        "pair",
        "event",
        "source",
        "side",
        "samples",
        "gross_account_pct_8h",
        "realistic_account_pct_8h",
        "stress_account_pct_8h",
        "realistic_win_rate_8h",
        "mfe_price_8h",
        "mae_price_8h",
        "mfe_mae_ratio_8h",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    group_cols = [
        "window",
        "regime_label",
        "window_role",
        "window_start",
        "window_end_exclusive",
        "pair",
        "event",
        "source",
        "side",
    ]
    for keys, group in trades.groupby(group_cols, dropna=False):
        mean_mae = float(group["mae_price_8h"].mean())
        mean_mfe = float(group["mfe_price_8h"].mean())
        rows.append(
            {
                **dict(zip(group_cols, keys, strict=True)),
                "samples": len(group),
                "gross_account_pct_8h": float(group["gross_account_pct_8h"].mean()),
                "realistic_account_pct_8h": float(group["realistic_account_pct_8h"].mean()),
                "stress_account_pct_8h": float(group["stress_account_pct_8h"].mean()),
                "realistic_win_rate_8h": float((group["realistic_account_pct_8h"] > 0).mean()),
                "mfe_price_8h": mean_mfe,
                "mae_price_8h": mean_mae,
                "mfe_mae_ratio_8h": mean_mfe / mean_mae if mean_mae > 0 else None,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def evidence_decisions(trades: pd.DataFrame) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    mapping = {
        "chan_third_buy": "donchian_retest_long",
        "chan_third_sell": "donchian_retest_short",
    }
    active = trades[trades["window_role"] != "diagnostic_only"].copy()
    unique_active = unique_event_outcomes(active)
    for event, baseline_event in mapping.items():
        window_chan = active[active["event"] == event]
        chan = unique_active[unique_active["event"] == event]
        baseline = unique_active[unique_active["event"] == baseline_event]
        samples = len(chan)
        positive_window_names = select_independent_positive_windows(window_chan)
        positive_windows = len(positive_window_names)
        realistic = float(chan["realistic_account_pct_8h"].mean()) if samples else None
        stress = float(chan["stress_account_pct_8h"].mean()) if samples else None
        baseline_realistic = (
            float(baseline["realistic_account_pct_8h"].mean()) if not baseline.empty else None
        )
        incremental = (
            realistic - baseline_realistic
            if realistic is not None and baseline_realistic is not None
            else None
        )
        if samples == 0:
            status = "no_sample"
        elif samples < MIN_EVENT_SAMPLES:
            status = "thin_sample"
        elif positive_windows < MIN_POSITIVE_WINDOWS:
            status = "needs_independent_window_replication"
        elif realistic is None or realistic <= 0:
            status = "reject_after_realistic_cost"
        elif incremental is not None and incremental <= 0:
            status = "reject_no_incremental_edge_vs_donchian"
        elif stress is not None and stress <= 0:
            status = "realistic_edge_but_stress_fragile"
        else:
            status = "event_edge_candidate_manual_review"
        decisions.append(
            {
                "event": event,
                "baseline_event": baseline_event,
                "samples": samples,
                "positive_independent_windows": positive_windows,
                "positive_independent_window_names": positive_window_names,
                "realistic_account_pct_8h": realistic,
                "stress_account_pct_8h": stress,
                "baseline_realistic_account_pct_8h": baseline_realistic,
                "incremental_account_pct_8h": incremental,
                "status": status,
                "knowledge_activation_allowed": False,
                "strategy_synthesis_allowed": False,
            }
        )
    return decisions


def signal_rows(pair: str, frame: pd.DataFrame, signals: list[EventSignal]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for signal in signals:
        if signal.signal_idx + 1 >= len(frame):
            continue
        rows.append(
            {
                "pair": pair,
                "event": signal.event,
                "source": signal.source,
                "side": signal.side,
                "signal_idx": signal.signal_idx,
                "signal_date": frame.at[signal.signal_idx, "date"],
                "entry_idx": signal.signal_idx + 1,
                "entry_date": frame.at[signal.signal_idx + 1, "date"],
                "details": json.dumps(signal.details, ensure_ascii=False, sort_keys=True),
            }
        )
    return rows


def fmt(value: Any, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.{digits}f}"


def write_markdown(payload: dict[str, Any], summary: pd.DataFrame, path: Path) -> None:
    realistic_friction = payload["cost_contract"]["realistic_margin_pct_per_trade"]
    stress_friction = payload["cost_contract"]["stress_margin_pct_per_trade"]
    lines = [
        "# Chan Third-Point Event Study",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Timeframe: `{TIMEFRAME}`; entry: next 15m open after causal confirmation.",
        f"- Pairs: `{', '.join(payload['pairs'])}`",
        "- Research-only: all six Chan cards remain quarantined; "
        "no strategy, registry, dry-run, or live change.",
        "- Comparison: locked-hub third points vs causal 24h Donchian breakout/retest baseline.",
        f"- Realistic 50x round-trip account friction: `{realistic_friction:.3f}%`.",
        f"- Stress 50x round-trip account friction: `{stress_friction:.3f}%`.",
        "",
        "## Causality Audit",
        "",
        "| Pair | Passed | Checkpoints |",
        "|---|---|---:|",
    ]
    for pair, audit in payload["prefix_invariance"].items():
        lines.append(f"| `{pair}` | `{audit['passed']}` | {len(audit['checks'])} |")
    lines.extend(
        [
            "",
            "## Evidence Decision",
            "",
            "| Event | Samples | Positive windows | Realistic 8h | Stress 8h | "
            "Baseline 8h | Incremental | Status |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload["decisions"]:
        lines.append(
            f"| `{row['event']}` | {row['samples']} | {row['positive_independent_windows']} | "
            f"{fmt(row['realistic_account_pct_8h'])}% | {fmt(row['stress_account_pct_8h'])}% | "
            f"{fmt(row['baseline_realistic_account_pct_8h'])}% | "
            f"{fmt(row['incremental_account_pct_8h'])}% | "
            f"`{row['status']}` |"
        )
    lines.extend(
        [
            "",
            "## Window Results",
            "",
            "| Window | Regime | Pair | Event | N | Realistic 8h | Stress 8h | Net win | MFE/MAE |",
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary.to_dict("records"):
        if row["window_role"] == "diagnostic_only":
            continue
        lines.append(
            f"| `{row['window']}` | `{row['regime_label']}` | `{row['pair']}` | `{row['event']}` | "
            f"{row['samples']} | {fmt(row['realistic_account_pct_8h'])}% | "
            f"{fmt(row['stress_account_pct_8h'])}% | {fmt(row['realistic_win_rate_8h'] * 100)}% | "
            f"{fmt(row['mfe_mae_ratio_8h'])} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Contract",
            "",
            "- `thin_sample` means retain the hypothesis without tuning thresholds.",
            "- Recent no-signal does not reject a structure unless the recent window is "
            "its data-derived home regime.",
            "- A positive event study still does not authorize a Freqtrade strategy; "
            "lookahead, recursive, full backtest, family risk, and promotion gates "
            "remain mandatory.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair-scope",
        choices=["core", "extension", "research_all"],
        default="core",
    )
    parser.add_argument("--pairs", nargs="+", default=None)
    parser.add_argument("--skip-prefix-audit", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pairs = args.pairs if args.pairs else pairs_for_scope(args.pair_scope)
    pair_scope = "explicit" if args.pairs else args.pair_scope
    manifest = load_regime_manifest()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = utc_stamp()

    all_signal_rows: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    structures: dict[str, Any] = {}
    audits: dict[str, Any] = {}
    data_sources: dict[str, str] = {}

    for pair in pairs:
        frame, source_path = load_pair(pair)
        structure = compute_chan_structure(frame)
        chan_signals = _third_point_signals(structure.events)
        baseline_signals = donchian_retest_signals(frame)
        signals = decluster([*chan_signals, *baseline_signals])
        windows = active_window_bank(manifest, frame)
        all_signal_rows.extend(signal_rows(pair, frame, signals))
        all_trades.extend(evaluate_signals(pair, frame, signals, windows))
        coverage[pair] = {
            "rows": len(frame),
            "start": str(frame["date"].min()),
            "end": str(frame["date"].max()),
        }
        structures[pair] = structure_counts(structure)
        audits[pair] = (
            {"passed": True, "checks": [], "skipped": True}
            if args.skip_prefix_audit
            else prefix_invariance_audit(frame, structure)
        )
        data_sources[pair] = rel(source_path)

    if not all(audit["passed"] for audit in audits.values()):
        raise RuntimeError(
            "Chan prefix-invariance audit failed; event outcomes are not admissible."
        )

    signals_df = pd.DataFrame(all_signal_rows)
    trades_df = pd.DataFrame(all_trades)
    summary_df = summarize(trades_df)
    decisions = evidence_decisions(trades_df) if not trades_df.empty else []

    stem = f"chan_third_point_event_study_{tag}_{pair_scope}"
    json_path = OUTPUT_DIR / f"{stem}.json"
    md_path = OUTPUT_DIR / f"{stem}.md"
    summary_path = OUTPUT_DIR / f"{stem}_summary.csv"
    signals_path = OUTPUT_DIR / f"{stem}_signals.csv"
    trades_path = OUTPUT_DIR / f"{stem}_trades.csv"
    summary_df.to_csv(summary_path, index=False)
    signals_df.to_csv(signals_path, index=False)
    trades_df.to_csv(trades_path, index=False)

    payload = {
        "generated_at_utc": tag,
        "research_only": True,
        "study": "causal_chan_third_point_vs_donchian_retest",
        "pair_scope": pair_scope,
        "pairs": pairs,
        "timeframe": TIMEFRAME,
        "entry_timing": "next_15m_open_after_signal_available_close",
        "horizons": HORIZONS,
        "decluster_bars": DECLUSTER_BARS,
        "causality_contract": {
            "inclusion_processing": "chronological_directional_merge",
            "fractal_confirmation": (
                "right canonical bar must be locked by a later non-inclusion bar"
            ),
            "pivot_confirmation": "endpoint locked only after later opposite pivot",
            "historical_backfill": False,
            "future_shift": False,
        },
        "event_contract": {
            "chan_third_buy": (
                "locked top leaves hub upper; later locked bottom remains above hub upper"
            ),
            "chan_third_sell": (
                "locked bottom leaves hub lower; later locked top remains below hub lower"
            ),
            "baseline": (
                "24h Donchian close departure followed within 8h by strict non-reentry retest"
            ),
        },
        "risk_contract_unchanged": {
            "market": "Binance USDT-M futures only",
            "margin_mode": "isolated",
            "leverage": 50,
            "minimal_roi": {"0": 1.20, "180": 1.50, "360": 1.00},
            "stoploss": -0.60,
            "primary_entry_timeframes": ["3m", "5m", "15m"],
            "this_study_entry_timeframe": "15m",
        },
        "cost_contract": {
            "realistic": asdict(REALISTIC_SCENARIO),
            "stress": asdict(STRESS_SCENARIO),
            "realistic_margin_pct_per_trade": margin_cost_pct(
                REALISTIC_SCENARIO.fee, REALISTIC_SCENARIO.slippage_bps, LEVERAGE
            ),
            "stress_margin_pct_per_trade": margin_cost_pct(
                STRESS_SCENARIO.fee, STRESS_SCENARIO.slippage_bps, LEVERAGE
            ),
        },
        "regime_manifest": {
            "generated_at_utc": manifest.get("generated_at_utc"),
            "manifest_version": manifest.get("manifest_version"),
            "method": manifest.get("method"),
        },
        "data_sources": data_sources,
        "coverage": coverage,
        "structure_counts": structures,
        "prefix_invariance": audits,
        "decisions": decisions,
        "knowledge_cards": [
            "chan_kline_inclusion_confirmed_fractal",
            "chan_causal_stroke_segment_state_machine",
            "chan_hub_overlap_regime_structure",
            "chan_third_point_breakout_retest",
            "chan_divergence_measurable_hypothesis",
            "chan_anti_repaint_lookahead_contract",
        ],
        "knowledge_activation_allowed": False,
        "strategy_synthesis_allowed": False,
        "dryrun_permission": False,
        "artifacts": {
            "summary_csv": rel(summary_path),
            "signals_csv": rel(signals_path),
            "trades_csv": rel(trades_path),
        },
    }
    write_markdown(payload, summary_df, md_path)
    payload["artifacts"].update(
        {
            "markdown": rel(md_path),
            "summary_sha256": sha256(summary_path),
            "signals_sha256": sha256(signals_path),
            "trades_sha256": sha256(trades_path),
            "markdown_sha256": sha256(md_path),
        }
    )
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(json_path, LATEST_JSON)
    shutil.copyfile(md_path, LATEST_MD)
    shutil.copyfile(summary_path, LATEST_SUMMARY)
    shutil.copyfile(signals_path, LATEST_SIGNALS)
    shutil.copyfile(trades_path, LATEST_TRADES)

    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Wrote {rel(summary_path)}")
    print(f"Wrote {rel(signals_path)}")
    print(f"Wrote {rel(trades_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
