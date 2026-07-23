#!/usr/bin/env python3
"""Run allocation-targeted, knowledge-derived futures factor research.

The independent research allocator selects a historical regime, strategy
family, and permitted sides.  This module then evaluates typed price-action, regime, derivatives,
microstructure, and cross-asset features.  Gross edge is checked before costs,
signals are de-clustered into independent episodes, and all-history diagnostics
cannot replace the current-target report pointer.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from cost_model import REALISTIC_SCENARIO
from factor_research_protocol import FACTOR_RESEARCH_METHOD_VERSION
from factor_feature_registry import (
    FACTOR_SPECS,
    add_ohlcv_features,
    attach_cross_asset_features,
    attach_registered_auxiliary_features,
    build_cross_asset_context,
    registry_payload,
)
from pair_universe import pairs_for_scope
from regime_window_builder import REGIME_LABELS, active_windows_for_label, regime_entry_mask
from repo_paths import find_repo_root
from research_artifact_index import publish_report
from research_target import ResearchTarget, load_current_research_target


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
DATA_ROOT = REPO_ROOT / "user_data/data/binance/futures"
AUX_ROOT = REPO_ROOT / "user_data/data/binance/futures_aux"
CACHE_ROOT = AGENT_ROOT / "cache/factor_features"
OUTPUT_DIR = AGENT_ROOT / "factors"
LATEST_JSON = OUTPUT_DIR / "latest_factor_research.json"
LATEST_MD = OUTPUT_DIR / "latest_factor_research.md"
INDEX_JSON = OUTPUT_DIR / "factor_research_index.json"
TIMEFRAMES = ["3m", "5m", "15m"]
REALISTIC_ROUND_TRIP_FRICTION = (
    2 * REALISTIC_SCENARIO.fee + REALISTIC_SCENARIO.slippage_bps / 10000.0
)

# Kept for compatibility with older report readers.  The active gate uses
# independent episodes rather than dense, overlapping candle rows.
MIN_SAMPLE = 80
MIN_INDEPENDENT_SAMPLE = 24
MIN_GROSS_WIN_RATE = 0.50
MIN_NET_WIN_RATE = 0.52
MIN_MFE_MAE_RATIO = 1.15


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def pair_data_path(pair: str, timeframe: str) -> Path:
    stem = pair.replace("/", "_").replace(":", "_")
    return DATA_ROOT / f"{stem}-{timeframe}-futures.feather"


def load_frame(pair: str, timeframe: str) -> pd.DataFrame:
    path = pair_data_path(pair, timeframe)
    frame = pd.read_feather(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    return frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Compatibility wrapper used by older tests and local notebooks."""

    return add_ohlcv_features(frame, REALISTIC_ROUND_TRIP_FRICTION)


