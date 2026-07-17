#!/usr/bin/env python3
"""Run OHLCV event studies before turning ideas into strategies.

The study answers one question before strategy generation: does a proposed
entry event have a forward-return distribution worth engineering?
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import talib.abstract as ta
from cost_model import REALISTIC_SCENARIO
from pair_universe import pairs_for_scope
from regime_window_builder import REGIME_LABELS, active_windows_for_label, regime_entry_mask
from repo_paths import find_repo_root
from research_artifact_index import publish_report
from research_target import ResearchTarget, load_current_research_target


REPO_ROOT = find_repo_root()
DATA_ROOT = REPO_ROOT / "user_data/data/binance/futures"
OUTPUT_DIR = REPO_ROOT / "user_data/strategy_research/event_studies"
LATEST_JSON = OUTPUT_DIR / "latest_event_study.json"
LATEST_MD = OUTPUT_DIR / "latest_event_study.md"
INDEX_JSON = OUTPUT_DIR / "event_study_index.json"
REALISTIC_ROUND_TRIP_FRICTION = (
    2 * REALISTIC_SCENARIO.fee + REALISTIC_SCENARIO.slippage_bps / 10000.0
)

EVENT_FAMILIES = {
    "failed_bounce_short": {"A1"},
    "failed_pullback_long": {"A2"},
    "pullback_resume_long": {"C2"},
    "pullback_resume_atr_q80_long": {"C2"},
    "pullback_resume_volume_q80_long": {"C2"},
    "pullback_resume_short": {"C1"},
    # ``B`` remains the current-market router alias.  The research allocator
    # emits the directional taxonomy codes, so both representations must route
    # to the same predeclared range structures.
    "false_break_long": {"B", "B2"},
    "false_break_short": {"B", "B1"},
    "second_leg_long": {"D2", "E"},
    "second_leg_short": {"D1", "E"},
}


@dataclass(frozen=True)
class EventResult:
    event: str
    pair: str
    side: str
    samples: int
    win_rate_3: float | None
    win_rate_6: float | None
    win_rate_12: float | None
    mean_ret_3: float | None
    mean_ret_6: float | None
    mean_ret_12: float | None
    median_ret_6: float | None
    mean_mfe_12: float | None
    mean_mae_12: float | None
    mfe_mae_ratio_12: float | None
    verdict: str
    notes: str
    raw_samples: int = 0
    mean_after_cost_6: float | None = None
    net_win_rate_6: float | None = None
    gross_gate: str = "not_evaluated"
    cost_gate: str = "not_evaluated"
    positive_independent_windows: int = 0


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def pair_to_stem(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def output_stem(payload: dict[str, Any]) -> str:
    scope = str(payload["pair_scope"]).replace("/", "_").replace(":", "_")
    timeframe = str(payload["timeframe"]).replace("/", "_").replace(":", "_")
    regime = str(payload.get("regime_label") or "all")
    return f"event_study_{payload['generated_at_utc']}_{timeframe}_{scope}_{regime}"


def load_pair(pair: str, timeframe: str) -> pd.DataFrame:
    path = DATA_ROOT / f"{pair_to_stem(pair)}-{timeframe}-futures.feather"
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_feather(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    return frame.sort_values("date").reset_index(drop=True)


def timeframe_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def rolling_days_bars(timeframe: str, days: int) -> int:
    return max(days * 24 * 60 // timeframe_minutes(timeframe), 1)


def add_indicators(frame: pd.DataFrame, timeframe: str = "15m") -> pd.DataFrame:
    frame = frame.copy()
    q80_window = rolling_days_bars(timeframe, 24)
    frame["ema_6"] = ta.EMA(frame, timeperiod=6)
    frame["ema_12"] = ta.EMA(frame, timeperiod=12)
    frame["ema_24"] = ta.EMA(frame, timeperiod=24)
    frame["ema_48"] = ta.EMA(frame, timeperiod=48)
    frame["rsi_14"] = ta.RSI(frame, timeperiod=14)
    frame["atr_14"] = ta.ATR(frame, timeperiod=14)
    frame["atr_pct"] = frame["atr_14"] / frame["close"]
    frame["ret_4"] = frame["close"] / frame["close"].shift(4) - 1.0
    frame["ret_3"] = frame["close"] / frame["close"].shift(3) - 1.0
    frame["ret_12"] = frame["close"] / frame["close"].shift(12) - 1.0
    frame["ret_24"] = frame["close"] / frame["close"].shift(24) - 1.0
    frame["ret_36"] = frame["close"] / frame["close"].shift(36) - 1.0
    frame["ret_96"] = frame["close"] / frame["close"].shift(96) - 1.0
    frame["volume_ratio"] = frame["volume"] / frame["volume"].rolling(24).mean()
    frame["atr_q80_24d"] = frame["atr_pct"].rolling(q80_window).quantile(0.80)
    frame["volume_ratio_q80_24d"] = frame["volume_ratio"].rolling(q80_window).quantile(0.80)
    frame["high_36_prev"] = frame["high"].rolling(36).max().shift(1)
    frame["low_36_prev"] = frame["low"].rolling(36).min().shift(1)
    frame["down_count_6"] = (frame["close"] < frame["open"]).rolling(6).sum()
    frame["up_count_6"] = (frame["close"] > frame["open"]).rolling(6).sum()
    candle_range = (frame["high"] - frame["low"]).replace(0, pd.NA)
    frame["body"] = (frame["close"] - frame["open"]) / frame["close"]
    frame["upper_wick"] = (
        frame["high"] - frame[["open", "close"]].max(axis=1)
    ) / candle_range
    frame["lower_wick"] = (
        frame[["open", "close"]].min(axis=1) - frame["low"]
    ) / candle_range
    bb = ta.BBANDS(frame, timeperiod=36)
    frame["bb_upper"] = bb["upperband"]
    frame["bb_middle"] = bb["middleband"]
    frame["bb_lower"] = bb["lowerband"]
    frame["bb_width"] = (frame["bb_upper"] - frame["bb_lower"]) / frame["bb_middle"]
    return frame


def event_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    liquid = (frame["volume"] > 0) & (frame["volume_ratio"] > 0.7) & frame["atr_pct"].between(0.0005, 0.018)
    trend_up = (frame["close"] > frame["ema_48"]) & (frame["ret_12"] > 0.0015)
    trend_down = (frame["close"] < frame["ema_48"]) & (frame["ret_12"] < -0.0015)
    failed_bounce_short = (
        liquid
        & (frame["ret_96"] < 0.0)
        & (frame["ret_24"] > 0.006)
        & (frame["ret_12"] > 0.0025)
        & (frame["ret_4"] < frame["ret_12"] * 0.55)
        & (frame["body"] < 0.0)
        & (frame["upper_wick"] > 0.25)
        & frame["rsi_14"].between(55.0, 86.0)
    )
    failed_pullback_long = (
        liquid
        & (frame["ret_96"] > 0.0)
        & (frame["ret_24"] < -0.006)
        & (frame["ret_12"] < -0.0025)
        & (frame["ret_4"] > frame["ret_12"] * 0.55)
        & (frame["body"] > 0.0)
        & (frame["lower_wick"] > 0.25)
        & frame["rsi_14"].between(14.0, 45.0)
    )
    pullback_long = (
        liquid
        & trend_up
        & frame["down_count_6"].between(2, 5)
        & (frame["low"].rolling(6).min() <= frame["ema_24"] * 1.001)
        & (frame["close"] > frame["ema_6"])
        & (frame["close"] > frame["open"])
    )
    pullback_atr_q80_long = pullback_long & (frame["atr_pct"] >= frame["atr_q80_24d"])
    pullback_volume_q80_long = pullback_long & (
        frame["volume_ratio"] >= frame["volume_ratio_q80_24d"]
    )
    pullback_short = (
        liquid
        & trend_down
        & frame["up_count_6"].between(2, 5)
        & (frame["high"].rolling(6).max() >= frame["ema_24"] * 0.999)
        & (frame["close"] < frame["ema_6"])
        & (frame["close"] < frame["open"])
    )
    range_ok = liquid & (frame["ret_36"].abs() < 0.025) & (frame["bb_width"] > 0.004)
    false_break_long = (
        range_ok
        & (frame["low"].shift(1) < frame["low_36_prev"].shift(1) * 0.999)
        & (frame["close"].shift(1) > frame["low_36_prev"].shift(1))
        & (frame["close"] > frame["open"])
        & (frame["close"] > frame["ema_6"])
    )
    false_break_short = (
        range_ok
        & (frame["high"].shift(1) > frame["high_36_prev"].shift(1) * 1.001)
        & (frame["close"].shift(1) < frame["high_36_prev"].shift(1))
        & (frame["close"] < frame["open"])
        & (frame["close"] < frame["ema_6"])
    )
    compressed = frame["bb_width"].shift(3).rolling(12).min() < 0.006
    second_leg_long = (
        liquid
        & compressed
        & (frame["high"].shift(1).rolling(3).max() > frame["high_36_prev"].shift(1))
        & (frame["low"] <= frame["high_36_prev"] * 1.0025)
        & (frame["close"] > frame["high_36_prev"])
    )
    second_leg_short = (
        liquid
        & compressed
        & (frame["low"].shift(1).rolling(3).min() < frame["low_36_prev"].shift(1))
        & (frame["high"] >= frame["low_36_prev"] * 0.9975)
        & (frame["close"] < frame["low_36_prev"])
    )
    return {
        "failed_bounce_short": failed_bounce_short,
        "failed_pullback_long": failed_pullback_long,
        "pullback_resume_long": pullback_long,
        "pullback_resume_atr_q80_long": pullback_atr_q80_long,
        "pullback_resume_volume_q80_long": pullback_volume_q80_long,
        "pullback_resume_short": pullback_short,
        "false_break_long": false_break_long,
        "false_break_short": false_break_short,
        "second_leg_long": second_leg_long,
        "second_leg_short": second_leg_short,
    }


def decluster_indices(indices: list[int], minimum_gap_bars: int = 12) -> list[int]:
    selected: list[int] = []
    for index in sorted(indices):
        if not selected or index - selected[-1] >= minimum_gap_bars:
            selected.append(index)
    return selected


def study_event(
    frame: pd.DataFrame,
    mask: pd.Series,
    event: str,
    pair: str,
    side: str,
    min_samples: int,
    regime_label: str | None = None,
) -> EventResult:
    raw_indices = frame.index[mask.fillna(False)].to_list()
    indices = decluster_indices(raw_indices)
    if not indices:
        return EventResult(
            event=event, pair=pair, side=side, samples=0,
            win_rate_3=None, win_rate_6=None, win_rate_12=None,
            mean_ret_3=None, mean_ret_6=None, mean_ret_12=None,
            median_ret_6=None, mean_mfe_12=None, mean_mae_12=None,
            mfe_mae_ratio_12=None, verdict="no_sample", notes="No independent events.",
            raw_samples=len(raw_indices),
        )

    signed = 1.0 if side == "long" else -1.0
    rows: list[dict[str, float]] = []
    for idx in indices:
        if idx + 12 >= len(frame):
            continue
        entry_price = float(frame.at[idx + 1, "open"])
        future = frame.iloc[idx + 1 : idx + 13]
        ret_3 = signed * (float(frame.at[idx + 3, "close"]) / entry_price - 1.0)
        ret_6 = signed * (float(frame.at[idx + 6, "close"]) / entry_price - 1.0)
        ret_12 = signed * (float(frame.at[idx + 12, "close"]) / entry_price - 1.0)
        if side == "long":
            mfe_12 = max(float(future["high"].max()) / entry_price - 1.0, 0.0)
            mae_12 = max(1.0 - float(future["low"].min()) / entry_price, 0.0)
        else:
            mfe_12 = max(1.0 - float(future["low"].min()) / entry_price, 0.0)
            mae_12 = max(float(future["high"].max()) / entry_price - 1.0, 0.0)
        rows.append(
            {
                "ret_3": ret_3,
                "ret_6": ret_6,
                "ret_12": ret_12,
                "mfe_12": mfe_12,
                "mae_12": mae_12,
                "regime_window": frame.at[idx, "regime_window"] if "regime_window" in frame else None,
            }
        )

    stats = pd.DataFrame(rows)
    if stats.empty:
        return EventResult(
            event=event, pair=pair, side=side, samples=0,
            win_rate_3=None, win_rate_6=None, win_rate_12=None,
            mean_ret_3=None, mean_ret_6=None, mean_ret_12=None,
            median_ret_6=None, mean_mfe_12=None, mean_mae_12=None,
            mfe_mae_ratio_12=None, verdict="no_forward_window", notes="Events exist only near data end.",
            raw_samples=len(raw_indices),
        )

    mean_mae = float(stats["mae_12"].mean())
    mean_mfe = float(stats["mfe_12"].mean())
    ratio = mean_mfe / mean_mae if mean_mae > 0 else None
    mean_ret_6 = float(stats["ret_6"].mean())
    win_rate_6 = float((stats["ret_6"] > 0).mean())
    after_cost = stats["ret_6"] - REALISTIC_ROUND_TRIP_FRICTION
    mean_after_cost = float(after_cost.mean())
    net_win_rate = float((after_cost > 0).mean())
    positive_windows = 0
    if regime_label:
        positive_windows = sum(
            float(group["ret_6"].mean()) > REALISTIC_ROUND_TRIP_FRICTION
            for _, group in stats.dropna(subset=["regime_window"]).groupby("regime_window")
        )
    gross_pass = (
        len(stats) >= min_samples
        and mean_ret_6 > REALISTIC_ROUND_TRIP_FRICTION
        and ratio is not None
        and ratio > 1.15
        and win_rate_6 > 0.50
    )
    cost_pass = gross_pass and mean_after_cost > 0 and net_win_rate > 0.52
    verdict = "reject_gross_edge"
    notes = "Gross forward distribution does not clear the signal edge gate."
    if len(stats) < min_samples:
        verdict = "thin_sample"
        notes = f"Only {len(stats)} independent episodes; do not generate strategy yet."
    elif gross_pass and not cost_pass:
        verdict = "reject_realistic_cost"
        notes = "Gross edge exists, but realistic fee/slippage removes it."
    elif cost_pass and regime_label and positive_windows < 2:
        verdict = "needs_independent_window"
        notes = "Cost-positive event has not replicated in two independent regime windows."
    elif cost_pass:
        verdict = "edge_candidate"
        notes = "Event clears gross edge, realistic cost, and independent-window gates."

    return EventResult(
        event=event,
        pair=pair,
        side=side,
        samples=int(len(stats)),
        win_rate_3=float((stats["ret_3"] > 0).mean()),
        win_rate_6=win_rate_6,
        win_rate_12=float((stats["ret_12"] > 0).mean()),
        mean_ret_3=float(stats["ret_3"].mean()),
        mean_ret_6=mean_ret_6,
        mean_ret_12=float(stats["ret_12"].mean()),
        median_ret_6=float(stats["ret_6"].median()),
        mean_mfe_12=mean_mfe,
        mean_mae_12=mean_mae,
        mfe_mae_ratio_12=ratio,
        verdict=verdict,
        notes=notes,
        raw_samples=len(raw_indices),
        mean_after_cost_6=mean_after_cost,
        net_win_rate_6=net_win_rate,
        gross_gate="pass" if gross_pass else "fail",
        cost_gate="pass" if cost_pass else ("fail" if gross_pass else "not_evaluated"),
        positive_independent_windows=positive_windows,
    )


def run_event_study(
    pairs: list[str],
    timeframe: str,
    min_samples: int,
    pair_scope: str,
    regime_label: str | None,
    research_target: ResearchTarget | None = None,
) -> dict[str, Any]:
    results: list[EventResult] = []
    for pair in pairs:
        frame = add_indicators(load_pair(pair, timeframe), timeframe)
        frame["regime_window"] = pd.NA
        if regime_label:
            horizon_delta = pd.to_timedelta(12 * timeframe_minutes(timeframe), unit="m")
            for window in active_windows_for_label(regime_label):
                start = pd.Timestamp(window["start"], tz="UTC")
                end = pd.Timestamp(window["end"], tz="UTC") + pd.Timedelta(days=1)
                frame.loc[(frame["date"] >= start) & ((frame["date"] + horizon_delta) < end), "regime_window"] = window["name"]
        regime_mask = (
            regime_entry_mask(frame, regime_label, 12, timeframe)
            if regime_label
            else pd.Series(True, index=frame.index)
        )
        for event_name, mask in event_masks(frame).items():
            side = "short" if event_name.endswith("_short") else "long"
            if research_target:
                if side not in research_target.allowed_sides:
                    continue
                if not (EVENT_FAMILIES.get(event_name, set()) & set(research_target.family_codes)):
                    continue
            results.append(
                study_event(frame, mask & regime_mask, event_name, pair, side, min_samples, regime_label)
            )
    return {
        "generated_at_utc": utc_stamp(),
        "timeframe": timeframe,
        "pair_scope": pair_scope,
        "regime_label": regime_label,
        "research_target": research_target.as_dict() if research_target else None,
        "regime_windows": [
            item["name"] for item in active_windows_for_label(regime_label)
        ] if regime_label else [],
        "pairs": pairs,
        "min_samples": min_samples,
        "execution_label_contract": {
            "signal_information": "completed_signal_candle_only",
            "entry_price": "next_candle_open",
            "entry_lag_bars": 1,
            "excursion_window": "next_candle_through_signal_plus_12",
            "mae_floor": 0.0,
        },
        "edge_gate": {
            "sequence": ["gross_edge", "realistic_cost", "independent_regime_windows"],
            "samples_at_least": min_samples,
            "mean_ret_6_gt": REALISTIC_ROUND_TRIP_FRICTION,
            "mfe_mae_ratio_12_gt": 1.15,
            "gross_win_rate_6_gt": 0.50,
            "net_win_rate_6_gt": 0.52,
            "round_trip_friction": REALISTIC_ROUND_TRIP_FRICTION,
            "positive_independent_windows": 2,
        },
        "results": [asdict(result) for result in results],
    }


def fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.3f}%"


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines = [
        "# Event Study Report",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Timeframe: `{payload['timeframe']}`",
        f"- Pair scope: `{payload['pair_scope']}`",
        f"- Regime label: `{payload['regime_label'] or 'all'}`",
        f"- Active regime windows: `{', '.join(payload['regime_windows']) or 'all data'}`",
        f"- Pairs: `{', '.join(payload['pairs'])}`",
        "- Extension pairs are research-generalization evidence only; they do not enter dry-run or registry without separate gates.",
        "- Event returns enter at the next candle open after the completed signal candle.",
        "",
        "| Event | Pair | Side | Raw | Independent | Gross Win 6 | Gross Ret 6 | Net Ret 6 | MFE/MAE 12 | Windows | Verdict | Notes |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["results"]:
        ratio = "" if row["mfe_mae_ratio_12"] is None else f"{row['mfe_mae_ratio_12']:.3f}"
        lines.append(
            f"| {row['event']} | {row['pair']} | {row['side']} | {row['raw_samples']} | {row['samples']} | "
            f"{fmt_pct(row['win_rate_6'])} | {fmt_pct(row['mean_ret_6'])} | {fmt_pct(row['mean_after_cost_6'])} | {ratio} | "
            f"{row['positive_independent_windows']} | "
            f"{row['verdict']} | {row['notes']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair-scope",
        choices=["core", "extension", "research_all"],
        default="core",
        help="Pair universe to evaluate when --pairs is not provided.",
    )
    parser.add_argument("--pairs", nargs="+", default=None, help="Explicit pair override.")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--min-samples", type=int, default=24)
    parser.add_argument(
        "--regime-label",
        choices=REGIME_LABELS,
        default=None,
        help="Evaluate events only inside active data-derived regime windows.",
    )
    parser.add_argument("--auto-target", action="store_true", help="Use the independent research allocator regime/family/side.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs = args.pairs if args.pairs is not None else pairs_for_scope(args.pair_scope)
    pair_scope = "explicit" if args.pairs is not None else args.pair_scope
    target = load_current_research_target() if args.auto_target else None
    if target and target.action == "no_trade" and args.regime_label is None:
        raise SystemExit(f"Research allocator selected no target: {target.reason}")
    regime_label = args.regime_label or (target.regime_label if target else None)
    payload = run_event_study(
        pairs,
        args.timeframe,
        args.min_samples,
        pair_scope,
        regime_label,
        target,
    )
    json_path = OUTPUT_DIR / f"{output_stem(payload)}.json"
    md_path = OUTPUT_DIR / f"{output_stem(payload)}.md"
    rendered_json = json.dumps(payload, indent=2, ensure_ascii=False)
    json_path.write_text(rendered_json, encoding="utf-8")
    write_markdown(payload, md_path)
    publish_report(
        payload=payload,
        json_path=json_path,
        md_path=md_path,
        index_path=INDEX_JSON,
        artifact_type="event_study",
        generic_latest_json=LATEST_JSON,
        generic_latest_md=LATEST_MD,
        publish_current=bool(regime_label),
    )
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Wrote {rel(LATEST_JSON)}")
    print(f"Wrote {rel(LATEST_MD)}")


if __name__ == "__main__":
    main()
