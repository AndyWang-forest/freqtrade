#!/usr/bin/env python3
"""Evaluate strategy-family promotion with router and circuit-breaker logic.

This gate is intentionally different from a naked all-regime strategy gate:
high-leverage crypto strategies are expected to specialize by regime.  The
promotion question is whether target-regime edge remains positive and hostile
regime losses are contained by family/portfolio risk controls.
"""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cost_model import PRIMARY_SCENARIO, is_primary_scenario, is_stress_scenario, scenario_label
from experiment_provenance import register_experiment, resolve_experiment


def find_repo_root() -> Path:
    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/reports"
OUTPUT_DIR = REPO_ROOT / "user_data/strategy_research/family_risk_gate"
PROMOTION_DIR = REPO_ROOT / "user_data/strategy_research/promotion_reports"
REGIME_MANIFEST_PATH = REPO_ROOT / "user_data/strategy_research/regime_windows/latest_regime_windows.json"

STARTING_BALANCE = 1000.0
TARGET_65D_GATE = 30.0
TARGET_30D_GATE = 20.0
LATEST5_GATE = 0.0
MIN_65D_TRADES = 8
HOSTILE_GUARDED_WORST_GATE = -15.0
FAMILY_DRAWDOWN_PAUSE_PCT = 10.0
CONSECUTIVE_LOSS_PAUSE = 3
STRESS_HOME_TOTAL_FLOOR_PCT = -10.0
STRESS_HOME_WORST_FLOOR_PCT = -15.0

FAMILY_INFERENCE = [
    ("SecondLeg", "downtrend_failed_bounce_short"),
    ("FailedBounce", "downtrend_failed_bounce_short"),
    ("BodyNotTooRed", "downtrend_failed_bounce_short"),
    ("DownsideBreakout", "downside_breakout_continuation_short"),
    ("DowntrendPullback", "downtrend_pullback_short"),
    ("UptrendPullback", "uptrend_pullback_long"),
    ("UpsideBreakout", "upside_breakout_continuation_long"),
]

FAMILY_ROLE_ALIASES = {
    "range_mean_reversion": "range_upper_reversion_short",
    "range_false_break_reversion": "range_upper_reversion_short",
}


@dataclass
class SimResult:
    raw_profit_pct: float
    guarded_profit_pct: float
    trades_seen: int
    trades_taken: int
    trades_blocked: int
    max_drawdown_pct: float
    pause_reason: str
    evidence_mode: str


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def family_candidate_rank(item: dict[str, Any]) -> tuple[Any, ...]:
    """Rank family candidates by repeated edge before aggregate profit.

    A strategy with independently positive home episodes is a stronger family
    representative than a higher-return strategy supported by one lucky
    episode. Promotion readiness remains the first boundary; guarded return is
    considered only after repeatability.
    """

    positive = int(item.get("home_episode_positive") or 0)
    total = int(item.get("home_episode_total") or 0)
    positive_share = positive / total if total else 0.0
    return (
        bool(item.get("ready_for_manual_dryrun_review")),
        positive,
        positive_share,
        float(item.get("target_65d_guarded_pct") or 0.0),
        float(item.get("stress_home_total_guarded_pct") or 0.0),
        float(item.get("hostile_guarded_worst_pct") or 0.0),
    )


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        dest="csv_path",
        help="Explicit experiment CSV. Registers a hash-locked source for deterministic reruns.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON payload to stdout.")
    parser.add_argument("--drawdown-pause-pct", type=float, default=FAMILY_DRAWDOWN_PAUSE_PCT)
    parser.add_argument(
        "--consecutive-loss-pause",
        type=int,
        default=CONSECUTIVE_LOSS_PAUSE,
        help="Pause after this many consecutive stop_loss exits. Time-stop drifts are not counted as big losses.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fnum(row: dict[str, Any], field: str) -> float:
    try:
        value = row.get(field)
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def inum(row: dict[str, Any], field: str) -> int:
    try:
        return int(float(row.get(field) or 0))
    except (TypeError, ValueError):
        return 0


def infer_family(strategy: str, row: dict[str, Any]) -> str:
    family = row.get("strategy_family")
    if family:
        return family
    for needle, inferred in FAMILY_INFERENCE:
        if needle in strategy:
            return inferred
    return "unknown"


def canonical_family_for_roles(family: str) -> str:
    return FAMILY_ROLE_ALIASES.get(family, family)


def scenario_is_high_fee(row: dict[str, Any]) -> bool:
    return is_primary_scenario(row.get("scenario"))


def scenario_is_stress(row: dict[str, Any]) -> bool:
    return is_stress_scenario(row.get("scenario"))


def row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        row.get("strategy", ""),
        row.get("slice", ""),
        row.get("window", ""),
        scenario_label(row.get("scenario")),
    )


