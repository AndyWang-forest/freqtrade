#!/usr/bin/env python3
"""Build and evaluate external A1 regime permission artifacts.

This keeps long-history regime logic outside Freqtrade strategy classes.  The
artifact is a research-only permission layer: it publishes daily/hourly A1
allow/deny states from BTC/ETH futures context, then evaluates those states on
already executed A1 trades.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import evaluate_a1_regime_classifier_allocator as BASE


def find_repo_root() -> Path:
    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
PERMISSION_DIR = REPO_ROOT / "user_data/strategy_research/regime_permissions"
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/reports"
MANIFEST_PATH = REPO_ROOT / "user_data/strategy_research/regime_windows/latest_regime_windows.json"


PERMISSION_RULES = {
    "a1_ret30_down_not_highvol_q85": {
        "description": "Allow A1 when combined BTC/ETH 30d return is negative and 30d realized vol is not above its rolling q85.",
        "allow_expr": "combined_ret_30d < 0 and combined_rv_30d <= combined_rv_30d_q85",
        "min_bear_stress_pct": 30.0,
        "min_bear_trades": 8,
        "min_latest5_high_pct": 0.0,
        "hostile_stress_floor_pct": -15.0,
    },
    "a1_ret30_down_not_highvol_q90": {
        "description": "Looser q90 version. Useful when q85 is too sample-thin but still blocks extreme high-vol/non-bear states.",
        "allow_expr": "combined_ret_30d < 0 and combined_rv_30d <= combined_rv_30d_q90",
        "min_bear_stress_pct": 30.0,
        "min_bear_trades": 8,
        "min_latest5_high_pct": 0.0,
        "hostile_stress_floor_pct": -15.0,
    },
    "a1_ret30_down_not_highvol_q80": {
        "description": "Stricter q80 version. Included as sample-thinning control.",
        "allow_expr": "combined_ret_30d < 0 and combined_rv_30d <= combined_rv_30d_q80",
        "min_bear_stress_pct": 30.0,
        "min_bear_trades": 8,
        "min_latest5_high_pct": 0.0,
        "hostile_stress_floor_pct": -15.0,
    },
}


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def load_manifest() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("quality", {}).get("legacy_hardcoded_windows_allowed") is not False:
        raise SystemExit("Regime manifest must explicitly forbid legacy hardcoded windows.")
    return manifest


def build_permission_frame(features: pd.DataFrame) -> pd.DataFrame:
    frame = features.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame["day"] = frame["date"].dt.floor("D")
    frame["a1_ret30_down_not_highvol_q80"] = (
        (frame["combined_ret_30d"] < 0)
        & (frame["combined_rv_30d"] <= frame["combined_rv_30d_q80"])
    )
    frame["a1_ret30_down_not_highvol_q85"] = (
        (frame["combined_ret_30d"] < 0)
        & (frame["combined_rv_30d"] <= frame["combined_rv_30d_q85"])
    )
    frame["a1_ret30_down_not_highvol_q90"] = (
        (frame["combined_ret_30d"] < 0)
        & (frame["combined_rv_30d"] <= frame["combined_rv_30d_q90"])
    )
    evidence_cols = [
        "date",
        "day",
        "combined_ret_30d",
        "combined_ret_60d",
        "combined_ema_gap",
        "combined_rv_30d",
        "combined_rv_30d_q80",
        "combined_rv_30d_q85",
        "combined_rv_30d_q90",
        "combined_trend_eff_30d",
        "btc_ret_30d",
        "eth_ret_30d",
        "btc_eth_same_30d_direction",
        *PERMISSION_RULES.keys(),
    ]
    return frame[evidence_cols].dropna(subset=["combined_ret_30d", "combined_rv_30d"]).copy()


def daily_snapshot(permission: pd.DataFrame) -> pd.DataFrame:
    ordered = permission.sort_values("date")
    daily = ordered.groupby("day", as_index=False).tail(1).copy()
    daily["day"] = daily["day"].dt.strftime("%Y-%m-%d")
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return daily


def write_artifacts(permission: pd.DataFrame, daily: pd.DataFrame, manifest: dict[str, Any], ts: str) -> tuple[Path, Path, Path]:
    PERMISSION_DIR.mkdir(parents=True, exist_ok=True)
    hourly_path = PERMISSION_DIR / f"a1_regime_permissions_hourly_{ts}.csv"
    daily_path = PERMISSION_DIR / f"a1_regime_permissions_daily_{ts}.csv"
    json_path = PERMISSION_DIR / f"a1_regime_permissions_{ts}.json"
    latest_json = PERMISSION_DIR / "latest_a1_regime_permissions.json"
    latest_daily = PERMISSION_DIR / "latest_a1_regime_permissions_daily.csv"

    permission.to_csv(hourly_path, index=False)
    daily.to_csv(daily_path, index=False)

    active_windows = [
        {
            "label": item["label"],
            "name": item["name"],
            "timerange": item["timerange"],
            "confidence": item["confidence"],
        }
        for item in manifest["windows"]
        if item.get("status") == "active"
    ]
    payload = {
        "version": 1,
        "generated_utc": ts,
        "scope": "research_only_external_a1_permission_artifact",
        "strategy_family": "downtrend_failed_bounce_short",
        "contract": {
            "market": "binance_usdt_m_futures",
            "margin": "isolated",
            "leverage": 50,
            "roi": {"0": 1.20, "180": 1.50, "360": 1.00},
            "stoploss": -0.60,
            "primary_entry_timeframe": "15m",
        },
        "rules": PERMISSION_RULES,
        "source_regime_manifest": rel(MANIFEST_PATH),
        "active_regime_windows": active_windows,
        "hourly_csv": rel(hourly_path),
        "daily_csv": rel(daily_path),
        "latest_daily_csv": rel(latest_daily),
        "rows": {
            "hourly": int(len(permission)),
            "daily": int(len(daily)),
            "start": str(daily["day"].iloc[0]) if len(daily) else None,
            "end": str(daily["day"].iloc[-1]) if len(daily) else None,
        },
        "policy": [
            "This artifact can be used as an external A1 permission screen in research.",
            "It is not a dry-run/live config and does not place orders.",
            "A rule that passes offline still needs orchestration implementation, recursive/lookahead checks, family-risk gate, promotion gate, and manual review.",
        ],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    json_path.write_text(text, encoding="utf-8")
    latest_json.write_text(text, encoding="utf-8")
    latest_daily.write_text(daily_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, daily_path, hourly_path


def attach_permission(trades: pd.DataFrame, permission: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy().sort_values("open_date")
    permission = permission.copy().sort_values("date")
    permission["date"] = pd.to_datetime(permission["date"], utc=True)
    return pd.merge_asof(
        trades,
        permission,
        left_on="open_date",
        right_on="date",
        direction="backward",
    )


def evaluate_rule(trades: pd.DataFrame, rule: str) -> dict[str, Any]:
    kept = trades[trades[rule].fillna(False)].copy()
    item = BASE.summarize(kept, trades, rule)
    status, blockers = BASE.verdict(item)
    item["status"] = status
    item["blockers"] = blockers
    return item


def write_report(rows: list[dict[str, Any]], artifact_path: Path, daily_path: Path, ts: str) -> Path:
    report_path = REPORT_DIR / f"a1_external_regime_permission_experiment_{ts}.md"
    lines = [
        "# A1 External Regime Permission Experiment",
        "",
        f"Generated UTC: `{ts}`",
        "",
        "Scope: research-only. This validates an external A1 permission artifact instead of embedding long-history rolling quantiles inside Freqtrade strategy code.",
        "",
        "Fixed contract: Binance USDT-M futures, isolated 50x, ROI={0:1.20,180:1.50,360:1.00}, stoploss=-0.60, 15m entry.",
        "",
        "## Permission Rules",
        "",
        "| rule | description | expression |",
        "|---|---|---|",
    ]
    for name, rule in PERMISSION_RULES.items():
        lines.append(f"| `{name}` | {rule['description']} | `{rule['allow_expr']}` |")

    lines.extend(
        [
            "",
            "## Router Readout",
            "",
            "| permission | status | bear stress % | bear trades | latest5 high % | latest5 trades | latest5 stress % | hostile worst % | hostile trades | WF high-fee | blockers |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| `{row['router']}` | {row['status']} | {row['bear_stress_adj_pct']:.4f} | "
            f"{row['bear_stress_trades']} | {row['latest5_high_adj_pct']:.4f} | "
            f"{row['latest5_high_trades']} | {row['latest5_stress_adj_pct']:.4f} | "
            f"{row['hostile_stress_worst_pct']:.4f} | {row['hostile_stress_trades']} | "
            f"{row['wf_high_positive']}/{row['wf_high_total']} | {row['blockers']} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Passing here means the external permission artifact is worth implementation research; it is not dry-run approval.",
            "- This avoids the previous failure mode where the same long-history q85/q90 logic produced 0 trades when embedded directly in a Freqtrade strategy class.",
            "- If q85/q90 continue to pass offline artifact screening, the next step is an orchestration/backtest adapter that injects daily permission without relying on strategy startup candles.",
            "",
            f"Artifact JSON: `{rel(artifact_path)}`",
            f"Daily permissions CSV: `{rel(daily_path)}`",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    features = BASE.load_features()
    permission = build_permission_frame(features)
    daily = daily_snapshot(permission)
    artifact_path, daily_path, hourly_path = write_artifacts(permission, daily, manifest, ts)

    trades = attach_permission(BASE.collect_trades(), permission)
    rows = [evaluate_rule(trades, rule) for rule in PERMISSION_RULES]
    csv_path = REPORT_DIR / f"a1_external_regime_permission_experiment_{ts}.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    report_path = write_report(rows, artifact_path, daily_path, ts)
    print(rel(artifact_path))
    print(rel(daily_path))
    print(rel(hourly_path))
    print(rel(csv_path))
    print(rel(report_path))


if __name__ == "__main__":
    main()
