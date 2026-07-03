#!/usr/bin/env python3
"""Run A1 range/bull-pullback abstention validation.

This is a research-only experiment. It keeps the current futures risk contract
fixed and tests coarse market-state abstention layers against the data-derived
regime manifest.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def find_repo_root() -> Path:
    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR / "run_cd_family_rotation_experiment.py"
if not BASE_PATH.exists():
    BASE_PATH = REPO_ROOT / "user_data/strategy_research/run_cd_family_rotation_experiment.py"
MANIFEST_PATH = REPO_ROOT / "user_data/strategy_research/regime_windows/latest_regime_windows.json"
RESULT_DIR = REPO_ROOT / "user_data/backtest_results/20260703T_a1_range_bull_abstain"
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/reports"

spec = importlib.util.spec_from_file_location("cd_family_rotation", BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load base runner: {BASE_PATH}")
BASE = importlib.util.module_from_spec(spec)
sys.modules["cd_family_rotation"] = BASE
spec.loader.exec_module(BASE)


STRATEGY_INFO = {
    "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060": {
        "family": "downtrend_failed_bounce_short",
        "logic": "Best current A1Peak40 branch: ret_96 <= 0.5%, volume_ratio >= 0.60.",
        "filters": "No-chase; fixed ROI/stoploss/leverage; peak40 giveback; volume participation.",
    },
    "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060RangeBullAbstain": {
        "family": "downtrend_failed_bounce_short",
        "logic": "Strict range/bull-pullback abstention on top of the current A1 branch.",
        "filters": "Blocks 24h rebound pressure, positive 48h pullback pockets, and near-24h-high states.",
    },
    "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060RangeBullAbstainLoose": {
        "family": "downtrend_failed_bounce_short",
        "logic": "Looser version of the range/bull-pullback abstention layer.",
        "filters": "Blocks only stronger rebound/pullback pockets to avoid over-thinning bear samples.",
    },
    "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060CompressionAbstain": {
        "family": "downtrend_failed_bounce_short",
        "logic": "Existing low-range compression abstention after ret96 and volume permission.",
        "filters": "Blocks low BB width + low ATR + near 24h high compression pockets.",
    },
    "A1NoChaseRet96Max000PeakDrawdown040VolumeConfirm060": {
        "family": "downtrend_failed_bounce_short",
        "logic": "Strict ret_96 <= 0 branch from the previous round, included as a risk-control benchmark.",
        "filters": "Flat-to-down 24h permission, volume participation, peak40 giveback.",
    },
}
STRATEGIES = list(STRATEGY_INFO)


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"Missing regime manifest: {MANIFEST_PATH}")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("quality", {}).get("legacy_hardcoded_windows_allowed") is not False:
        raise SystemExit("Regime manifest does not block legacy hardcoded windows.")
    return manifest


def windows_from_manifest(manifest: dict[str, Any]) -> list[tuple[str, str, str]]:
    active = {item["label"]: item for item in manifest["windows"] if item.get("status") == "active"}
    required = ["bull", "bear", "range", "high_vol"]
    missing = [label for label in required if label not in active]
    if missing:
        raise SystemExit(f"Manifest missing active labels: {missing}")

    bear = active["bear"]
    start = bear["start"].replace("-", "")
    end = bear["end"].replace("-", "")
    # Three approximately equal walk-forward slices inside the current data-derived bear window.
    wf = [
        ("walk_forward", "wf_bear_1", f"{start}-20260523"),
        ("walk_forward", "wf_bear_2", "20260523-20260612"),
        ("walk_forward", "wf_bear_3", f"20260612-{end}"),
    ]
    return [
        ("main", "65d", "20260429-20260703"),
        ("main", "30d", "20260603-20260703"),
        ("manifest", f"manifest_bear_{bear['name']}", bear["timerange"]),
        ("manifest", f"manifest_bull_{active['bull']['name']}", active["bull"]["timerange"]),
        ("manifest", f"manifest_range_{active['range']['name']}", active["range"]["timerange"]),
        ("manifest", f"manifest_high_vol_{active['high_vol']['name']}", active["high_vol"]["timerange"]),
        *wf,
        ("recent", "latest5", "20260628-20260703"),
    ]


def configure_base(windows: list[tuple[str, str, str]]) -> None:
    BASE.RESULT_DIR = RESULT_DIR
    BASE.STRATEGY_INFO = STRATEGY_INFO
    BASE.STRATEGIES = STRATEGIES
    BASE.WINDOWS = windows


def rows_for(rows: list[Any], strategy: str, scenario: str, window_contains: str | None = None) -> list[Any]:
    selected = [row for row in rows if row.strategy == strategy and row.scenario == scenario]
    if window_contains is not None:
        selected = [row for row in selected if window_contains in row.window]
    return selected


def write_csv(rows: list[Any], ts: str) -> Path:
    path = REPORT_DIR / f"a1_range_bull_abstain_experiment_{ts}.csv"
    fields = list(BASE.Row.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
    return path


def verdict(rows: list[Any], strategy: str) -> tuple[str, list[str]]:
    high = {row.window: row for row in rows if row.strategy == strategy and row.scenario == "high_fee_12bps"}
    stress = {row.window: row for row in rows if row.strategy == strategy and row.scenario == "stress_fee_20bps"}
    bear_high = next((row for key, row in high.items() if "manifest_bear" in key), None)
    bear_stress = next((row for key, row in stress.items() if "manifest_bear" in key), None)
    latest_high = high.get("latest5")
    latest_stress = stress.get("latest5")
    wf_high = [row for key, row in high.items() if key.startswith("wf_bear_")]
    hostile_stress = [
        row
        for key, row in stress.items()
        if "manifest_bull" in key or "manifest_range" in key or "manifest_high_vol" in key
    ]

    reasons: list[str] = []
    if bear_high is None or bear_high.adjusted_profit_pct <= 30:
        reasons.append("home bear high-fee adjusted profit <= 30%")
    if bear_stress is None or bear_stress.adjusted_profit_pct <= 30:
        reasons.append("home bear stress adjusted profit <= 30%")
    if bear_high is None or bear_high.trades < 8:
        reasons.append("home bear sample below 8 trades")
    if latest_high is None or latest_high.trades == 0 or latest_high.adjusted_profit_pct <= 0:
        reasons.append("latest5 high-fee not positive")
    if latest_stress is None or latest_stress.trades == 0 or latest_stress.adjusted_profit_pct <= -1.0:
        reasons.append("latest5 stress too weak")
    wf_worst = min((row.adjusted_profit_pct for row in wf_high), default=0.0)
    wf_pos = sum(1 for row in wf_high if row.adjusted_profit_pct > 0)
    if wf_pos < len(wf_high) and wf_worst < -2.0:
        reasons.append("bear walk-forward not robust")
    hostile_worst = min((row.adjusted_profit_pct for row in hostile_stress), default=0.0)
    if hostile_worst < -15.0:
        reasons.append("hostile stress worst below -15%")
    return ("research_candidate_needs_bias_checks" if not reasons else "research_candidate", reasons)


def write_report(rows: list[Any], csv_path: Path, ts: str, manifest: dict[str, Any]) -> Path:
    path = REPORT_DIR / f"a1_range_bull_abstain_experiment_{ts}.md"
    lines = [
        "# A1 Range/Bull Pullback Abstention Experiment",
        "",
        f"Generated UTC: `{ts}`",
        "",
        "Scope: A1 downtrend failed-bounce short. Research-only. No dry-run/live config or registry changes.",
        "",
        "Fixed contract: Binance USDT-M futures, isolated margin, 50x, ROI={0:1.20,180:1.50,360:1.00}, stoploss=-0.60, 15m entry timeframe.",
        "",
        "Regime source: data-derived `latest_regime_windows.json`; no legacy hardcoded bull/range/bear/high-vol windows.",
        "",
        "## Manifest Windows",
        "",
        "| label | name | timerange | confidence | evidence |",
        "|---|---|---|---|---|",
    ]
    for item in manifest["windows"]:
        evidence = item["evidence"]
        lines.append(
            f"| {item['label']} | {item['name']} | `{item['timerange']}` | {item['confidence']} | "
            f"BTC {evidence['btc_return_pct']:.2f}%, ETH {evidence['eth_return_pct']:.2f}%, "
            f"vol_pctl {evidence['realized_vol_percentile_avg']:.2f}, label_share {evidence['label_share']:.2f} |"
        )

    lines.extend(["", "## Strategy Logic", "", "| strategy | logic | filters |", "|---|---|---|"])
    for strategy, info in STRATEGY_INFO.items():
        lines.append(f"| `{strategy}` | {info['logic']} | {info['filters']} |")

    lines.extend(["", "## Main Readout", "", "| strategy | scenario | bear adj % | bear trades | latest5 adj % | latest5 trades | hostile worst % | hostile trades |", "|---|---|---:|---:|---:|---:|---:|---:|"])
    for strategy in STRATEGIES:
        for scenario, _, _ in BASE.SCENARIOS:
            sr = {row.window: row for row in rows if row.strategy == strategy and row.scenario == scenario}
            bear = next(row for key, row in sr.items() if "manifest_bear" in key)
            latest = sr["latest5"]
            hostile = [
                row
                for key, row in sr.items()
                if "manifest_bull" in key or "manifest_range" in key or "manifest_high_vol" in key
            ]
            lines.append(
                f"| `{strategy}` | {scenario} | {bear.adjusted_profit_pct:.4f} | {bear.trades} | "
                f"{latest.adjusted_profit_pct:.4f} | {latest.trades} | "
                f"{min(row.adjusted_profit_pct for row in hostile):.4f} | {sum(row.trades for row in hostile)} |"
            )

    lines.extend(["", "## Bear Walk-Forward", "", "| strategy | scenario | positive windows | worst adjusted % | total adjusted % | trades |", "|---|---|---:|---:|---:|---:|"])
    for strategy in STRATEGIES:
        for scenario, _, _ in BASE.SCENARIOS:
            wf = [
                row
                for row in rows
                if row.strategy == strategy and row.scenario == scenario and row.window.startswith("wf_bear_")
            ]
            lines.append(
                f"| `{strategy}` | {scenario} | {sum(1 for row in wf if row.adjusted_profit_pct > 0)}/{len(wf)} | "
                f"{min((row.adjusted_profit_pct for row in wf), default=0.0):.4f} | "
                f"{sum(row.adjusted_profit_pct for row in wf):.4f} | {sum(row.trades for row in wf)} |"
            )

    lines.extend(["", "## Verdict", "", "| strategy | verdict | blockers |", "|---|---|---|"])
    for strategy in STRATEGIES:
        status, reasons = verdict(rows, strategy)
        blockers = "; ".join(reasons) if reasons else "needs recursive-analysis, lookahead-analysis, family-risk gate, and manual review"
        lines.append(f"| `{strategy}` | {status} | {blockers} |")

    lines.extend(
        [
            "",
            "## Research Interpretation",
            "",
            "- If strict abstention fixes hostile leakage but removes bear samples, keep it as a clue, not a candidate.",
            "- If loose abstention preserves bear and improves hostile stress, use it as the next research-candidate branch.",
            "- If compression abstention does not add value after ret96+volume, stop carrying it forward for A1.",
            "",
            f"CSV: `{rel(csv_path)}`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    manifest = load_manifest()
    windows = windows_from_manifest(manifest)
    configure_base(windows)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[Any] = []
    for slice_name, window, timerange in BASE.WINDOWS:
        for scenario, fee, slippage_bps in BASE.SCENARIOS:
            artifact = BASE.run_backtest(timerange, fee)
            payload = BASE.load_payload(artifact)
            for strategy in STRATEGIES:
                row = BASE.summarize(payload, artifact, strategy, slice_name, window, timerange, scenario, fee, slippage_bps)
                rows.append(row)
                print(strategy, scenario, window, row.trades, row.adjusted_profit_pct)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = write_csv(rows, ts)
    report_path = write_report(rows, csv_path, ts, manifest)
    print(rel(csv_path))
    print(rel(report_path))


if __name__ == "__main__":
    main()
