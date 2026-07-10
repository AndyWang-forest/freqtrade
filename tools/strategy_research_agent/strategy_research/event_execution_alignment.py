#!/usr/bin/env python3
"""Compare event-study signals with real Freqtrade backtest executions.

This is a research guardrail between pandas event studies and Freqtrade
strategy validation. It answers: did the same event actually become a trade,
how late did Freqtrade enter, and did trade PnL preserve the event forward
return after realistic execution rules?
"""

from __future__ import annotations

import argparse
import csv
import inspect
import importlib.util
import json
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from cost_model import PRIMARY_SCENARIO


def find_repo_root() -> Path:
    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
REPORT_DIR = AGENT_ROOT / "event_execution_alignment"
LATEST_JSON = REPORT_DIR / "latest_event_execution_alignment.json"
LATEST_MD = REPORT_DIR / "latest_event_execution_alignment.md"
DEFAULT_TARGETS = AGENT_ROOT / "event_execution_alignment_targets.json"
STARTING_BALANCE = 1000.0


@dataclass
class AlignmentRow:
    target: str
    strategy: str
    event: str
    pair: str
    timeframe: str
    timerange: str
    event_time: str
    expected_entry_time: str
    event_forward_pct: float
    status: str
    matched_trade_open: str
    entry_delay_candles: int
    exit_reason: str
    trade_profit_pct: float
    trade_duration_min: int
    blocked_by_trade_open: str


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load event module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def timeframe_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def parse_timerange(timerange: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_s, end_s = timerange.split("-")
    return (
        pd.to_datetime(start_s, format="%Y%m%d", utc=True),
        pd.to_datetime(end_s, format="%Y%m%d", utc=True),
    )


def target_from_experiment_csv(target: dict[str, Any]) -> dict[str, Any]:
    csv_path = REPO_ROOT / target["experiment_csv"]
    strategy = target["strategy"]
    window = target["window"]
    scenario = target.get("scenario", PRIMARY_SCENARIO)
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("strategy") == strategy and row.get("window") == window and row.get("scenario") == scenario:
                target = dict(target)
                target["artifact"] = row["artifact"]
                target["timerange"] = row["timerange"]
                return target
    raise RuntimeError(f"No matching row in {csv_path}: strategy={strategy} window={window} scenario={scenario}")


def load_trades(artifact: Path, strategy: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(artifact) as zf:
        result_name = next(
            name
            for name in zf.namelist()
            if name.endswith(".json") and not name.endswith("_config.json") and not name.endswith(".meta.json")
        )
        payload = json.loads(zf.read(result_name))
    return payload["strategy"].get(strategy, {}).get("trades", [])


def build_frame(module: Any, pair_key: str, timeframe: str) -> pd.DataFrame:
    if hasattr(module, "build_frames"):
        frames = module.build_frames()
        return frames[(pair_key, timeframe)].copy()
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    pairs = list(getattr(module, "PAIRS", [pair_key]))
    timeframes = list(getattr(module, "TIMEFRAMES", [timeframe]))
    for pair in pairs:
        for tf in timeframes:
            if pair == pair_key or tf == timeframe or hasattr(module, "add_cross_pair_context"):
                try:
                    params = inspect.signature(module.load_pair).parameters
                    if len(params) >= 2:
                        frame = module.load_pair(pair, tf)
                    else:
                        frame = module.load_pair(pair)
                    if hasattr(module, "add_indicators"):
                        frame = module.add_indicators(frame)
                    frames[(pair, tf)] = frame
                except Exception:
                    continue
    if hasattr(module, "add_cross_pair_context"):
        module.add_cross_pair_context(frames)
    return frames[(pair_key, timeframe)].copy()


def event_forward_stats(module: Any, df: pd.DataFrame, mask: pd.Series, side: str, horizon_bars: int) -> pd.Series:
    import inspect

    if not hasattr(module, "forward_stats"):
        signed = 1.0 if side == "long" else -1.0
        return (signed * (df["close"].shift(-horizon_bars) / df["close"] - 1.0) * 50.0 * 100.0)[mask].dropna()
    params = inspect.signature(module.forward_stats).parameters
    if len(params) >= 4:
        ret, _mfe, _mae = module.forward_stats(df, mask, side, horizon_bars)
    else:
        ret, _mfe, _mae = module.forward_stats(df, mask, horizon_bars)
    return ret


def resolve_horizon_bars(module: Any, timeframe: str, horizon: str) -> int:
    if hasattr(module, "horizon_map"):
        return int(module.horizon_map(timeframe)[horizon])
    minutes = timeframe_minutes(horizon) if horizon.endswith(("m", "h")) else int(horizon)
    return max(minutes // timeframe_minutes(timeframe), 1)


def build_events(target: dict[str, Any]) -> tuple[pd.DataFrame, pd.Series, pd.Series, str, pd.Timestamp]:
    module = load_module(REPO_ROOT / target["event_module"])
    pair_key = target["pair_key"]
    timeframe = target["timeframe"]
    event = target["event"]
    side_override = target.get("side")
    df = build_frame(module, pair_key, timeframe)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    startup_candles = int(target.get("startup_candles", 0))
    if df.empty or len(df) <= startup_candles:
        warmup_cutoff = pd.Timestamp.max.tz_localize("UTC")
    else:
        warmup_cutoff = df.iloc[startup_candles]["date"]
    start, end = parse_timerange(target["timerange"])
    df = df[(df["date"] >= start) & (df["date"] < end)].copy()
    if hasattr(module, "event_masks"):
        params = inspect.signature(module.event_masks).parameters
        masks = module.event_masks(df, timeframe) if len(params) >= 2 else module.event_masks(df)
    elif hasattr(module, "signals"):
        masks = module.signals(df)
    else:
        raise RuntimeError(f"Event module has no event_masks() or signals(): {target['event_module']}")
    event_value = masks[event]
    event_tuple = event_value if isinstance(event_value, tuple) else (target.get("family", "event_signal"), "", event_value)
    family = event_tuple[0]
    tuple_side = event_tuple[1] if len(event_tuple) > 1 else ""
    side = side_override or (tuple_side if tuple_side in {"long", "short"} else "short")
    mask = event_tuple[-1].fillna(False)
    horizon_bars = resolve_horizon_bars(module, timeframe, target.get("horizon", "8h"))
    ret = event_forward_stats(module, df, mask, side, horizon_bars)
    return df, mask, ret, family, warmup_cutoff


def trade_open_time(trade: dict[str, Any]) -> pd.Timestamp:
    return pd.to_datetime(trade["open_date"], utc=True)


def trade_close_time(trade: dict[str, Any]) -> pd.Timestamp:
    return pd.to_datetime(trade["close_date"], utc=True)


def classify_target(target: dict[str, Any]) -> tuple[dict[str, Any], list[AlignmentRow]]:
    if target.get("experiment_csv") and not target.get("artifact"):
        target = target_from_experiment_csv(target)
    if not target.get("artifact"):
        raise RuntimeError(f"Missing backtest artifact for target: {target.get('name') or target.get('strategy')}")
    artifact = REPO_ROOT / target["artifact"]
    strategy = target["strategy"]
    trades = load_trades(artifact, strategy)
    df, mask, ret, family, warmup_cutoff = build_events(target)

    tf_minutes = timeframe_minutes(target["timeframe"])
    lag = int(target.get("entry_lag_candles", 1))
    tolerance = pd.Timedelta(minutes=tf_minutes * int(target.get("match_tolerance_candles", 2)))
    pair = target["pair"]
    trade_rows = []
    for trade in trades:
        if trade.get("pair") and trade.get("pair") != pair:
            continue
        trade_rows.append((trade_open_time(trade), trade_close_time(trade), trade))
    trade_rows.sort(key=lambda item: item[0])

    rows: list[AlignmentRow] = []
    events = df.loc[mask, ["date"]].copy()
    events["event_forward_pct"] = ret.reindex(events.index)
    for _, event_row in events.dropna(subset=["event_forward_pct"]).iterrows():
        event_time = pd.to_datetime(event_row["date"], utc=True)
        expected_entry = event_time + pd.Timedelta(minutes=tf_minutes * lag)
        matched = None
        blocked_by = ""
        status = "unused_signal"
        if event_time < warmup_cutoff:
            status = "blocked_by_startup"
        else:
            for open_time, _close_time, trade in trade_rows:
                if open_time >= expected_entry and open_time - expected_entry <= tolerance:
                    matched = (open_time, trade)
                    status = "executed"
                    break
            if matched is None:
                for open_time, close_time, trade in trade_rows:
                    if open_time <= expected_entry < close_time:
                        status = "blocked_by_existing_trade"
                        blocked_by = open_time.isoformat()
                        break
        trade = matched[1] if matched else None
        open_time = matched[0] if matched else None
        entry_delay = int(round((open_time - event_time).total_seconds() / 60 / tf_minutes)) if open_time is not None else 0
        rows.append(
            AlignmentRow(
                target=target.get("name") or f"{strategy}:{target['event']}",
                strategy=strategy,
                event=target["event"],
                pair=pair,
                timeframe=target["timeframe"],
                timerange=target["timerange"],
                event_time=event_time.isoformat(),
                expected_entry_time=expected_entry.isoformat(),
                event_forward_pct=round(float(event_row["event_forward_pct"]), 4),
                status=status,
                matched_trade_open=open_time.isoformat() if open_time is not None else "",
                entry_delay_candles=entry_delay,
                exit_reason=trade.get("exit_reason", "") if trade else "",
                trade_profit_pct=round(float(trade.get("profit_ratio") or 0.0) * 100, 4) if trade else 0.0,
                trade_duration_min=int(trade.get("trade_duration") or 0) if trade else 0,
                blocked_by_trade_open=blocked_by,
            )
        )
    summary = {
        "target": target.get("name") or f"{strategy}:{target['event']}",
        "strategy": strategy,
        "family": family,
        "event": target["event"],
        "pair": pair,
        "timeframe": target["timeframe"],
        "timerange": target["timerange"],
        "artifact": rel(artifact),
        "event_count": len(rows),
        "executed_count": sum(1 for row in rows if row.status == "executed"),
        "blocked_by_startup": sum(1 for row in rows if row.status == "blocked_by_startup"),
        "blocked_by_existing_trade": sum(1 for row in rows if row.status == "blocked_by_existing_trade"),
        "unused_signal": sum(1 for row in rows if row.status == "unused_signal"),
    }
    executed = [row for row in rows if row.status == "executed"]
    unique_trade_opens = {row.matched_trade_open for row in executed if row.matched_trade_open}
    summary["unique_executed_trades"] = len(unique_trade_opens)
    summary["duplicate_event_matches"] = max(len(executed) - len(unique_trade_opens), 0)
    if rows:
        summary["event_forward_mean_pct"] = round(sum(row.event_forward_pct for row in rows) / len(rows), 4)
    if executed:
        summary["executed_event_forward_mean_pct"] = round(sum(row.event_forward_pct for row in executed) / len(executed), 4)
        summary["executed_trade_profit_mean_pct"] = round(sum(row.trade_profit_pct for row in executed) / len(executed), 4)
        summary["execution_gap_mean_pct"] = round(
            sum(row.trade_profit_pct - row.event_forward_pct for row in executed) / len(executed),
            4,
        )
    return summary, rows


def load_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.target_file:
        return json.loads((REPO_ROOT / args.target_file).read_text(encoding="utf-8")).get("targets", [])
    if args.strategy:
        target = {
            "name": args.name or f"{args.strategy}:{args.event}",
            "strategy": args.strategy,
            "event_module": args.event_module,
            "event": args.event,
            "pair": args.pair,
            "pair_key": args.pair_key,
            "timeframe": args.timeframe,
            "timerange": args.timerange,
            "artifact": args.artifact,
            "experiment_csv": args.experiment_csv,
            "window": args.window,
            "scenario": args.scenario,
            "side": args.side,
            "horizon": args.horizon,
            "startup_candles": args.startup_candles,
        }
        return [{key: value for key, value in target.items() if value is not None}]
    if DEFAULT_TARGETS.exists():
        return json.loads(DEFAULT_TARGETS.read_text(encoding="utf-8")).get("targets", [])
    return []


def write_outputs(summaries: list[dict[str, Any]], rows: list[AlignmentRow]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = REPORT_DIR / f"event_execution_alignment_{ts}.json"
    md_path = REPORT_DIR / f"event_execution_alignment_{ts}.md"
    payload = {
        "generated_at_utc": ts,
        "research_only": True,
        "summary_count": len(summaries),
        "summaries": summaries,
        "rows": [asdict(row) for row in rows],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    LATEST_JSON.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    lines = [
        "# Event-To-Freqtrade Execution Alignment",
        "",
        f"- Generated UTC: `{ts}`",
        f"- Targets: `{len(summaries)}`",
        "",
        "This gate compares local event-study signals with actual Freqtrade backtest trades. It is a guardrail against promoting event-study edge that cannot be executed by Freqtrade order, timing, startup, max-open-trades, or exit rules.",
        "",
        "## Target Summary",
        "",
        "| Target | Strategy | Event | Window | Events | Event matches | Unique trades | Duplicate matches | Startup blocked | Trade-overlap blocked | Unused | Event mean % | Executed event mean % | Trade mean % | Gap % |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['target']} | `{item['strategy']}` | `{item['event']}` | `{item['timerange']}` | "
            f"{item['event_count']} | {item['executed_count']} | {item.get('unique_executed_trades', 0)} | "
            f"{item.get('duplicate_event_matches', 0)} | {item['blocked_by_startup']} | "
            f"{item['blocked_by_existing_trade']} | {item['unused_signal']} | "
            f"{item.get('event_forward_mean_pct', 0.0):.4f} | {item.get('executed_event_forward_mean_pct', 0.0):.4f} | "
            f"{item.get('executed_trade_profit_mean_pct', 0.0):.4f} | {item.get('execution_gap_mean_pct', 0.0):.4f} |"
        )
    lines.extend(
        [
            "",
            "## Diagnosis Rules",
            "",
            "- If event count is high but executed count is low, the event study is overstating executable opportunities.",
            "- If executed event mean is positive but trade mean is negative, inspect entry timing, order fill, ROI/stoploss, time-stop, and protection interaction before changing thresholds.",
            "- If startup or existing-trade blocks dominate, treat the event edge as clustered or startup-dependent rather than a clean strategy candidate.",
            "",
            "## Event Rows",
            "",
            "| Target | Event time | Status | Event fwd % | Expected entry | Trade open | Delay candles | Exit | Trade % | Blocked by |",
            "|---|---|---|---:|---|---|---:|---|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.target} | {row.event_time} | `{row.status}` | {row.event_forward_pct:.4f} | "
            f"{row.expected_entry_time} | {row.matched_trade_open} | {row.entry_delay_candles} | "
            f"{row.exit_reason} | {row.trade_profit_pct:.4f} | {row.blocked_by_trade_open} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LATEST_MD.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-file")
    parser.add_argument("--name")
    parser.add_argument("--strategy")
    parser.add_argument("--event-module")
    parser.add_argument("--event")
    parser.add_argument("--pair")
    parser.add_argument("--pair-key")
    parser.add_argument("--timeframe")
    parser.add_argument("--timerange")
    parser.add_argument("--artifact")
    parser.add_argument("--experiment-csv")
    parser.add_argument("--window")
    parser.add_argument("--scenario", default=PRIMARY_SCENARIO)
    parser.add_argument("--side")
    parser.add_argument("--horizon", default="8h")
    parser.add_argument("--startup-candles", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    targets = load_targets(args)
    if not targets:
        summary = {
            "target": "no_targets_configured",
            "strategy": "",
            "family": "",
            "event": "",
            "pair": "",
            "timeframe": "",
            "timerange": "",
            "artifact": "",
            "event_count": 0,
            "executed_count": 0,
            "blocked_by_startup": 0,
            "blocked_by_existing_trade": 0,
            "unused_signal": 0,
        }
        json_path, md_path = write_outputs([summary], [])
        print(f"Wrote {rel(json_path)}")
        print(f"Wrote {rel(md_path)}")
        return
    summaries: list[dict[str, Any]] = []
    all_rows: list[AlignmentRow] = []
    for target in targets:
        summary, rows = classify_target(target)
        summaries.append(summary)
        all_rows.extend(rows)
    json_path, md_path = write_outputs(summaries, all_rows)
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")


if __name__ == "__main__":
    main()