def canonical_gate_window(window: str) -> str:
    """Normalize runner-specific recent-window labels for promotion gates."""
    if window in {"65d", "30d", "latest5"}:
        return window
    if window in {"recent_65d", "current_65d"} or window.startswith("65d_"):
        return "65d"
    if window in {"recent_30d", "current_30d"} or window.startswith("30d_"):
        return "30d"
    if window.startswith("latest5_"):
        return "latest5"
    return window


def load_regime_manifest() -> dict[str, Any]:
    if not REGIME_MANIFEST_PATH.exists():
        return {}
    try:
        manifest = json.loads(REGIME_MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if manifest.get("method") != "data_derived_btc_eth_futures_ohlcv":
        return {}
    return manifest


def active_window_names_by_label(manifest: dict[str, Any], label: str) -> set[str]:
    return {
        item.get("name", "")
        for item in manifest.get("windows", [])
        if item.get("status") == "active" and item.get("label") == label and item.get("name")
    }


def active_window_names_except_label(manifest: dict[str, Any], label: str) -> set[str]:
    return {
        item.get("name", "")
        for item in manifest.get("windows", [])
        if item.get("status") == "active" and item.get("label") != label and item.get("name")
    }


def family_role_names(manifest: dict[str, Any], family: str, role: str) -> set[str]:
    if family in {
        "volatility_compression_directional_expansion",
        "volatility_compression_breakout",
    }:
        if role == "home":
            return active_window_names_by_label(manifest, "high_vol")
        if role == "hostile":
            return active_window_names_except_label(manifest, "high_vol")
    roles = manifest.get("family_window_roles") or {}
    canonical = canonical_family_for_roles(family)
    names = set(roles.get(canonical, {}).get(role, []) or [])
    if not names and canonical != family:
        names = set(roles.get(family, {}).get(role, []) or [])
    return names


def manifest_window_name(row: dict[str, Any]) -> str:
    window = row.get("window", "")
    if not window.startswith("manifest_"):
        return window
    return window.removeprefix("manifest_")


def manifest_row_matches(row: dict[str, Any], role_names: set[str]) -> bool:
    window_name = manifest_window_name(row)
    return any(window_name == name or window_name.endswith(f"_{name}") for name in role_names)


def current_rows_are_abstention(rows: list[dict[str, Any]], sims: dict[tuple[str, str, str, str], SimResult]) -> bool:
    current_rows = [
        row
        for row in rows
        if row.get("slice") == "main" and canonical_gate_window(row.get("window", "")) in {"65d", "30d", "latest5"}
    ]
    if not current_rows:
        return False
    return all(sims[row_key(row)].trades_taken == 0 and sims[row_key(row)].guarded_profit_pct == 0.0 for row in current_rows)


def combine_episode_sims(rows: list[tuple[dict[str, Any], SimResult]]) -> tuple[dict[str, Any], SimResult] | tuple[dict[str, Any], None]:
    if not rows:
        return {}, None
    names = [row.get("window", "") for row, _ in rows]
    modes = sorted({sim.evidence_mode for _, sim in rows})
    return (
        {"window": ";".join(names), "episode_windows": names},
        SimResult(
            raw_profit_pct=round(sum(sim.raw_profit_pct for _, sim in rows), 4),
            guarded_profit_pct=round(sum(sim.guarded_profit_pct for _, sim in rows), 4),
            trades_seen=sum(sim.trades_seen for _, sim in rows),
            trades_taken=sum(sim.trades_taken for _, sim in rows),
            trades_blocked=sum(sim.trades_blocked for _, sim in rows),
            max_drawdown_pct=round(max((sim.max_drawdown_pct for _, sim in rows), default=0.0), 4),
            pause_reason=";".join(sorted({sim.pause_reason for _, sim in rows})),
            evidence_mode="multi_episode[" + ",".join(modes) + "]",
        ),
    )


def load_payload(artifact: Path) -> dict[str, Any] | None:
    if not artifact.exists() or artifact.suffix != ".zip":
        return None
    with zipfile.ZipFile(artifact) as zf:
        names = [
            name
            for name in zf.namelist()
            if name.endswith(".json") and not name.endswith("_config.json") and not name.endswith(".meta.json")
        ]
        if not names:
            return None
        return json.loads(zf.read(names[0]))


def trades_for(row: dict[str, Any]) -> list[dict[str, Any]]:
    artifact_text = row.get("artifact") or ""
    if not artifact_text:
        return []
    artifact = REPO_ROOT / artifact_text
    payload = load_payload(artifact)
    if not payload:
        return []
    strategy = row.get("strategy", "")
    stats = (payload.get("strategy") or {}).get(strategy) or {}
    trades = stats.get("trades") or []
    return sorted(trades, key=lambda item: int(item.get("close_timestamp") or item.get("open_timestamp") or 0))


def trade_adjusted_profit_abs(trade: dict[str, Any], slippage_bps: float) -> float:
    profit_abs = float(trade.get("profit_abs") or 0.0)
    notional = float(trade.get("stake_amount") or 0.0) * float(trade.get("leverage") or 1.0)
    slippage_abs = abs(notional) * slippage_bps / 10000.0
    return profit_abs - slippage_abs


def simulate_row_from_trades(
    row: dict[str, Any],
    drawdown_pause_pct: float,
    consecutive_loss_pause: int,
) -> SimResult | None:
    trades = trades_for(row)
    if not trades:
        return None
    slippage_bps = fnum(row, "slippage_bps")
    equity = STARTING_BALANCE
    peak = STARTING_BALANCE
    raw_profit_abs = 0.0
    guarded_profit_abs = 0.0
    max_drawdown = 0.0
    consecutive_stop_losses = 0
    pause_reason = ""
    trades_taken = 0
    trades_blocked = 0
    paused = False

    for trade in trades:
        adjusted_abs = trade_adjusted_profit_abs(trade, slippage_bps)
        raw_profit_abs += adjusted_abs
        if paused:
            trades_blocked += 1
            continue
        guarded_profit_abs += adjusted_abs
        trades_taken += 1
        equity += adjusted_abs
        peak = max(peak, equity)
        drawdown = (peak - equity) / STARTING_BALANCE * 100.0
        max_drawdown = max(max_drawdown, drawdown)
        if trade.get("exit_reason") == "stop_loss":
            consecutive_stop_losses += 1
        else:
            consecutive_stop_losses = 0
        if drawdown >= drawdown_pause_pct:
            paused = True
            pause_reason = f"family_drawdown_pause_{drawdown_pause_pct:g}pct"
        elif consecutive_stop_losses >= consecutive_loss_pause:
            paused = True
            pause_reason = f"consecutive_stop_loss_pause_{consecutive_loss_pause}"

    return SimResult(
        raw_profit_pct=round(raw_profit_abs / STARTING_BALANCE * 100.0, 4),
        guarded_profit_pct=round(guarded_profit_abs / STARTING_BALANCE * 100.0, 4),
        trades_seen=len(trades),
        trades_taken=trades_taken,
        trades_blocked=trades_blocked,
        max_drawdown_pct=round(max_drawdown, 4),
        pause_reason=pause_reason or "none",
        evidence_mode="trade_level",
    )


def simulate_row_aggregate(row: dict[str, Any]) -> SimResult:
    adjusted = fnum(row, "adjusted_profit_pct") or fnum(row, "profit_total_pct")
    guarded = max(adjusted, HOSTILE_GUARDED_WORST_GATE) if row.get("slice") == "regime" else adjusted
    return SimResult(
        raw_profit_pct=round(adjusted, 4),
        guarded_profit_pct=round(guarded, 4),
        trades_seen=inum(row, "trades"),
        trades_taken=inum(row, "trades"),
        trades_blocked=0,
        max_drawdown_pct=fnum(row, "max_drawdown_pct"),
        pause_reason="aggregate_only",
        evidence_mode="aggregate_simulation",
    )


def simulate_row(row: dict[str, Any], drawdown_pause_pct: float, consecutive_loss_pause: int) -> SimResult:
    trade_level = simulate_row_from_trades(row, drawdown_pause_pct, consecutive_loss_pause)
    return trade_level or simulate_row_aggregate(row)


def summarize_strategy(
    strategy: str,
    family: str,
    rows: list[dict[str, Any]],
    drawdown_pause_pct: float,
    consecutive_loss_pause: int,
    regime_manifest: dict[str, Any],
) -> dict[str, Any]:
    high_rows = [row for row in rows if scenario_is_high_fee(row)]
    stress_rows = [row for row in rows if scenario_is_stress(row)]
    sims = {
        row_key(row): simulate_row(row, drawdown_pause_pct, consecutive_loss_pause)
        for row in high_rows
    }
    main = {
        canonical_gate_window(row.get("window", "")): (row, sims[row_key(row)])
        for row in high_rows
        if row.get("slice") == "main"
    }
    home_names = family_role_names(regime_manifest, family, "home")
    hostile_names = family_role_names(regime_manifest, family, "hostile")
    home_rows = [
        (row, sims[row_key(row)])
        for row in high_rows
        if manifest_row_matches(row, home_names)
    ]
    observed_home_names = {
        name
        for name in home_names
        if any(manifest_row_matches(row, {name}) for row, _ in home_rows)
    }
    missing_home_names = sorted(home_names - observed_home_names)
    recent = {
        canonical_gate_window(row.get("window", "")): (row, sims[row_key(row)])
        for row in high_rows
        if row.get("slice") == "recent"
    }
    walk_forward = [(row, sims[row_key(row)]) for row in high_rows if row.get("slice") == "walk_forward"]
    hostile = [
        (row, sims[row_key(row)])
        for row in high_rows
        if manifest_row_matches(row, hostile_names)
    ]
    if not hostile:
        hostile = [(row, sims[row_key(row)]) for row in high_rows if row.get("slice") == "regime"]
    if not hostile:
        hostile = [
            (row, sims[row_key(row)])
            for row in high_rows
            if row.get("slice") == "manifest" and not manifest_row_matches(row, home_names)
        ]

    row_65, sim_65 = main.get("65d", ({}, None))
    row_30, sim_30 = main.get("30d", ({}, None))
    row_5, sim_5 = main.get("latest5", ({}, None))
    evaluation_mode = "recent_main"
    current_window_role = "target"
    home_row, home_sim = combine_episode_sims(home_rows)
    if home_sim is not None:
        evaluation_mode = "manifest_home"
        current_window_role = "abstain" if current_rows_are_abstention(high_rows, sims) else "non_home_observation"
        row_65, sim_65 = home_row, home_sim
        sim_30 = None
    if sim_5 is None:
        row_5, sim_5 = recent.get("latest5", ({}, None))
    target_65 = sim_65.guarded_profit_pct if sim_65 else 0.0
    target_30 = sim_30.guarded_profit_pct if sim_30 else 0.0
    latest5 = sim_5.guarded_profit_pct if sim_5 else 0.0
    trades_65 = sim_65.trades_taken if sim_65 else 0
    wf_positive = sum(1 for _, sim in walk_forward if sim.guarded_profit_pct > 0)
    wf_total = len(walk_forward)
    wf_worst = min((sim.guarded_profit_pct for _, sim in walk_forward), default=0.0)
    hostile_raw_worst = min((sim.raw_profit_pct for _, sim in hostile), default=0.0)
    hostile_guarded_worst = min((sim.guarded_profit_pct for _, sim in hostile), default=0.0)
    hostile_guarded_total = sum(sim.guarded_profit_pct for _, sim in hostile)
    evidence_modes = sorted({sim.evidence_mode for sim in sims.values()})
    stress_sims = {
        row_key(row): simulate_row(row, drawdown_pause_pct, consecutive_loss_pause)
        for row in stress_rows
    }
    stress_home_rows = [
        (row, stress_sims[row_key(row)])
        for row in stress_rows
        if manifest_row_matches(row, home_names)
    ]
    stress_home_total = sum(sim.guarded_profit_pct for _, sim in stress_home_rows)
    stress_home_worst = min((sim.guarded_profit_pct for _, sim in stress_home_rows), default=0.0)
    home_episode_positive = sum(1 for _, sim in home_rows if sim.guarded_profit_pct > 0)
    home_episode_total = len(home_rows)
    home_episode_worst = min((sim.guarded_profit_pct for _, sim in home_rows), default=0.0)
    aggregate_rows_with_trades = [
        row.get("window", "")
        for row in high_rows
        if sims[row_key(row)].evidence_mode == "aggregate_simulation" and inum(row, "trades") > 0
    ]

    blockers: list[str] = []
    supports: list[str] = []
    target_label = "home-regime" if evaluation_mode == "manifest_home" else "65d target-regime"
    if target_65 <= TARGET_65D_GATE:
        blockers.append(f"{target_label} guarded profit {target_65:.4f}% <= {TARGET_65D_GATE:.1f}%")
    else:
        supports.append(f"{target_label} guarded profit {target_65:.4f}% clears gate")
    if evaluation_mode == "manifest_home":
        if current_window_role == "abstain":
            supports.append("current 65d/30d/latest5 non-home windows are clean abstention")
        else:
            supports.append("current 65d/30d/latest5 treated as non-home observation, not target-regime gate")
    else:
        if sim_30 is None:
            blockers.append("30d target-regime row missing from gate input")
        elif target_30 <= TARGET_30D_GATE:
            blockers.append(f"30d target-regime guarded profit {target_30:.4f}% <= {TARGET_30D_GATE:.1f}%")
        else:
            supports.append(f"30d target-regime guarded profit {target_30:.4f}% clears gate")
        if latest5 <= LATEST5_GATE:
            blockers.append("latest5 is not positive after family risk controls")
        else:
            supports.append(f"latest5 remains positive at {latest5:.4f}%")
    if trades_65 < MIN_65D_TRADES:
        blockers.append(f"65d trades taken {trades_65} < {MIN_65D_TRADES}")
    if home_rows and missing_home_names:
        blockers.append("active home validation episodes missing from gate input: " + ", ".join(missing_home_names))
    if home_episode_total > 1 and home_episode_positive * 2 < home_episode_total:
        blockers.append(
            f"home episodes positive {home_episode_positive}/{home_episode_total}; edge is not independently repeated"
        )
    if home_rows and not stress_home_rows:
        blockers.append("stress-cost home-regime evidence is missing")
    elif stress_home_rows:
        if stress_home_total < STRESS_HOME_TOTAL_FLOOR_PCT:
            blockers.append(
                f"stress home total {stress_home_total:.4f}% < {STRESS_HOME_TOTAL_FLOOR_PCT:.1f}% safety floor"
            )
        if stress_home_worst < STRESS_HOME_WORST_FLOOR_PCT:
            blockers.append(
                f"stress home worst {stress_home_worst:.4f}% < {STRESS_HOME_WORST_FLOOR_PCT:.1f}% safety floor"
            )
    if hostile_guarded_worst < HOSTILE_GUARDED_WORST_GATE:
        blockers.append(
            f"hostile-regime guarded worst {hostile_guarded_worst:.4f}% < {HOSTILE_GUARDED_WORST_GATE:.1f}%"
        )
    else:
        supports.append(f"hostile-regime guarded worst {hostile_guarded_worst:.4f}% is contained")
    if wf_total and wf_positive < wf_total and wf_worst < -2.0:
        blockers.append(f"walk-forward guarded positives {wf_positive}/{wf_total}, worst {wf_worst:.4f}%")
    if aggregate_rows_with_trades:
        blockers.append(
            "some rows with trades used aggregate simulation; require trade-level confirmation before dry-run review"
        )

    ready = not blockers
    state = "dryrun_candidate_review_pending_manual_approval" if ready else "research_candidate"
    next_actions: list[str] = []
    if blockers:
        next_actions.append("improve_regime_router_or_family_risk_controls")
    if aggregate_rows_with_trades:
        next_actions.append("rerun_with_trade_level_artifacts")
    if ready:
        next_actions.append("run_recursive_lookahead_and_manual_dryrun_review")
    if not next_actions:
        next_actions.append("continue_research")
    return {
        "strategy": strategy,
        "strategy_family": family,
        "ready_for_manual_dryrun_review": ready,
        "state": state,
        "verdict": state,
        "target_65d_guarded_pct": round(target_65, 4),
        "target_30d_guarded_pct": round(target_30, 4),
        "latest5_guarded_pct": round(latest5, 4),
        "target_65d_trades_taken": trades_65,
        "evaluation_mode": evaluation_mode,
        "current_window_role": current_window_role,
        "home_windows": sorted(home_names),
        "hostile_windows": sorted(hostile_names),
        "selected_home_window": row_65.get("window", ""),
        "selected_home_windows": row_65.get("episode_windows", [row_65.get("window", "")]),
        "missing_home_windows": missing_home_names,
        "home_episode_positive": home_episode_positive,
        "home_episode_total": home_episode_total,
        "home_episode_worst_guarded_pct": round(home_episode_worst, 4),
        "stress_home_total_guarded_pct": round(stress_home_total, 4),
        "stress_home_worst_guarded_pct": round(stress_home_worst, 4),
        "walk_forward_positive": wf_positive,
        "walk_forward_total": wf_total,
        "walk_forward_worst_guarded_pct": round(wf_worst, 4),
        "hostile_raw_worst_pct": round(hostile_raw_worst, 4),
        "hostile_guarded_worst_pct": round(hostile_guarded_worst, 4),
        "hostile_guarded_total_pct": round(hostile_guarded_total, 4),
        "evidence_modes": evidence_modes,
        "supports": supports,
        "blockers": blockers,
        "blocks": "; ".join(blockers) if blockers else "none",
        "next_actions": "; ".join(next_actions),
    }


def build_payload(csv_path: Path, args: argparse.Namespace, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = read_csv(csv_path)
    regime_manifest = load_regime_manifest()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        strategy = row.get("strategy", "")
        family = infer_family(strategy, row)
        grouped[(family, strategy)].append(row)
    verdicts = [
        summarize_strategy(
            strategy,
            family,
            strategy_rows,
            args.drawdown_pause_pct,
            args.consecutive_loss_pause,
            regime_manifest,
        )
        for (family, strategy), strategy_rows in sorted(grouped.items())
    ]
    family_verdicts: dict[str, dict[str, Any]] = {}
    for family in sorted({item["strategy_family"] for item in verdicts}):
        family_items = [item for item in verdicts if item["strategy_family"] == family]
        best = max(
            family_items,
            key=family_candidate_rank,
        )
        family_verdicts[family] = {
            "family": family,
            "best_strategy": best["strategy"],
            "ready_for_manual_dryrun_review": best["ready_for_manual_dryrun_review"],
            "state": best["state"],
            "verdict": best["verdict"],
            "evaluation_mode": best["evaluation_mode"],
            "current_window_role": best["current_window_role"],
            "selected_home_window": best["selected_home_window"],
            "selected_home_windows": best["selected_home_windows"],
            "home_episode_positive": best["home_episode_positive"],
            "home_episode_total": best["home_episode_total"],
            "stress_home_total_guarded_pct": best["stress_home_total_guarded_pct"],
            "stress_home_worst_guarded_pct": best["stress_home_worst_guarded_pct"],
            "blockers": best["blockers"],
            "blocks": best["blocks"],
            "supports": best["supports"],
            "next_actions": best["next_actions"],
        }
    return {
        "generated_at_utc": now_utc(),
        "source_csv": rel(csv_path),
        "source_provenance": provenance or {},
        "gate_version": 2,
        "scope": "all_strategy_families",
        "promotion_principle": (
            "Strategy families do not need to be all-regime holy grails.  Dry-run review requires "
            "family home-regime edge under the realistic-cost primary screen plus hostile-regime loss containment "
            "under family/portfolio circuit breakers. Current non-home windows with zero trades are treated as "
            "abstention evidence, not failed target-regime profit. Stress cost is a safety check, not the sole entry filter."
        ),
        "regime_manifest": {
            "path": rel(REGIME_MANIFEST_PATH),
            "method": regime_manifest.get("method", "missing"),
            "generated_at_utc": regime_manifest.get("generated_at_utc"),
            "manifest_version": regime_manifest.get("manifest_version"),
        },
        "risk_controls": {
            "starting_balance": STARTING_BALANCE,
            "primary_cost_scenario": PRIMARY_SCENARIO,
            "family_drawdown_pause_pct": args.drawdown_pause_pct,
            "consecutive_stop_loss_pause": args.consecutive_loss_pause,
            "hostile_guarded_worst_gate_pct": HOSTILE_GUARDED_WORST_GATE,
            "stress_home_total_floor_pct": STRESS_HOME_TOTAL_FLOOR_PCT,
            "stress_home_worst_floor_pct": STRESS_HOME_WORST_FLOOR_PCT,
        },
        "family_verdicts": list(family_verdicts.values()),
        "verdicts": verdicts,
    }


def write_json(payload: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROMOTION_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = payload["generated_at_utc"]
    path = OUTPUT_DIR / f"family_risk_gate_{timestamp}.json"
    latest = OUTPUT_DIR / "latest_family_risk_gate.json"
    promotion_latest = PROMOTION_DIR / "latest_promotion_report.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    promotion_latest.write_text(text, encoding="utf-8")
    return path


def write_markdown(payload: dict[str, Any]) -> Path:
    timestamp = payload["generated_at_utc"]
    path = OUTPUT_DIR / f"family_risk_gate_{timestamp}.md"
    latest = OUTPUT_DIR / "latest_family_risk_gate.md"
    promotion_latest = PROMOTION_DIR / "latest_promotion_report.md"
    lines = [
        "# Strategy Family Risk Gate",
        "",
        f"- Generated UTC: `{timestamp}`",
        f"- Source CSV: `{payload['source_csv']}`",
        f"- Scope: `{payload['scope']}`",
        f"- Gate version: `{payload['gate_version']}`",
        "",
        "## Principle",
        "",
        payload["promotion_principle"],
        "",
        "## Risk Controls",
        "",
        "| Control | Value |",
        "|---|---:|",
    ]
    for key, value in payload["risk_controls"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## Regime Manifest",
            "",
            "| Field | Value |",
            "|---|---|",
        ]
    )
    for key, value in payload.get("regime_manifest", {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## Family Verdicts",
            "",
            "| Family | Best Strategy | Eval Mode | Current Role | Selected Home | Ready | State | Blockers |",
            "|---|---|---|---|---|---:|---|---|",
        ]
    )
    for item in payload["family_verdicts"]:
        blockers = "; ".join(item["blockers"]) if item["blockers"] else "none"
        lines.append(
            f"| `{item['family']}` | `{item['best_strategy']}` | `{item.get('evaluation_mode', '')}` | "
            f"`{item.get('current_window_role', '')}` | `{item.get('selected_home_window', '')}` | "
            f"{item['ready_for_manual_dryrun_review']} | "
            f"`{item['state']}` | {blockers} |"
        )
    lines.extend(
        [
            "",
            "## Strategy Verdicts",
            "",
            "| Strategy | Family | Eval Mode | Current Role | Home Episodes | State | Home/65d guarded | Stress Total | Stress Worst | 30d guarded | latest5 | WF | Hostile raw worst | Hostile guarded worst | Evidence |",
            "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|",
        ]
    )
    for item in payload["verdicts"]:
        evidence = ", ".join(item["evidence_modes"])
        wf = f"{item['walk_forward_positive']}/{item['walk_forward_total']} worst {item['walk_forward_worst_guarded_pct']:.4f}%"
        lines.append(
            f"| `{item['strategy']}` | `{item['strategy_family']}` | `{item.get('evaluation_mode', '')}` | "
            f"`{item.get('current_window_role', '')}` | {item['home_episode_positive']}/{item['home_episode_total']} | `{item['state']}` | "
            f"{item['target_65d_guarded_pct']:.4f} | {item['stress_home_total_guarded_pct']:.4f} | "
            f"{item['stress_home_worst_guarded_pct']:.4f} | {item['target_30d_guarded_pct']:.4f} | "
            f"{item['latest5_guarded_pct']:.4f} | {wf} | {item['hostile_raw_worst_pct']:.4f} | "
            f"{item['hostile_guarded_worst_pct']:.4f} | {evidence} |"
        )
    lines.extend(
        [
            "",
            "## Promotion Boundary",
            "",
            "- This gate records readiness for manual dry-run review only.",
            "- It does not start dry-run/live trading and does not edit trading config.",
            "- A family can pass only when target-regime edge survives and hostile-regime loss is contained by circuit breakers.",
            "",
        ]
    )
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    promotion_latest.write_text(text, encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    csv_path, provenance = resolve_experiment(
        args.csv_path,
        register_explicit=False,
        producer="family_risk_gate_explicit_input" if args.csv_path else "family_risk_gate_registered_input",
    )
    payload = build_payload(csv_path, args, provenance)
    if args.csv_path:
        provenance = register_experiment(csv_path, producer="family_risk_gate_explicit_input")
        payload["source_provenance"] = provenance
    json_path = write_json(payload)
    md_path = write_markdown(payload)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Wrote {rel(json_path)}")
        print(f"Wrote {rel(md_path)}")
        print(f"Wrote {rel(PROMOTION_DIR / 'latest_promotion_report.json')}")
        print(f"Wrote {rel(PROMOTION_DIR / 'latest_promotion_report.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
