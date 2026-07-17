#!/usr/bin/env python3
"""Research-only current market-state to strategy-family router.

This script does not change registry, dry-run, or live config.  It reads local
Binance USDT-M futures OHLCV and publishes a current-state report that explains
which strategy families should be enabled, watched, or kept off.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from research_target import resolve_target_from_payload


PAIRS = ["BTC_USDT_USDT", "ETH_USDT_USDT"]
PAIR_LABEL = {"BTC_USDT_USDT": "BTC/USDT:USDT", "ETH_USDT_USDT": "ETH/USDT:USDT"}
TIMEFRAMES = ["5m", "15m"]
WINDOW_DAYS = [5, 15, 30, 65]


def find_repo_root() -> Path:
    for start in [Path.cwd(), Path(__file__).resolve()]:
        for path in [start, *start.parents]:
            if (path / "user_data/data/binance/futures").exists() and (path / "pyproject.toml").exists():
                return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
DATA_DIR = REPO_ROOT / "user_data/data/binance/futures"
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/reports"
REGIME_MANIFEST = REPO_ROOT / "user_data/strategy_research/regime_windows/latest_regime_windows.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def read_ohlcv(pair: str, timeframe: str) -> pd.DataFrame:
    path = DATA_DIR / f"{pair}-{timeframe}-futures.feather"
    frame = pd.read_feather(path, columns=["date", "open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.sort_values("date").drop_duplicates("date").set_index("date")
    return frame


def daily_from(frame: pd.DataFrame) -> pd.DataFrame:
    daily = frame.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    daily = daily.dropna(subset=["open", "high", "low", "close"]).copy()
    daily["ret_1d"] = daily["close"].pct_change()
    prev_close = daily["close"].shift(1)
    true_range = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - prev_close).abs(),
            (daily["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    daily["atr_pct"] = true_range.ewm(alpha=1 / 14, adjust=False).mean() / daily["close"]
    daily["realized_vol_15d"] = daily["ret_1d"].rolling(15, min_periods=8).std() * math.sqrt(365)
    daily["realized_vol_30d"] = daily["ret_1d"].rolling(30, min_periods=15).std() * math.sqrt(365)
    daily["ema30"] = daily["close"].ewm(span=30, adjust=False).mean()
    daily["ema60"] = daily["close"].ewm(span=60, adjust=False).mean()
    daily["ema120"] = daily["close"].ewm(span=120, adjust=False).mean()
    daily["ema30_120_gap"] = daily["ema30"] / daily["ema120"] - 1
    mid = daily["close"].rolling(20, min_periods=12).mean()
    std = daily["close"].rolling(20, min_periods=12).std()
    daily["bb_width"] = (4 * std) / mid
    daily["bb_width_pctile"] = daily["bb_width"].rank(pct=True)
    daily["vol_pctile"] = daily["realized_vol_30d"].rank(pct=True)
    daily["atr_pctile"] = daily["atr_pct"].rank(pct=True)
    daily["trend_eff_30d"] = (
        (daily["close"] - daily["close"].shift(30)).abs()
        / daily["close"].diff().abs().rolling(30, min_periods=15).sum()
    )
    return daily


def window_return(daily: pd.DataFrame, days: int) -> float:
    if len(daily) <= days:
        return float("nan")
    return float(daily["close"].iloc[-1] / daily["close"].iloc[-days - 1] - 1)


def recent_high_low_position(frame: pd.DataFrame, days: int) -> float:
    end = frame.index.max()
    start = end - pd.Timedelta(days=days)
    recent = frame.loc[frame.index >= start]
    if recent.empty:
        return float("nan")
    low = float(recent["low"].min())
    high = float(recent["high"].max())
    close = float(recent["close"].iloc[-1])
    if high <= low:
        return 0.5
    return (close - low) / (high - low)


def intraday_structure(frame: pd.DataFrame, days: int) -> dict[str, float]:
    end = frame.index.max()
    start = end - pd.Timedelta(days=days)
    recent = frame.loc[frame.index >= start].copy()
    if len(recent) < 20:
        return {"green_share": 0.0, "range_pct": 0.0, "position": 0.5, "volume_ratio": 1.0}
    green_share = float((recent["close"] > recent["open"]).mean())
    range_pct = float((recent["high"].max() - recent["low"].min()) / recent["close"].iloc[-1])
    position = recent_high_low_position(frame, days)
    volume_ratio = float(recent["volume"].tail(96).mean() / max(recent["volume"].mean(), 1e-12))
    return {
        "green_share": green_share,
        "range_pct": range_pct,
        "position": position,
        "volume_ratio": volume_ratio,
    }


@dataclass
class FamilyDecision:
    family_code: str
    family: str
    status: str
    reason: str
    evidence: str
    next_research_action: str


def classify_state(features: dict[str, Any]) -> tuple[str, str]:
    ret5 = features["combined"]["ret_5d"]
    ret30 = features["combined"]["ret_30d"]
    ret65 = features["combined"]["ret_65d"]
    ema_gap = features["combined"]["ema30_120_gap"]
    vol_pct = features["combined"]["vol_pctile"]
    bb_pct = features["combined"]["bb_width_pctile"]
    trend_eff = features["combined"]["trend_eff_30d"]
    pos5 = features["combined"]["position_5d"]
    green5 = features["combined"]["green_share_5d"]

    if ret30 < -0.08 and ret65 < -0.12:
        if ret5 > 0.035 and pos5 > 0.65 and green5 > 0.52:
            return (
                "bear_relief_or_mixed",
                "30/65d remain bearish, but latest5 shows relief strength; short families need permission, long families are research-only.",
            )
        return (
            "bear_continuation",
            "30/65d are bearish and latest5 does not show a strong relief rally; A1/C1/D1 can be considered only if their own event appears.",
        )
    if ret30 > 0.08 and ema_gap > 0.02:
        return ("bull_trend", "30d trend and EMA structure are positive; C2/A2/D2 home-regime research is relevant.")
    if abs(ret30) < 0.06 and trend_eff < 0.22 and bb_pct < 0.65:
        return ("range_or_compression", "30d return and trend efficiency are muted; B/E research is relevant, but only with cost-positive events.")
    if vol_pct > 0.82:
        return ("high_vol_mixed", "Volatility percentile is high; only breakout-continuation modules with explicit direction and guards should be tested.")
    return ("mixed_unknown", "Signals conflict; default to defense/no-trade until an event-specific edge appears.")


def family_decisions(features: dict[str, Any], state: str) -> list[FamilyDecision]:
    c = features["combined"]
    e = (
        f"ret5={c['ret_5d']*100:.2f}%, ret30={c['ret_30d']*100:.2f}%, "
        f"ret65={c['ret_65d']*100:.2f}%, vol_pct={c['vol_pctile']:.2f}, "
        f"bb_pct={c['bb_width_pctile']:.2f}, pos5={c['position_5d']:.2f}"
    )
    decisions: list[FamilyDecision] = []

    relief = state == "bear_relief_or_mixed"
    bearish = state in {"bear_continuation", "bear_relief_or_mixed"}
    bull = state == "bull_trend"
    range_like = state == "range_or_compression"
    high_vol = state == "high_vol_mixed"

    decisions.append(
        FamilyDecision(
            "A1",
            "downtrend_failed_bounce_short",
            "conditional_watch" if bearish and not relief else "off_or_wait",
            "A1 needs failed-bounce evidence. Current relief/mixed state should block blind shorts." if relief else "Bear state is compatible, but only strategy event can trigger.",
            e,
            "Do not micro-tune A1; require fresh failed-bounce event plus external permission before any new entry.",
        )
    )
    decisions.append(
        FamilyDecision(
            "C1",
            "downtrend_pullback_short",
            "conditional_watch" if bearish and not relief else "off_or_wait",
            (
                "Bear continuation is compatible, but C1 still requires its own 15m "
                "pullback-resume event and completed-1h ETH/BTC downtrend confirmation."
                if bearish and not relief
                else "C1 is bear-home only; relief, range, bull, or mixed states block its short entry."
            ),
            e,
            "Keep the frozen C1 signal; enable it only when both bear-router permission and its own event agree.",
        )
    )
    decisions.append(
        FamilyDecision(
            "D1",
            "downside_breakout_continuation_short",
            "conditional_watch" if bearish and c["ret_5d"] <= 0 else "off_or_wait",
            "D1 needs downside expansion; latest relief or non-breakdown state means no chase.",
            e,
            "Search only for confirmed breakdown/volatility-expansion events; otherwise abstain.",
        )
    )
    decisions.append(
        FamilyDecision(
            "C2",
            "uptrend_pullback_long",
            "research_watch" if bull else ("future_bull_module_only" if c["ret_5d"] > 0 else "off"),
            "C2 ETH 5m momentum-resume has thin bull-only evidence; current state is not confirmed bull.",
            e,
            "Keep ETH 5m C2 as watchlist; validate only behind bull router and more samples.",
        )
    )
    decisions.append(
        FamilyDecision(
            "A2",
            "uptrend_failed_pullback_long",
            "off" if not bull else "research_only",
            "Prior A2 events failed home-window cost gates; do not synthesize until new event evidence appears.",
            e,
            "Redesign A2 event definitions from factor/event evidence, not mirror-A1 logic.",
        )
    )
    decisions.append(
        FamilyDecision(
            "B",
            "range_mean_reversion",
            "research_only" if range_like else "off",
            "B range lifecycle tests failed cost gates; range state alone is insufficient.",
            e,
            "Only revisit after a range event clears high-fee/stress event study.",
        )
    )
    decisions.append(
        FamilyDecision(
            "E",
            "volatility_compression_directional_expansion",
            "research_watch" if range_like or c["bb_width_pctile"] < 0.35 else "off_or_wait",
            "Compression needs a directional release event; current state alone is not an entry.",
            e,
            "Run compression-to-direction event discovery if bb/vol compression persists.",
        )
    )
    decisions.append(
        FamilyDecision(
            "F",
            "defense_no_trade",
            "active" if state in {"bear_relief_or_mixed", "mixed_unknown"} else "available",
            "When router confidence is mixed or target-family event is absent, no-trade is a valid decision.",
            e,
            "Use no-trade as the default unless a family-specific event and gate both pass.",
        )
    )
    if high_vol:
        decisions.append(
            FamilyDecision(
                "D2/D1",
                "high_vol_breakout_continuation",
                "research_only",
                "High volatility can favor continuation, but direction must be confirmed.",
                e,
                "Run high-vol directional breakout event study with strict false-break controls.",
            )
        )
    return decisions


def load_manifest_context() -> dict[str, Any]:
    if not REGIME_MANIFEST.exists():
        return {"available": False, "path": rel(REGIME_MANIFEST)}
    data = json.loads(REGIME_MANIFEST.read_text())
    windows = data.get("windows", [])
    return {
        "available": True,
        "path": rel(REGIME_MANIFEST),
        "generated_utc": data.get("generated_utc"),
        "windows": [
            {
                "label": item.get("label"),
                "name": item.get("name"),
                "timerange": item.get("timerange"),
                "confidence": item.get("confidence"),
                "label_share": item.get("label_share"),
            }
            for item in windows
        ],
    }


def build_features() -> dict[str, Any]:
    pair_features: dict[str, Any] = {}
    source_ranges: list[dict[str, str]] = []
    combined_rows: list[dict[str, float]] = []

    for pair in PAIRS:
        frame_5m = read_ohlcv(pair, "5m")
        frame_15m = read_ohlcv(pair, "15m")
        daily = daily_from(frame_15m)
        latest = daily.iloc[-1]
        row: dict[str, Any] = {
            "pair": PAIR_LABEL[pair],
            "data_end": frame_5m.index.max().isoformat(),
            "close": float(frame_5m["close"].iloc[-1]),
            "ema30_120_gap": float(latest["ema30_120_gap"]),
            "vol_pctile": float(latest["vol_pctile"]),
            "atr_pctile": float(latest["atr_pctile"]),
            "bb_width_pctile": float(latest["bb_width_pctile"]),
            "trend_eff_30d": float(latest["trend_eff_30d"]),
        }
        for days in WINDOW_DAYS:
            row[f"ret_{days}d"] = window_return(daily, days)
            row[f"position_{days}d"] = recent_high_low_position(frame_5m, days)
            row.update({f"{key}_{days}d": value for key, value in intraday_structure(frame_5m, days).items()})
        pair_features[pair] = row
        source_ranges.append(
            {
                "pair": PAIR_LABEL[pair],
                "5m_start": frame_5m.index.min().isoformat(),
                "5m_end": frame_5m.index.max().isoformat(),
                "15m_start": frame_15m.index.min().isoformat(),
                "15m_end": frame_15m.index.max().isoformat(),
            }
        )
        combined_rows.append(row)

    combined: dict[str, float] = {}
    for key in [
        "ema30_120_gap",
        "vol_pctile",
        "atr_pctile",
        "bb_width_pctile",
        "trend_eff_30d",
        *[f"ret_{days}d" for days in WINDOW_DAYS],
        *[f"position_{days}d" for days in WINDOW_DAYS],
        *[f"green_share_{days}d" for days in WINDOW_DAYS],
        *[f"range_pct_{days}d" for days in WINDOW_DAYS],
        *[f"volume_ratio_{days}d" for days in WINDOW_DAYS],
    ]:
        values = [float(row[key]) for row in combined_rows if pd.notna(row.get(key))]
        combined[key] = float(sum(values) / len(values)) if values else float("nan")

    return {"pairs": pair_features, "combined": combined, "sources": source_ranges}


def write_outputs(payload: dict[str, Any], decisions: list[FamilyDecision], ts: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"current_market_state_family_router_{ts}.json"
    md_path = REPORT_DIR / f"current_market_state_family_router_{ts}.md"
    csv_path = REPORT_DIR / f"current_market_state_family_router_{ts}.csv"
    latest_json = REPORT_DIR / "latest_current_market_state_family_router.json"
    latest_md = REPORT_DIR / "latest_current_market_state_family_router.md"
    latest_csv = REPORT_DIR / "latest_current_market_state_family_router.csv"

    rows = [asdict(item) for item in decisions]
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    pd.DataFrame(rows).to_csv(latest_csv, index=False)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    c = payload["features"]["combined"]
    lines = [
        "# Current Market State Family Router",
        "",
        f"- Generated UTC: `{payload['generated_utc']}`",
        "- Scope: research-only; no registry, dry-run, live config, or strategy code changed.",
        "- Market: Binance USDT-M futures BTC/ETH, 5m/15m local data.",
        "- Fixed risk context: isolated 50x, ROI `{0:1.20,180:1.50,360:1.00}`, stoploss `-0.60`.",
        f"- Current state: `{payload['current_state']}`",
        f"- State reason: {payload['state_reason']}",
        f"- Deployment target: regime `{(payload.get('deployment_target') or {}).get('regime_label') or 'none'}`, "
        f"families `{', '.join((payload.get('deployment_target') or {}).get('family_codes') or []) or 'no-trade'}`",
        "- Research allocation is independent and is written by `research_family_allocator.py`.",
        "",
        "## Combined Evidence",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key in [
        "ret_5d",
        "ret_15d",
        "ret_30d",
        "ret_65d",
        "ema30_120_gap",
        "vol_pctile",
        "atr_pctile",
        "bb_width_pctile",
        "trend_eff_30d",
        "green_share_5d",
        "position_5d",
        "volume_ratio_5d",
    ]:
        value = c.get(key)
        if key.startswith("ret_") or key == "ema30_120_gap":
            display = f"{value * 100:.4f}%"
        else:
            display = f"{value:.4f}"
        lines.append(f"| `{key}` | {display} |")

    lines.extend(["", "## Pair Evidence", "", "| pair | close | data end | ret5 | ret30 | ret65 | ema gap | vol pct | pos5 | green5 |", "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"])
    for row in payload["features"]["pairs"].values():
        lines.append(
            "| {pair} | {close:.4f} | {end} | {ret5:.2f}% | {ret30:.2f}% | {ret65:.2f}% | {gap:.2f}% | {vol:.2f} | {pos:.2f} | {green:.2f} |".format(
                pair=row["pair"],
                close=row["close"],
                end=row["data_end"],
                ret5=row["ret_5d"] * 100,
                ret30=row["ret_30d"] * 100,
                ret65=row["ret_65d"] * 100,
                gap=row["ema30_120_gap"] * 100,
                vol=row["vol_pctile"],
                pos=row["position_5d"],
                green=row["green_share_5d"],
            )
        )

    lines.extend(["", "## Family Router Decision", "", "| code | family | status | reason | next research action |", "|---|---|---|---|---|"])
    for item in decisions:
        lines.append(
            f"| {item.family_code} | `{item.family}` | `{item.status}` | {item.reason} | {item.next_research_action} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This report is a router research artifact, not a trading signal.",
            "- `off_or_wait` and `active` no-trade states are valid outcomes when the family-specific event is absent.",
            "- A family can move from router watch to strategy synthesis only after event-study edge, Freqtrade execution alignment, cost stress, walk-forward, family-risk, promotion, recursive, and lookahead gates.",
            "",
            "## Files",
            "",
            f"- JSON: `{rel(json_path)}`",
            f"- CSV: `{rel(csv_path)}`",
        ]
    )
    md_text = "\n".join(lines) + "\n"
    md_path.write_text(md_text)
    latest_md.write_text(md_text)


def main() -> int:
    ts = now_utc()
    features = build_features()
    state, reason = classify_state(features)
    decisions = family_decisions(features, state)
    payload = {
        "generated_utc": ts,
        "research_only": True,
        "current_state": state,
        "state_reason": reason,
        "features": features,
        "regime_manifest": load_manifest_context(),
        "family_decisions": [asdict(item) for item in decisions],
    }
    payload["deployment_target"] = resolve_target_from_payload(payload).as_dict()
    write_outputs(payload, decisions, ts)
    print(REPORT_DIR / f"current_market_state_family_router_{ts}.md")
    print(REPORT_DIR / "latest_current_market_state_family_router.md")
    print(state)
    print(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
