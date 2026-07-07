#!/usr/bin/env python3
"""Evaluate non-oracle A1 regime classifier candidates on executed trades.

This is a research-only allocator screen. It does not generate strategy code
and does not alter dry-run/live configuration. The purpose is to test whether
simple, interpretable BTC/ETH market-state permissions can route the existing
A1 module before we embed any classifier into Freqtrade strategy code.
"""

from __future__ import annotations

import csv
import json
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from cost_model import PRIMARY_SCENARIO, STRESS_SCENARIO_NAME


def find_repo_root() -> Path:
    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/reports"
DATA_DIR = REPO_ROOT / "user_data/data/binance/futures"
STRATEGY = "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060"
STARTING_BALANCE = 1000.0


def latest_source_csv() -> Path:
    candidates = sorted(
        REPORT_DIR.glob("a1_range_bull_abstain_experiment_*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No a1_range_bull_abstain_experiment_*.csv report found")
    return candidates[0]


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def read_result_json(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        name = next(
            n
            for n in zf.namelist()
            if n.endswith(".json") and not n.endswith("_config.json") and not n.endswith(".meta.json")
        )
        return json.loads(zf.read(name))


def resample_ohlcv(path: Path, rule: str) -> pd.DataFrame:
    frame = pd.read_feather(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.set_index("date").sort_index()
    out = pd.DataFrame(
        {
            "open": frame["open"].resample(rule).first(),
            "high": frame["high"].resample(rule).max(),
            "low": frame["low"].resample(rule).min(),
            "close": frame["close"].resample(rule).last(),
            "volume": frame["volume"].resample(rule).sum(),
        }
    ).dropna()
    return out.reset_index()


def add_features(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"]
    returns = close.pct_change()
    out[f"{prefix}_ret_12h"] = close / close.shift(12) - 1.0
    out[f"{prefix}_ret_24h"] = close / close.shift(24) - 1.0
    out[f"{prefix}_ret_72h"] = close / close.shift(72) - 1.0
    out[f"{prefix}_ret_30d"] = close / close.shift(24 * 30) - 1.0
    out[f"{prefix}_ret_60d"] = close / close.shift(24 * 60) - 1.0
    ema30 = close.ewm(span=30, adjust=False).mean()
    ema120 = close.ewm(span=120, adjust=False).mean()
    out[f"{prefix}_ema30_120_gap"] = ema30 / ema120 - 1.0
    out[f"{prefix}_rv_30d"] = returns.rolling(24 * 30).std()
    distance = (close - close.shift(24 * 30)).abs()
    path = close.diff().abs().rolling(24 * 30).sum()
    out[f"{prefix}_trend_eff_30d"] = distance / path.replace(0, pd.NA)
    out[f"{prefix}_dist_30d_high"] = close / out["high"].shift(1).rolling(24 * 30).max() - 1.0
    out[f"{prefix}_dist_30d_low"] = close / out["low"].shift(1).rolling(24 * 30).min() - 1.0
    return out


def load_features() -> pd.DataFrame:
    btc = add_features(resample_ohlcv(DATA_DIR / "BTC_USDT_USDT-1m-futures.feather", "1h"), "btc")
    eth = add_features(resample_ohlcv(DATA_DIR / "ETH_USDT_USDT-1m-futures.feather", "1h"), "eth")
    btc_cols = ["date"] + [col for col in btc.columns if col.startswith("btc_")]
    eth_cols = ["date"] + [col for col in eth.columns if col.startswith("eth_")]
    features = btc[btc_cols].merge(eth[eth_cols], on="date", how="outer").sort_values("date")
    features["combined_ret_24h"] = (features["btc_ret_24h"] + features["eth_ret_24h"]) / 2.0
    features["combined_ret_72h"] = (features["btc_ret_72h"] + features["eth_ret_72h"]) / 2.0
    features["combined_ret_30d"] = (features["btc_ret_30d"] + features["eth_ret_30d"]) / 2.0
    features["combined_ret_60d"] = (features["btc_ret_60d"] + features["eth_ret_60d"]) / 2.0
    features["combined_ema_gap"] = (features["btc_ema30_120_gap"] + features["eth_ema30_120_gap"]) / 2.0
    features["combined_rv_30d"] = (features["btc_rv_30d"] + features["eth_rv_30d"]) / 2.0
    features["combined_rv_30d_q70"] = features["combined_rv_30d"].rolling(24 * 365, min_periods=24 * 120).quantile(0.70)
    features["combined_rv_30d_q80"] = features["combined_rv_30d"].rolling(24 * 365, min_periods=24 * 120).quantile(0.80)
    features["combined_rv_30d_q85"] = features["combined_rv_30d"].rolling(24 * 365, min_periods=24 * 120).quantile(0.85)
    features["combined_rv_30d_q90"] = features["combined_rv_30d"].rolling(24 * 365, min_periods=24 * 120).quantile(0.90)
    features["combined_trend_eff_30d"] = (
        features["btc_trend_eff_30d"] + features["eth_trend_eff_30d"]
    ) / 2.0
    features["btc_eth_same_30d_direction"] = (
        (features["btc_ret_30d"] > 0) == (features["eth_ret_30d"] > 0)
    ).astype(float)
    features["date"] = pd.to_datetime(features["date"], utc=True).astype("datetime64[ns, UTC]")
    return features


def collect_trades() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    source_csv = latest_source_csv()
    with source_csv.open(newline="", encoding="utf-8") as fh:
        for summary in csv.DictReader(fh):
            if summary["strategy"] != STRATEGY:
                continue
            if not (
                "manifest_bear" in summary["window"]
                or "manifest_bull" in summary["window"]
                or "manifest_range" in summary["window"]
                or "manifest_high_vol" in summary["window"]
                or summary["window"].startswith("wf_bear_")
                or summary["window"] == "latest5"
            ):
                continue
            key = (
                summary["artifact"],
                summary["strategy"],
                summary["scenario"],
                summary["window"],
                summary["timerange"],
            )
            if key in seen:
                continue
            seen.add(key)
            payload = read_result_json(REPO_ROOT / summary["artifact"])
            trades = payload["strategy"][STRATEGY]["trades"]
            slippage_bps = float(summary["slippage_bps"])
            for trade in trades:
                stake = float(trade.get("stake_amount") or 0.0)
                leverage = float(trade.get("leverage") or 1.0)
                slippage_abs = stake * leverage * slippage_bps / 10000.0
                profit_abs = float(trade.get("profit_abs") or 0.0)
                rows.append(
                    {
                        "scenario": summary["scenario"],
                        "window": summary["window"],
                        "timerange": summary["timerange"],
                        "pair": trade["pair"],
                        "open_date": pd.to_datetime(trade["open_date"], utc=True),
                        "close_date": trade["close_date"],
                        "profit_abs": profit_abs,
                        "account_profit_pct": profit_abs / STARTING_BALANCE * 100.0,
                        "adjusted_account_profit_pct": (profit_abs - slippage_abs) / STARTING_BALANCE * 100.0,
                        "profit_pct": float(trade["profit_ratio"]) * 100.0,
                        "exit_reason": trade.get("exit_reason"),
                        "enter_tag": trade.get("enter_tag"),
                        "stake_amount": stake,
                        "leverage": leverage,
                        "slippage_abs": slippage_abs,
                    }
                )
    if not rows:
        raise RuntimeError(f"No trades found for {STRATEGY} in {source_csv}")
    trades = pd.DataFrame(rows).sort_values(["scenario", "window", "open_date"])
    trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True).astype("datetime64[ns, UTC]")
    return trades


def attach_features(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    return pd.merge_asof(
        trades.sort_values("open_date"),
        features.sort_values("date"),
        left_on="open_date",
        right_on="date",
        direction="backward",
    )


def rule_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    bear_like = (df["combined_ret_60d"] < 0) & (df["combined_ema_gap"] < 0)
    clear_bear = (df["combined_ret_60d"] < -0.10) & (df["combined_ema_gap"] < -0.02)
    non_extreme_vol = df["combined_rv_30d"] <= df["combined_rv_30d_q80"]
    loose_non_extreme_vol = df["combined_rv_30d"] <= df["combined_rv_30d_q85"]
    very_loose_non_extreme_vol = df["combined_rv_30d"] <= df["combined_rv_30d_q90"]
    near_term_down = df["combined_ret_72h"] <= 0
    no_fast_rebound = df["combined_ret_24h"] <= 0.015
    both_30d_down = (df["btc_ret_30d"] < 0) & (df["eth_ret_30d"] < 0)
    return {
        "baseline_no_router": pd.Series(True, index=df.index),
        "oracle_manifest_bear_only": df["window"].str.contains("manifest_bear|wf_bear_|latest5", regex=True),
        "classifier_bear_like": bear_like,
        "classifier_clear_bear": clear_bear,
        "classifier_bear_like_not_highvol": bear_like & non_extreme_vol,
        "classifier_clear_bear_not_highvol": clear_bear & non_extreme_vol,
        "classifier_clear_bear_no_fast_rebound": clear_bear & no_fast_rebound,
        "classifier_clear_bear_near_term_down": clear_bear & near_term_down,
        "classifier_both_30d_down_clear_bear": clear_bear & both_30d_down,
        "classifier_ret30_down_not_highvol_q80": (df["combined_ret_30d"] < 0) & non_extreme_vol,
        "classifier_ret30_down_not_highvol_q85": (df["combined_ret_30d"] < 0) & loose_non_extreme_vol,
        "classifier_ret30_down_not_highvol_q90": (df["combined_ret_30d"] < 0) & very_loose_non_extreme_vol,
    }


def summarize(masked: pd.DataFrame, all_rows: pd.DataFrame, router: str) -> dict[str, Any]:
    def metric(window_contains: str | Callable[[pd.Series], pd.Series], scenario: str) -> tuple[float, int, int]:
        rows = masked[masked["scenario"] == scenario]
        if callable(window_contains):
            rows = rows[window_contains(rows)]
        else:
            rows = rows[rows["window"].str.contains(window_contains, regex=True)]
        return (
            float(rows["adjusted_account_profit_pct"].sum()),
            int(len(rows)),
            int((rows["exit_reason"] == "stop_loss").sum()),
        )

    hostile = masked[
        (masked["scenario"] == STRESS_SCENARIO_NAME)
        & masked["window"].str.contains("manifest_bull|manifest_range|manifest_high_vol", regex=True)
    ]
    hostile_by_window = hostile.groupby("window")["adjusted_account_profit_pct"].sum()
    wf = masked[(masked["scenario"] == PRIMARY_SCENARIO) & masked["window"].str.startswith("wf_bear_")]
    wf_by_window = wf.groupby("window")["adjusted_account_profit_pct"].sum()
    bear_high, bear_high_trades, bear_high_sl = metric("manifest_bear", PRIMARY_SCENARIO)
    bear_stress, bear_stress_trades, bear_stress_sl = metric("manifest_bear", STRESS_SCENARIO_NAME)
    latest_high, latest_high_trades, _ = metric("^latest5$", PRIMARY_SCENARIO)
    latest_stress, latest_stress_trades, _ = metric("^latest5$", STRESS_SCENARIO_NAME)
    return {
        "router": router,
        "bear_high_adj_pct": round(bear_high, 4),
        "bear_high_trades": bear_high_trades,
        "bear_high_stoploss": bear_high_sl,
        "bear_stress_adj_pct": round(bear_stress, 4),
        "bear_stress_trades": bear_stress_trades,
        "bear_stress_stoploss": bear_stress_sl,
        "latest5_high_adj_pct": round(latest_high, 4),
        "latest5_high_trades": latest_high_trades,
        "latest5_stress_adj_pct": round(latest_stress, 4),
        "latest5_stress_trades": latest_stress_trades,
        "hostile_stress_worst_pct": round(float(hostile_by_window.min()) if not hostile_by_window.empty else 0.0, 4),
        "hostile_stress_total_pct": round(float(hostile_by_window.sum()) if not hostile_by_window.empty else 0.0, 4),
        "hostile_stress_trades": int(len(hostile)),
        "wf_high_positive": int((wf_by_window > 0).sum()),
        "wf_high_total": int(len(wf_by_window)),
        "wf_high_worst_pct": round(float(wf_by_window.min()) if not wf_by_window.empty else 0.0, 4),
        "total_kept_trades": int(len(masked)),
        "total_available_trades": int(len(all_rows)),
    }


def verdict(row: dict[str, Any]) -> tuple[str, str]:
    blockers: list[str] = []
    if row["router"] == "oracle_manifest_bear_only":
        blockers.append("oracle comparator only; cannot be deployed")
    if row["bear_high_adj_pct"] <= 30:
        blockers.append("bear high-fee <= 30%")
    if row["bear_stress_adj_pct"] <= 30:
        blockers.append("bear stress <= 30%")
    if row["bear_high_trades"] < 8:
        blockers.append("bear sample < 8")
    if row["latest5_high_trades"] == 0 or row["latest5_high_adj_pct"] <= 0:
        blockers.append("latest5 high-fee not positive")
    if row["hostile_stress_worst_pct"] < -15:
        blockers.append("hostile stress worst < -15%")
    if row["wf_high_total"] and row["wf_high_positive"] < row["wf_high_total"] and row["wf_high_worst_pct"] < -2:
        blockers.append("walk-forward weak")
    if not blockers:
        return "router_research_candidate_needs_strategy_implementation", "passes offline screen; still needs live-computable strategy implementation and full gates"
    return "research_evidence_only", "; ".join(blockers)


def main() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    trades = attach_features(collect_trades(), load_features())
    trade_path = REPORT_DIR / f"a1_regime_classifier_allocator_trades_{ts}.csv"
    trades.to_csv(trade_path, index=False)

    rows: list[dict[str, Any]] = []
    for name, mask in rule_masks(trades).items():
        kept = trades[mask.fillna(False)].copy()
        item = summarize(kept, trades, name)
        status, blockers = verdict(item)
        item["status"] = status
        item["blockers"] = blockers
        rows.append(item)

    summary_path = REPORT_DIR / f"a1_regime_classifier_allocator_{ts}.csv"
    pd.DataFrame(rows).to_csv(summary_path, index=False)

    lines = [
        "# A1 Regime Classifier Allocator Screen",
        "",
        f"Generated UTC: `{ts}`",
        "",
        "Scope: research-only offline router evaluation. No strategy code, registry, dry-run, or live config changes.",
        "",
        f"Strategy module under test: `{STRATEGY}`",
        "",
        "Question: can a non-oracle BTC/ETH market-state classifier decide when A1 should be enabled before we embed it in strategy code?",
        "",
        "Fixed contract inherited from source backtests: Binance USDT-M futures, isolated 50x, ROI={0:1.20,180:1.50,360:1.00}, stoploss=-0.60, 15m entry.",
        "",
        "## Router Readout",
        "",
        "| router | status | bear stress % | bear trades | latest5 stress % | hostile worst % | hostile trades | WF high-fee | blockers |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['router']}` | {row['status']} | {row['bear_stress_adj_pct']:.4f} | "
            f"{row['bear_stress_trades']} | {row['latest5_stress_adj_pct']:.4f} | "
            f"{row['hostile_stress_worst_pct']:.4f} | {row['hostile_stress_trades']} | "
            f"{row['wf_high_positive']}/{row['wf_high_total']} | {row['blockers']} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `oracle_manifest_bear_only` is an upper bound, not a deployable router.",
            "- A useful deployable classifier must keep bear stress above 30%, keep enough bear trades, preserve latest5 opportunity, and reduce hostile range/high-vol losses below the family-risk floor.",
            "- If all non-oracle classifiers either starve bear trades or leave hostile stress below -15%, the next step is a dedicated regime-classifier event study rather than another A1 entry tweak.",
            "",
            f"Trade CSV: `{rel(trade_path)}`",
            f"Summary CSV: `{rel(summary_path)}`",
            "",
        ]
    )
    report_path = REPORT_DIR / f"a1_regime_classifier_allocator_{ts}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(rel(summary_path))
    print(rel(trade_path))
    print(rel(report_path))


if __name__ == "__main__":
    main()