def add_forward_labels(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    out = frame.copy()
    entry_price = out["open"].shift(-1)
    out["entry_price"] = entry_price
    out["forward_return"] = out["close"].shift(-horizon) / entry_price - 1.0
    forward_high = out["high"].shift(-1).rolling(horizon).max().shift(-(horizon - 1))
    forward_low = out["low"].shift(-1).rolling(horizon).min().shift(-(horizon - 1))
    out["long_mfe"] = (forward_high / entry_price - 1.0).clip(lower=0.0)
    out["long_mae"] = (1.0 - forward_low / entry_price).clip(lower=0.0)
    out["short_mfe"] = (1.0 - forward_low / entry_price).clip(lower=0.0)
    out["short_mae"] = (forward_high / entry_price - 1.0).clip(lower=0.0)
    return out


def decluster_sample(sample: pd.DataFrame, minimum_gap_bars: int) -> pd.DataFrame:
    """Keep one event per non-overlapping forward horizon."""

    if sample.empty:
        return sample
    selected: list[int] = []
    last_position: int | None = None
    for position in sample.sort_index().index:
        numeric = int(position)
        if last_position is None or numeric - last_position >= minimum_gap_bars:
            selected.append(position)
            last_position = numeric
    return sample.loc[selected].copy()


def regime_window_protocol(regime_label: str | None) -> dict[str, Any]:
    """Freeze thresholds on the earliest home episode and validate later ones."""

    if not regime_label:
        return {
            "threshold_source": "all_history_diagnostic",
            "development_window": None,
            "validation_windows": [],
            "window_roles": {},
        }
    windows = sorted(
        active_windows_for_label(regime_label),
        key=lambda item: (item["start"], item["end"], item["name"]),
    )
    development = windows[0]["name"]
    validation = [item["name"] for item in windows[1:]]
    return {
        "threshold_source": "earliest_home_episode",
        "development_window": development,
        "validation_windows": validation,
        "window_roles": {
            item["name"]: ("development" if item["name"] == development else "validation")
            for item in windows
        },
    }


def _window_evidence(
    sample: pd.DataFrame,
    side: str,
    window_roles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "regime_window" not in sample.columns:
        return rows
    for name, group in sample.dropna(subset=["regime_window"]).groupby("regime_window"):
        signed = group["forward_return"] if side == "long" else -group["forward_return"]
        rows.append(
            {
                "window": str(name),
                "role": (window_roles or {}).get(str(name), "diagnostic"),
                "sample": int(len(group)),
                "mean_gross_pct": round(float(signed.mean()) * 100, 4),
                "mean_after_fee_pct": round(float((signed - REALISTIC_ROUND_TRIP_FRICTION).mean()) * 100, 4),
                "gross_positive": bool(float(signed.mean()) > 0),
                "net_positive": bool(float((signed - REALISTIC_ROUND_TRIP_FRICTION).mean()) > 0),
            }
        )
    role_order = {"development": 0, "validation": 1, "diagnostic": 2}
    return sorted(rows, key=lambda item: (role_order.get(item["role"], 9), item["window"]))


def side_score(
    sample: pd.DataFrame,
    side: str,
    *,
    raw_sample: int | None = None,
    regime_label: str | None = None,
    window_roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    if side == "long":
        returns = sample["forward_return"]
        mfe = sample["long_mfe"]
        mae = sample["long_mae"]
    else:
        returns = -sample["forward_return"]
        mfe = sample["short_mfe"]
        mae = sample["short_mae"]
    returns_after_fee = returns - REALISTIC_ROUND_TRIP_FRICTION
    count = int(returns.count())
    if count == 0:
        return {
            "raw_sample": int(raw_sample or 0),
            "sample": 0,
            "independent_sample": 0,
            "mean_forward_return_pct": 0.0,
            "mean_after_fee_pct": 0.0,
            "gross_win_rate": 0.0,
            "win_rate": 0.0,
            "mean_mfe_pct": 0.0,
            "mean_mae_pct": 0.0,
            "mfe_mae_ratio": None,
            "gross_gate": "insufficient_sample",
            "cost_gate": "not_evaluated",
            "positive_validation_windows": 0,
            "required_validation_windows": 1 if regime_label else 0,
            "window_evidence": [],
            "verdict": "insufficient_sample",
        }
    mean_gross = float(returns.mean())
    mean_after_fee = float(returns_after_fee.mean())
    gross_win_rate = float((returns > 0).mean())
    net_win_rate = float((returns_after_fee > 0).mean())
    mean_mfe = float(mfe.mean())
    mean_mae = float(mae.mean())
    ratio = mean_mfe / mean_mae if mean_mae > 0 else None
    gross_pass = (
        count >= MIN_INDEPENDENT_SAMPLE
        and mean_gross > REALISTIC_ROUND_TRIP_FRICTION
        and gross_win_rate >= MIN_GROSS_WIN_RATE
        and ratio is not None
        and ratio >= MIN_MFE_MAE_RATIO
    )
    cost_pass = gross_pass and mean_after_fee > 0 and net_win_rate >= MIN_NET_WIN_RATE
    windows = _window_evidence(sample, side, window_roles)
    required_windows = 2 if regime_label else 0
    positive_windows = sum(bool(item["net_positive"]) for item in windows)
    positive_validation_windows = sum(
        bool(item["net_positive"])
        for item in windows
        if item.get("role") == "validation"
    )
    required_validation_windows = 1 if regime_label else 0
    if not regime_label:
        verdict = "diagnostic_only_all_history"
    elif not gross_pass:
        verdict = "reject_gross_edge"
    elif not cost_pass:
        verdict = "reject_realistic_cost"
    elif (
        positive_windows < required_windows
        or positive_validation_windows < required_validation_windows
    ):
        verdict = "needs_independent_window"
    else:
        verdict = "edge_candidate"
    return {
        "raw_sample": int(raw_sample if raw_sample is not None else count),
        "sample": count,
        "independent_sample": count,
        "mean_forward_return_pct": round(mean_gross * 100, 4),
        "mean_after_fee_pct": round(mean_after_fee * 100, 4),
        "gross_win_rate": round(gross_win_rate, 4),
        "win_rate": round(net_win_rate, 4),
        "mean_mfe_pct": round(mean_mfe * 100, 4),
        "mean_mae_pct": round(mean_mae * 100, 4),
        "mfe_mae_ratio": round(ratio, 4) if ratio is not None else None,
        "gross_gate": "pass" if gross_pass else "fail",
        "cost_gate": "pass" if cost_pass else ("fail" if gross_pass else "not_evaluated"),
        "positive_independent_windows": positive_windows,
        "required_independent_windows": required_windows,
        "positive_validation_windows": positive_validation_windows,
        "required_validation_windows": required_validation_windows,
        "window_evidence": windows,
        "verdict": verdict,
    }


def quantile_sample(
    frame: pd.DataFrame,
    column: str,
    tail: str,
    *,
    calibration_window: str | None = None,
) -> tuple[pd.DataFrame, float | None]:
    required = [column, "forward_return", "long_mfe", "long_mae", "short_mfe", "short_mae"]
    data = frame.dropna(subset=required)
    if data.empty:
        return data, None
    calibration = data
    if calibration_window is not None:
        calibration = data.loc[data["regime_window"] == calibration_window]
    if calibration.empty:
        return data.iloc[0:0].copy(), None
    quantile = 0.80 if tail == "high" else 0.20
    threshold = float(calibration[column].quantile(quantile))
    mask = data[column] >= threshold if tail == "high" else data[column] <= threshold
    return data.loc[mask].copy(), threshold


def top_evaluations(evaluations: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    return sorted(
        evaluations,
        key=lambda item: (item["sample"] > 0, item["mean_after_fee_pct"]),
        reverse=True,
    )[:limit]


def _annotate_windows(frame: pd.DataFrame, regime_label: str, horizon: int, timeframe: str) -> pd.DataFrame:
    out = frame.copy()
    out["regime_window"] = pd.NA
    horizon_delta = pd.to_timedelta(horizon * int(timeframe[:-1]), unit="m")
    for window in active_windows_for_label(regime_label):
        start = pd.Timestamp(window["start"], tz="UTC")
        end = pd.Timestamp(window["end"], tz="UTC") + pd.Timedelta(days=1)
        mask = (out["date"] >= start) & ((out["date"] + horizon_delta) < end)
        out.loc[mask, "regime_window"] = window["name"]
    return out


def _feature_frames(pairs: list[str], timeframe: str) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    context_pairs = pairs_for_scope("research_all")
    raw_frames: dict[str, pd.DataFrame] = {}
    audits: list[dict[str, Any]] = []
    for pair in context_pairs:
        path = pair_data_path(pair, timeframe)
        if path.exists():
            raw_frames[pair] = load_frame(pair, timeframe)
    featured = {
        pair: add_ohlcv_features(frame, REALISTIC_ROUND_TRIP_FRICTION)
        for pair, frame in raw_frames.items()
    }
    context = build_cross_asset_context(featured)
    output: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        path = pair_data_path(pair, timeframe)
        audit: dict[str, Any] = {"pair": pair, "timeframe": timeframe, "path": rel(path), "exists": path.exists()}
        if not path.exists():
            audits.append(audit)
            continue
        frame = attach_cross_asset_features(featured[pair], pair, context)
        frame, aux_audits = attach_registered_auxiliary_features(
            frame, pair, DATA_ROOT, AUX_ROOT, CACHE_ROOT
        )
        audit.update(
            {
                "rows": int(len(frame)),
                "first_utc": frame["date"].iloc[0].isoformat(),
                "last_utc": frame["date"].iloc[-1].isoformat(),
                "auxiliary": [{**item, "path": rel(Path(item["path"]))} if item.get("path") else item for item in aux_audits],
            }
        )
        output[pair] = frame
        audits.append(audit)
    return output, audits


def evaluate_pair_timeframe(
    pair: str,
    timeframe: str,
    frame: pd.DataFrame,
    regime_label: str | None,
    allowed_sides: tuple[str, ...],
    domains: set[str] | None,
) -> list[dict[str, Any]]:
    horizon = {"3m": 12, "5m": 12, "15m": 8}[timeframe]
    frame = add_forward_labels(frame, horizon)
    protocol = regime_window_protocol(regime_label)
    if regime_label:
        frame = _annotate_windows(frame, regime_label, horizon, timeframe)
        frame = frame.loc[regime_entry_mask(frame, regime_label, horizon, timeframe)].copy()
    rows: list[dict[str, Any]] = []
    for spec in FACTOR_SPECS:
        if domains and spec.domain not in domains:
            continue
        available = spec.column in frame.columns and frame[spec.column].notna().any()
        if not available:
            rows.append(
                {
                    "pair": pair,
                    "timeframe": timeframe,
                    "horizon_bars": horizon,
                    "factor": spec.name,
                    "factor_column": spec.column,
                    "description": spec.description,
                    "domain": spec.domain,
                    "data_requirement": spec.data_requirement,
                    "knowledge_cards": list(spec.knowledge_cards),
                    "tail": "unavailable",
                    "side": "none",
                    **side_score(pd.DataFrame(columns=["forward_return", "long_mfe", "long_mae", "short_mfe", "short_mae"]), "long", regime_label=regime_label),
                    "verdict": "data_unavailable",
                }
            )
            continue
        for tail in spec.tails:
            required = [
                spec.column,
                "forward_return",
                "long_mfe",
                "long_mae",
                "short_mfe",
                "short_mae",
            ]
            eligible = frame.dropna(subset=required)
            development_window = protocol["development_window"]
            calibration_sample = (
                int((eligible["regime_window"] == development_window).sum())
                if development_window is not None
                else int(len(eligible))
            )
            raw, threshold = quantile_sample(
                frame,
                spec.column,
                tail,
                calibration_window=development_window,
            )
            sample = decluster_sample(raw, horizon)
            for side in allowed_sides:
                score = side_score(
                    sample,
                    side,
                    raw_sample=len(raw),
                    regime_label=regime_label,
                    window_roles=protocol["window_roles"],
                )
                rows.append(
                    {
                        "pair": pair,
                        "timeframe": timeframe,
                        "horizon_bars": horizon,
                        "factor": spec.name,
                        "factor_column": spec.column,
                        "description": spec.description,
                        "domain": spec.domain,
                        "data_requirement": spec.data_requirement,
                        "knowledge_cards": list(spec.knowledge_cards),
                        "tail": tail,
                        "quantile_threshold": threshold,
                        "quantile_threshold_source": protocol["threshold_source"],
                        "quantile_calibration_sample": calibration_sample,
                        "development_window": development_window,
                        "validation_windows": protocol["validation_windows"],
                        "side": side,
                        **score,
                    }
                )
    return rows


def _resolve_target(auto_target: bool, explicit_regime: str | None) -> tuple[ResearchTarget | None, str | None, tuple[str, ...], str]:
    target = load_current_research_target() if auto_target else None
    regime_label = explicit_regime or (target.regime_label if target else None)
    if target and target.action == "no_trade" and explicit_regime is None:
        return target, None, (), "allocator_no_research_target"
    sides = target.allowed_sides if target and target.allowed_sides else ("long", "short")
    source = "explicit_regime" if explicit_regime else (target.selection_source if target else "all_history_diagnostic")
    return target, regime_label, sides, source


def build_payload(
    pair_scope: str,
    regime_label: str | None,
    *,
    auto_target: bool = False,
    domains: set[str] | None = None,
) -> dict[str, Any]:
    target, selected_regime, allowed_sides, selection_source = _resolve_target(auto_target, regime_label)
    pairs = pairs_for_scope(pair_scope)
    evaluations: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    blocked = target is not None and target.action == "no_trade" and regime_label is None
    if not blocked:
        for timeframe in TIMEFRAMES:
            frames, timeframe_audits = _feature_frames(pairs, timeframe)
            audits.extend(timeframe_audits)
            for pair, frame in frames.items():
                evaluations.extend(
                    evaluate_pair_timeframe(
                        pair,
                        timeframe,
                        frame,
                        selected_regime,
                        allowed_sides,
                        domains,
                    )
                )
    gross_candidates = [item for item in evaluations if item.get("gross_gate") == "pass"]
    candidates = [item for item in evaluations if item.get("verdict") == "edge_candidate"]
    domain_summary: dict[str, dict[str, int]] = {}
    for item in evaluations:
        bucket = domain_summary.setdefault(item["domain"], {"evaluations": 0, "gross_candidates": 0, "edge_candidates": 0})
        bucket["evaluations"] += 1
        bucket["gross_candidates"] += int(item.get("gross_gate") == "pass")
        bucket["edge_candidates"] += int(item.get("verdict") == "edge_candidate")
    if blocked:
        verdict = "blocked_by_allocator_no_research_target"
    elif not selected_regime:
        verdict = "all_history_diagnostic_only"
    elif candidates:
        verdict = "has_regime_replicated_edge_candidates"
    elif gross_candidates:
        verdict = "gross_edge_failed_cost_or_replication"
    else:
        verdict = "no_gross_edge_candidates"
    return {
        "generated_at_utc": now_utc(),
        "research_only": True,
        "factor_research_method_version": FACTOR_RESEARCH_METHOD_VERSION,
        "market": "Binance USDT-M futures",
        "pair_scope": pair_scope,
        "regime_label": selected_regime,
        "selection_source": selection_source,
        "research_target": target.as_dict() if target else None,
        "target_family_codes": list(target.family_codes) if target else [],
        "allowed_sides": list(allowed_sides),
        "regime_windows": [item["name"] for item in active_windows_for_label(selected_regime)] if selected_regime else [],
        "threshold_protocol": regime_window_protocol(selected_regime),
        "timeframes": TIMEFRAMES,
        "pairs": pairs,
        "feature_registry": registry_payload(),
        "selected_domains": sorted(domains) if domains else "all",
        "cost_scenario": REALISTIC_SCENARIO.name,
        "fee_single_side": REALISTIC_SCENARIO.fee,
        "slippage_bps": REALISTIC_SCENARIO.slippage_bps,
        "round_trip_friction": REALISTIC_ROUND_TRIP_FRICTION,
        "execution_label_contract": {
            "signal_information": "completed_signal_candle_only",
            "entry_price": "next_candle_open",
            "entry_lag_bars": 1,
            "exit_price": "signal_plus_horizon_close",
            "excursion_window": "next_candle_through_signal_plus_horizon",
            "mae_floor": 0.0,
        },
        "edge_gate": {
            "sequence": [
                "development_window_threshold_freeze",
                "gross_edge",
                "realistic_cost",
                "independent_regime_windows",
            ],
            "min_independent_sample": MIN_INDEPENDENT_SAMPLE,
            "gross_mean_gt_round_trip_friction": REALISTIC_ROUND_TRIP_FRICTION,
            "gross_win_rate_gte": MIN_GROSS_WIN_RATE,
            "mfe_mae_ratio_gte": MIN_MFE_MAE_RATIO,
            "net_win_rate_gte": MIN_NET_WIN_RATE,
            "positive_independent_windows_required": 2,
            "positive_validation_windows_required": 1,
        },
        "data_audit": audits,
        "evaluations": evaluations,
        "gross_candidates": gross_candidates,
        "edge_candidates": candidates,
        "domain_summary": domain_summary,
        "summary": {
            "evaluations": len(evaluations),
            "gross_candidates": len(gross_candidates),
            "edge_candidates": len(candidates),
            "verdict": verdict,
        },
        "blocked_reason": target.reason if blocked and target else None,
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    target = payload.get("research_target") or {}
    lines = [
        "# Regime-Targeted Factor Research",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Selection: `{payload['selection_source']}`",
        f"- Deployment state: `{target.get('current_state', 'not loaded')}`",
        f"- Target regime: `{payload['regime_label'] or 'none (diagnostic only)'}`",
        f"- Target families: `{', '.join(payload['target_family_codes']) or 'unscoped'}`",
        f"- Allowed sides: `{', '.join(payload['allowed_sides']) or 'none'}`",
        f"- Pair scope: `{payload['pair_scope']}`",
        f"- Regime windows: `{', '.join(payload['regime_windows']) or 'none'}`",
        f"- Development window: `{payload['threshold_protocol']['development_window'] or 'all-history diagnostic'}`",
        f"- Validation windows: `{', '.join(payload['threshold_protocol']['validation_windows']) or 'none'}`",
        f"- Verdict: `{payload['summary']['verdict']}`",
        f"- Gross candidates: `{payload['summary']['gross_candidates']}`; cost + replication candidates: `{payload['summary']['edge_candidates']}`",
        "",
        "## Gate Order",
        "",
        "1. Freeze each factor-tail threshold on the earliest chronological home episode only.",
        "2. Apply that unchanged threshold to later independent home episodes.",
        "3. Enter at the next candle open, then check gross forward edge and zero-clamped MFE/MAE on de-clustered events.",
        "4. Apply the realistic fee + slippage cost gate.",
        "5. Require positive evidence in at least two windows, including one validation episode.",
        "",
        "## Edge Candidates",
        "",
        "| Pair | TF | Domain | Factor | Tail | Side | Raw | Independent | Gross % | Net % | Gross Win | Net Win | Windows | Validation + | Verdict |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in payload["edge_candidates"]:
        lines.append(
            f"| {item['pair']} | {item['timeframe']} | {item['domain']} | {item['factor']} | {item['tail']} | {item['side']} | "
            f"{item['raw_sample']} | {item['independent_sample']} | {item['mean_forward_return_pct']} | {item['mean_after_fee_pct']} | "
            f"{item['gross_win_rate']} | {item['win_rate']} | {item['positive_independent_windows']} | "
            f"{item['positive_validation_windows']} | {item['verdict']} |"
        )
    if not payload["edge_candidates"]:
        lines.append("| none | - | - | - | - | - | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | blocked |")
    lines.extend(["", "## Domain Coverage", "", "| Domain | Evaluations | Gross candidates | Final candidates |", "|---|---:|---:|---:|"])
    for domain, counts in sorted(payload["domain_summary"].items()):
        lines.append(f"| {domain} | {counts['evaluations']} | {counts['gross_candidates']} | {counts['edge_candidates']} |")
    lines.extend(
        [
            "",
            "## Contract",
            "",
            "- All-history runs are diagnostics and cannot replace the current allocator-target report.",
            "- Missing derivatives or microstructure data stays missing; it is never zero-filled.",
            "- Validation episodes never participate in quantile threshold calibration.",
            "- Signal-candle features are evaluated only after that candle completes; execution labels enter at the next candle open.",
            "- An edge candidate is still only an event hypothesis. It cannot directly create a strategy class.",
            "- This layer does not modify Freqtrade config, registry, dry-run, live config, or exchange credentials.",
        ]
    )
    if payload.get("blocked_reason"):
        lines.extend(["", f"Blocked reason: {payload['blocked_reason']}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-scope", choices=["core", "extension", "research_all"], default="core")
    parser.add_argument("--regime-label", choices=REGIME_LABELS, default=None)
    parser.add_argument("--auto-target", action="store_true", help="Use the independent research allocator to choose regime, family, and side.")
    parser.add_argument(
        "--feature-domain",
        action="append",
        choices=["price_action", "regime", "derivatives", "microstructure", "cross_asset"],
        default=None,
        help="Restrict to one or more typed feature domains.",
    )
    parser.add_argument(
        "--publish-all-history-current",
        action="store_true",
        help="Explicit escape hatch for a diagnostic pointer; normal workflows must not use this.",
    )
    return parser.parse_args()


def should_publish_current(args: argparse.Namespace, payload: dict[str, Any]) -> bool:
    # An allocator-authorized no-allocation result is current truth, not an
    # all-history diagnostic. Publish it so stale family evidence cannot remain
    # behind the generic latest pointer.
    return bool(
        args.auto_target
        or payload.get("regime_label")
        or args.publish_all_history_current
    )


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload(
        args.pair_scope,
        args.regime_label,
        auto_target=args.auto_target,
        domains=set(args.feature_domain) if args.feature_domain else None,
    )
    timestamp = payload["generated_at_utc"]
    json_path = OUTPUT_DIR / f"factor_research_{timestamp}.json"
    md_path = OUTPUT_DIR / f"factor_research_{timestamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(md_path, payload)
    publish_current = should_publish_current(args, payload)
    publish_report(
        payload=payload,
        json_path=json_path,
        md_path=md_path,
        index_path=INDEX_JSON,
        artifact_type="factor_research",
        generic_latest_json=LATEST_JSON,
        generic_latest_md=LATEST_MD,
        publish_current=publish_current,
    )
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Indexed {rel(INDEX_JSON)}")
    print(payload["summary"]["verdict"])


if __name__ == "__main__":
    main()
