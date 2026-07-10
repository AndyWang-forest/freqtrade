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
from pair_universe import pairs_for_scope
from regime_window_builder import REGIME_LABELS, active_windows_for_label, regime_entry_mask
from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
DATA_ROOT = REPO_ROOT / "user_data/data/binance/futures"
OUTPUT_DIR = REPO_ROOT / "user_data/strategy_research/event_studies"
LATEST_JSON = OUTPUT_DIR / "latest_event_study.json"
LATEST_MD = OUTPUT_DIR / "latest_event_study.md"


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
    frame["ret_3"] = frame["close"] / frame["close"].shift(3) - 1.0
    frame["ret_12"] = frame["close"] / frame["close"].shift(12) - 1.0
    frame["ret_36"] = frame["close"] / frame["close"].shift(36) - 1.0
    frame["volume_ratio"] = frame["volume"] / frame["volume"].rolling(24).mean()
    frame["atr_q80_24d"] = frame["atr_pct"].rolling(q80_window).quantile(0.80)
    frame["volume_ratio_q80_24d"] = frame["volume_ratio"].rolling(q80_window).quantile(0.80)
    frame["high_36_prev"] = frame["high"].rolling(36).max().shift(1)
    frame["low_36_prev"] = frame["low"].rolling(36).min().shift(1)
    frame["down_count_6"] = (frame["close"] < frame["open"]).rolling(6).sum()
    frame["up_count_6"] = (frame["close"] > frame["open"]).rolling(6).sum()
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
        "pullback_resume_long": pullback_long,
        "pullback_resume_atr_q80_long": pullback_atr_q80_long,
        "pullback_resume_volume_q80_long": pullback_volume_q80_long,
        "pullback_resume_short": pullback_short,
        "false_break_long": false_break_long,
        "false_break_short": false_break_short,
        "second_leg_long": second_leg_long,
        "second_leg_short": second_leg_short,
    }


def study_event(frame: pd.DataFrame, mask: pd.Series, event: str, pair: str, side: str, min_samples: int) -> EventResult:
    indices = frame.index[mask.fillna(False)].to_list()
    if not indices:
        return EventResult(event, pair, side, 0, None, None, None, None, None, None, None, None, None, None, "no_sample", "No events.")

    signed = 1.0 if side == "long" else -1.0
    rows: list[dict[str, float]] = []
    for idx in indices:
        if idx + 12 >= len(frame):
            continue
        close = float(frame.at[idx, "close"])
        future = frame.iloc[idx + 1 : idx + 13]
        ret_3 = signed * (float(frame.at[idx + 3, "close"]) / close - 1.0)
        ret_6 = signed * (float(frame.at[idx + 6, "close"]) / close - 1.0)
        ret_12 = signed * (float(frame.at[idx + 12, "close"]) / close - 1.0)
        if side == "long":
            mfe_12 = float(future["high"].max()) / close - 1.0
            mae_12 = abs(min(float(future["low"].min()) / close - 1.0, 0.0))
        else:
            mfe_12 = close / float(future["low"].min()) - 1.0
            mae_12 = abs(max(float(future["high"].max()) / close - 1.0, 0.0))
        rows.append({"ret_3": ret_3, "ret_6": ret_6, "ret_12": ret_12, "mfe_12": mfe_12, "mae_12": mae_12})

    stats = pd.DataFrame(rows)
    if stats.empty:
        return EventResult(event, pair, side, 0, None, None, None, None, None, None, None, None, None, None, "no_forward_window", "Events exist only near data end.")

    mean_mae = float(stats["mae_12"].mean())
    mean_mfe = float(stats["mfe_12"].mean())
    ratio = mean_mfe / mean_mae if mean_mae > 0 else None
    mean_ret_6 = float(stats["ret_6"].mean())
    win_rate_6 = float((stats["ret_6"] > 0).mean())
    verdict = "reject"
    notes = "Forward distribution does not clear edge gate."
    if len(stats) < min_samples:
        verdict = "thin_sample"
        notes = f"Only {len(stats)} samples; do not generate strategy yet."
    elif mean_ret_6 > 0.001 and ratio is not None and ratio > 1.15 and win_rate_6 > 0.52:
        verdict = "edge_candidate"
        notes = "Event clears initial forward edge gate; strategy generation may be allowed after regime/cost checks."

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
    )


def run_event_study(
    pairs: list[str],
    timeframe: str,
    min_samples: int,
    pair_scope: str,
    regime_label: str | None,
) -> dict[str, Any]:
    results: list[EventResult] = []
    for pair in pairs:
        frame = add_indicators(load_pair(pair, timeframe), timeframe)
        regime_mask = (
            regime_entry_mask(frame, regime_label, 12, timeframe)
            if regime_label
            else pd.Series(True, index=frame.index)
        )
        for event_name, mask in event_masks(frame).items():
            side = "short" if event_name.endswith("_short") else "long"
            results.append(
                study_event(frame, mask & regime_mask, event_name, pair, side, min_samples)
            )
    return {
        "generated_at_utc": utc_stamp(),
        "timeframe": timeframe,
        "pair_scope": pair_scope,
        "regime_label": regime_label,
        "regime_windows": [
            item["name"] for item in active_windows_for_label(regime_label)
        ] if regime_label else [],
        "pairs": pairs,
        "min_samples": min_samples,
        "edge_gate": {
            "samples_at_least": min_samples,
            "mean_ret_6_gt": 0.001,
            "mfe_mae_ratio_12_gt": 1.15,
            "win_rate_6_gt": 0.52,
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
        "",
        "| Event | Pair | Side | Samples | Win 6 | Mean Ret 6 | MFE/MAE 12 | Verdict | Notes |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["results"]:
        ratio = "" if row["mfe_mae_ratio_12"] is None else f"{row['mfe_mae_ratio_12']:.3f}"
        lines.append(
            f"| {row['event']} | {row['pair']} | {row['side']} | {row['samples']} | "
            f"{fmt_pct(row['win_rate_6'])} | {fmt_pct(row['mean_ret_6'])} | {ratio} | "
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
    parser.add_argument("--min-samples", type=int, default=200)
    parser.add_argument(
        "--regime-label",
        choices=REGIME_LABELS,
        default=None,
        help="Evaluate events only inside active data-derived regime windows.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs = args.pairs if args.pairs is not None else pairs_for_scope(args.pair_scope)
    pair_scope = "explicit" if args.pairs is not None else args.pair_scope
    payload = run_event_study(
        pairs,
        args.timeframe,
        args.min_samples,
        pair_scope,
        args.regime_label,
    )
    json_path = OUTPUT_DIR / f"{output_stem(payload)}.json"
    md_path = OUTPUT_DIR / f"{output_stem(payload)}.md"
    rendered_json = json.dumps(payload, indent=2, ensure_ascii=False)
    json_path.write_text(rendered_json, encoding="utf-8")
    LATEST_JSON.write_text(rendered_json, encoding="utf-8")
    write_markdown(payload, md_path)
    LATEST_MD.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Wrote {rel(LATEST_JSON)}")
    print(f"Wrote {rel(LATEST_MD)}")


if __name__ == "__main__":
    main()
