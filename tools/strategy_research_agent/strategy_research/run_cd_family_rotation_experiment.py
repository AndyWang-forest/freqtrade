#!/usr/bin/env python3
"""Shared Freqtrade backtest utilities for C/D-family research.

The original direct runner used hand-picked regime dates and is retired. New
experiments may import the reusable Row/run_backtest/summarize helpers, but must
provide windows from the data-derived regime manifest.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cost_model import DEFAULT_SCENARIOS, PRIMARY_SCENARIO, STRESS_SCENARIO_NAME
from experiment_provenance import register_experiment


def find_repo_root() -> Path:
    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
RESULT_DIR = REPO_ROOT / "user_data/backtest_results/20260701T_cd_family_rotation"
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/reports"
CONFIG = "user_data/config_futures_dryrun.json"
STRATEGY_PATH = "user_data/strategies/research_generated"
TIMEFRAME = "15m"
STARTING_BALANCE = 1000.0

STRATEGY_INFO = {
    "C2UptrendPullbackLongPrototype": {
        "family": "uptrend_pullback_long",
        "logic": "Uptrend EMA stack plus controlled pullback into EMA-mid support and bullish resume candle.",
        "filters": "trend up; controlled pullback; resume close above EMA-fast",
    },
    "C1DowntrendPullbackShortPrototype": {
        "family": "downtrend_pullback_short",
        "logic": "Downtrend EMA stack plus controlled pullback into EMA-mid resistance and bearish resume candle.",
        "filters": "trend down; controlled pullback; resume close below EMA-fast",
    },
    "D2UpsideBreakoutContinuationLongPrototype": {
        "family": "upside_breakout_continuation_long",
        "logic": "Compression, two closed candles above 36-bar high, volume confirmation, ATR above rolling q55.",
        "filters": "compression before breakout; no wick-only breakout; volatility expansion",
    },
    "D1DownsideBreakoutContinuationShortPrototype": {
        "family": "downside_breakout_continuation_short",
        "logic": "Compression, two closed candles below 36-bar low, volume confirmation, ATR above rolling q55.",
        "filters": "compression before breakdown; no wick-only breakdown; volatility expansion",
    },
}
STRATEGIES = list(STRATEGY_INFO)
SCENARIOS = DEFAULT_SCENARIOS
@dataclass
class Row:
    strategy: str
    strategy_family: str
    slice: str
    window: str
    timerange: str
    scenario: str
    fee: float
    slippage_bps: float
    trades: int
    profit_total_pct: float
    adjusted_profit_pct: float
    profit_abs: float
    profit_factor: float
    max_drawdown_pct: float
    wins: int
    losses: int
    roi_exits: int
    time_stop_exits: int
    stop_loss_exits: int
    force_exit_exits: int
    artifact: str


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def run_backtest(timerange: str, fee: float) -> Path:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in RESULT_DIR.glob("*.zip")}
    cmd = [
        str(REPO_ROOT / ".venv/bin/freqtrade"),
        "backtesting",
        "-c",
        CONFIG,
        "--strategy-list",
        *STRATEGIES,
        "--strategy-path",
        STRATEGY_PATH,
        "--timeframe",
        TIMEFRAME,
        "--timerange",
        timerange,
        "--fee",
        str(fee),
        "--cache",
        "none",
        "--export",
        "trades",
        "--enable-protections",
        "--backtest-directory",
        str(RESULT_DIR),
    ]
    env = os.environ.copy()
    env.setdefault("FT_DISABLE_SERVICES", "1")
    offline_path = str(REPO_ROOT / "user_data/offline_exchange")
    env["PYTHONPATH"] = offline_path if not env.get("PYTHONPATH") else f"{offline_path}{os.pathsep}{env['PYTHONPATH']}"
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    after = sorted(
        [p for p in RESULT_DIR.glob("*.zip") if p.name not in before],
        key=lambda p: p.stat().st_mtime,
    )
    if completed.returncode != 0 or not after:
        tail = "\n".join(completed.stdout.splitlines()[-120:])
        raise RuntimeError(f"Backtest failed timerange={timerange} fee={fee}\n{tail}")
    return after[-1]


def load_payload(artifact: Path) -> dict[str, Any]:
    with zipfile.ZipFile(artifact) as zf:
        result_names = [name for name in zf.namelist() if name.endswith(".json")]
        result_name = next(
            (
                name
                for name in result_names
                if not name.endswith("_config.json") and not name.endswith(".meta.json")
            ),
            result_names[0],
        )
        return json.loads(zf.read(result_name))


def summarize(
    payload: dict[str, Any],
    artifact: Path,
    strategy: str,
    slice_name: str,
    window: str,
    timerange: str,
    scenario: str,
    fee: float,
    slippage_bps: float,
) -> Row:
    stats = payload["strategy"].get(strategy, {})
    trades = stats.get("trades", [])
    exits = {
        item.get("key"): int(item.get("trades") or 0)
        for item in (stats.get("exit_reason_summary") or [])
    }
    slippage_abs = sum(
        float(t.get("stake_amount") or 0.0)
        * float(t.get("leverage") or 1.0)
        * slippage_bps
        / 10000.0
        for t in trades
    )
    profit_total_pct = round(float(stats.get("profit_total") or 0.0) * 100, 4)
    adjusted_profit_pct = round(profit_total_pct - (slippage_abs / STARTING_BALANCE * 100), 4)
    return Row(
        strategy=strategy,
        strategy_family=STRATEGY_INFO[strategy]["family"],
        slice=slice_name,
        window=window,
        timerange=timerange,
        scenario=scenario,
        fee=fee,
        slippage_bps=slippage_bps,
        trades=int(stats.get("total_trades") or len(trades)),
        profit_total_pct=profit_total_pct,
        adjusted_profit_pct=adjusted_profit_pct,
        profit_abs=float(stats.get("profit_total_abs") or 0.0),
        profit_factor=float(stats.get("profit_factor") or 0.0),
        max_drawdown_pct=round(float(stats.get("max_drawdown_account") or 0.0) * 100, 4),
        wins=int(stats.get("wins") or 0),
        losses=int(stats.get("losses") or 0),
        roi_exits=exits.get("roi", 0),
        time_stop_exits=exits.get("fr_time_stop_8h_losing", 0),
        stop_loss_exits=exits.get("stop_loss", 0),
        force_exit_exits=exits.get("force_exit", 0),
        artifact=rel(artifact),
    )


def rows_for(rows: list[Row], strategy: str, slice_name: str, scenario: str) -> list[Row]:
    return [
        row
        for row in rows
        if row.strategy == strategy and row.slice == slice_name and row.scenario == scenario
    ]


def verdict_for(rows: list[Row], strategy: str) -> tuple[str, list[str]]:
    raise RuntimeError(
        "Legacy runner-local verdicts are retired. Register the experiment CSV and use family_risk_gate.py."
    )
    high_main = {row.window: row for row in rows_for(rows, strategy, "main", PRIMARY_SCENARIO)}
    stress_main = rows_for(rows, strategy, "main", STRESS_SCENARIO_NAME)
    high_wf = rows_for(rows, strategy, "walk_forward", PRIMARY_SCENARIO)
    stress_regime = rows_for(rows, strategy, "regime", STRESS_SCENARIO_NAME)
    reasons: list[str] = []

    if high_main.get("65d") is None or high_main["65d"].adjusted_profit_pct <= 30.0:
        reasons.append("65d adjusted profit <= 30%")
    if high_main.get("30d") is None or high_main["30d"].adjusted_profit_pct <= 20.0:
        reasons.append("30d adjusted profit <= 20%")
    if high_main.get("latest5") is None or high_main["latest5"].trades == 0 or high_main["latest5"].adjusted_profit_pct <= 0:
        reasons.append("latest5 lacks positive trades")
    if high_main.get("65d") is None or high_main["65d"].trades < 8:
        reasons.append("65d trade count below 8")
    if any(row.adjusted_profit_pct <= -10.0 for row in stress_main):
        reasons.append("stress-cost main window drawdown is below -10%")

    wf_pos = sum(1 for row in high_wf if row.adjusted_profit_pct > 0)
    wf_worst = min((row.adjusted_profit_pct for row in high_wf), default=0.0)
    if wf_pos < len(high_wf) and wf_worst < -2.0:
        reasons.append("walk-forward not 3/3 positive and worst below -2%")

    hostile_worst = min((row.adjusted_profit_pct for row in stress_regime), default=0.0)
    if hostile_worst < -5.0:
        reasons.append("hostile regime stress loss below -5%")
    return ("dryrun_candidate_review_pending_bias_checks" if not reasons else "research_candidate", reasons)


def write_csv(rows: list[Row]) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORT_DIR / f"cd_family_rotation_experiment_{timestamp}.csv"
    fields = list(Row.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
    register_experiment(path, producer="run_cd_family_rotation_experiment.write_csv")
    return path


def write_report(rows: list[Row], csv_path: Path) -> Path:
    raise RuntimeError(
        "Legacy runner-local reports are retired. Use the canonical family-risk/promotion report."
    )
    path = REPORT_DIR / "cd_family_rotation_experiment_20260701T.md"
    lines = [
        "# C/D Strategy Family Rotation Experiment",
        "",
        "Date: 2026-07-01",
        "",
        "Scope: C/D trend pullback and breakout continuation families, BTC/ETH Binance USDT-M futures, isolated margin, fixed 50x, 15m entries, research-only.",
        "",
        "Risk policy fixed: ROI={0:1.20,180:1.50,360:1.00}, stoploss=-0.60. No dry-run/live config changed.",
        "",
        f"Cost policy: `{PRIMARY_SCENARIO}` is the primary edge screen; `{STRESS_SCENARIO_NAME}` is a stress/safety check, not the sole rejection gate.",
        "",
        "## Strategy Logic",
        "",
        "| strategy | family | entry logic | filters |",
        "|---|---|---|---|",
    ]
    for strategy, info in STRATEGY_INFO.items():
        lines.append(f"| `{strategy}` | `{info['family']}` | {info['logic']} | {info['filters']} |")

    lines.extend(
        [
            "",
            "## Main Windows",
            "",
            "| strategy | scenario | 65d adj % | 30d adj % | latest5 adj % | weak_week adj % | 65d trades | 65d stoploss |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for strategy in STRATEGIES:
        for scenario, _, _ in SCENARIOS:
            sr = {row.window: row for row in rows_for(rows, strategy, "main", scenario)}
            lines.append(
                f"| `{strategy}` | {scenario} | {sr['65d'].adjusted_profit_pct:.4f} | "
                f"{sr['30d'].adjusted_profit_pct:.4f} | {sr['latest5'].adjusted_profit_pct:.4f} | "
                f"{sr['weak_week'].adjusted_profit_pct:.4f} | {sr['65d'].trades} | "
                f"{sr['65d'].stop_loss_exits} |"
            )

    lines.extend(
        [
            "",
            "## Walk-Forward",
            "",
            "| strategy | scenario | positive windows | worst adjusted % | total adjusted % | trades |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for strategy in STRATEGIES:
        for scenario, _, _ in SCENARIOS:
            sr = rows_for(rows, strategy, "walk_forward", scenario)
            pos = sum(1 for row in sr if row.adjusted_profit_pct > 0)
            worst = min((row.adjusted_profit_pct for row in sr), default=0.0)
            total = sum(row.adjusted_profit_pct for row in sr)
            trades = sum(row.trades for row in sr)
            lines.append(f"| `{strategy}` | {scenario} | {pos}/{len(sr)} | {worst:.4f} | {total:.4f} | {trades} |")

    lines.extend(
        [
            "",
            "## Regime Matrix",
            "",
            "| strategy | scenario | positive regimes | worst adjusted % | total adjusted % | zero-trade regimes | regime trades |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for strategy in STRATEGIES:
        for scenario, _, _ in SCENARIOS:
            sr = rows_for(rows, strategy, "regime", scenario)
            pos = sum(1 for row in sr if row.adjusted_profit_pct > 0)
            worst = min((row.adjusted_profit_pct for row in sr), default=0.0)
            total = sum(row.adjusted_profit_pct for row in sr)
            zero = sum(1 for row in sr if row.trades == 0)
            trades = sum(row.trades for row in sr)
            lines.append(f"| `{strategy}` | {scenario} | {pos}/{len(sr)} | {worst:.4f} | {total:.4f} | {zero} | {trades} |")

    lines.extend(["", "## Gate Verdict", "", "| strategy | verdict | blockers |", "|---|---|---|"])
    for strategy in STRATEGIES:
        verdict, reasons = verdict_for(rows, strategy)
        blockers = "; ".join(reasons) if reasons else "needs recursive-analysis and lookahead-analysis before manual dry-run review"
        lines.append(f"| `{strategy}` | {verdict} | {blockers} |")

    lines.extend(
        [
            "",
            "## Research Rule",
            "",
            "If C/D remains weak without event-study support, do not tune thresholds. Return to event-definition design or market-state routing.",
            "",
            f"CSV: `{rel(csv_path)}`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    raise SystemExit(
        "This legacy direct runner is retired because it used hand-picked regime windows. "
        "Import its reusable helpers from a manifest-driven experiment instead."
    )


if __name__ == "__main__":
    main()
