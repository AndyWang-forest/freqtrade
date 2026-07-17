#!/usr/bin/env python3
"""Build strategy scorecards and failure diagnostics from local research artifacts."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from repo_paths import find_repo_root
from typing import Any


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
REPORT_DIR = AGENT_ROOT / "strategy_assessments"
POOL_DIRS = [
    AGENT_ROOT / "candidates",
    AGENT_ROOT / "watchlist",
]
REGISTRY_JSON = AGENT_ROOT / "strategy_registry.json"
REJECTED_DIR = AGENT_ROOT / "rejected"
PROMOTION_JSON = AGENT_ROOT / "promotion_reports/latest_promotion_report.json"


@dataclass
class Scorecard:
    strategy: str
    evidence_scope: str
    source_pool: str | None
    source_path: str | None
    evidence_artifacts: list[str]
    active_state: str | None
    gate_blockers: list[str]
    tier: str
    score: int | None
    base_return_pct: float | None
    adjusted_return_pct: float | None
    market_change_pct: float | None
    profit_factor: float | None
    max_drawdown_pct: float | None
    trades: int | None
    matrix_verdict: str | None
    positive_matrix_runs: int | None
    stress_negative_runs: int | None
    too_few_trade_runs: int | None
    primary_failures: list[str]
    next_actions: list[str]


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def latest_report_path() -> Path | None:
    index = load_json(AGENT_ROOT / "reports/agent_report_index.json")
    if not index:
        return None
    latest = index.get("latest_report") or index.get("latest_dashboard_refresh")
    if not latest:
        return None
    return REPO_ROOT / latest["path"]


def load_pool_metrics() -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for directory in POOL_DIRS:
        for path in directory.glob("*.json"):
            item = load_json(path)
            if not item:
                continue
            strategy = item.get("strategy") or item.get("name")
            if not strategy:
                continue
            current = metrics.setdefault(strategy, {})
            current.update(item)
            current["pool"] = directory.name
            current["pool_path"] = rel_path(path)
    return metrics


def load_registry_metrics() -> dict[str, dict[str, Any]]:
    registry = load_json(REGISTRY_JSON) or {}
    metrics: dict[str, dict[str, Any]] = {}
    for item in registry.get("strategies", []):
        strategy = item.get("name") or item.get("strategy")
        if not strategy:
            continue
        metrics[strategy] = {
            **item,
            "strategy": strategy,
            "pool": "registry",
            "pool_path": rel_path(REGISTRY_JSON),
        }
    return metrics


def index_by_strategy(items: list[dict[str, Any]], key: str = "strategy") -> dict[str, dict[str, Any]]:
    return {item[key]: item for item in items if item.get(key)}


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def add_points(score: int, failures: list[str], condition: bool, points: int, failure: str) -> int:
    if condition:
        return score + points
    failures.append(failure)
    return score


def tier(score: int, failures: list[str]) -> str:
    hard_failures = {
        "negative_after_cost",
        "fragile_matrix",
        "too_few_matrix_trades",
        "stress_cost_failure",
        "lookahead_or_recursive_unverified",
    }
    if score >= 80 and not hard_failures.intersection(failures):
        return "promotable_research_candidate"
    if score >= 60:
        return "watchlist"
    if score >= 40:
        return "needs_redesign"
    return "reject_or_archive"


def next_actions_for(failures: list[str]) -> list[str]:
    actions: list[str] = []
    if "too_few_matrix_trades" in failures:
        actions.append("放宽或重构入场确认，让 90 天窗口内交易数足够评估。")
    if "stress_cost_failure" in failures or "negative_after_cost" in failures:
        actions.append("提高单笔期望值，减少低边际交易；优先测试更严格的入场质量而不是提高杠杆。")
    if "cost_not_estimated" in failures:
        actions.append("先导出交易明细并运行 funding/滑点成本校正，再决定是否继续研究。")
    if "matrix_not_tested" in failures:
        actions.append("先纳入 bull/bear/range/high-vol 与 base/stress fee 矩阵，补齐韧性证据。")
    if "underperforms_market" in failures:
        actions.append("加入基准超额收益门槛，避免只得到低波动但跑输持有的策略。")
    if "weak_profit_factor" in failures:
        actions.append("分解进出场标签，检查亏损主要来自止损、信号失效还是震荡误入场。")
    if "fragile_matrix" in failures:
        actions.append("按 bull/bear/range/high-vol 分别建模，不要用同一套规则硬吃所有状态。")
    if "lookahead_or_recursive_unverified" in failures:
        actions.append("对进入 dry-run 候选前的版本运行 recursive-analysis 和 lookahead-analysis。")
    if not actions:
        actions.append("进入更长 dry-run，对比真实成交、滑点和回测差异。")
    return actions[:4]


def score_strategy(
    strategy: str,
    base: dict[str, Any],
    matrix: dict[str, Any] | None,
    costs: dict[str, Any] | None,
) -> Scorecard:
    evidence_artifacts = []
    evidence = base.get("evidence")
    if isinstance(evidence, dict):
        for value in evidence.values():
            if isinstance(value, str):
                evidence_artifacts.append(value)
            elif isinstance(value, list):
                evidence_artifacts.extend(str(item) for item in value if isinstance(item, str))
    elif isinstance(evidence, list):
        evidence_artifacts.extend(str(item) for item in evidence)

    quantitative_keys = {
        "total_profit_pct",
        "profit_factor",
        "max_drawdown_pct",
        "trades",
        "market_change_pct",
    }
    if not any(base.get(key) is not None for key in quantitative_keys):
        return Scorecard(
            strategy=strategy,
            evidence_scope="active_registry_candidate_watchlist",
            source_pool=base.get("pool"),
            source_path=base.get("pool_path"),
            evidence_artifacts=evidence_artifacts,
            active_state=base.get("promotion_state") or base.get("classification") or base.get("state"),
            gate_blockers=list(base.get("promotion_blockers") or []),
            tier="registered_evidence_reference",
            score=None,
            base_return_pct=None,
            adjusted_return_pct=None,
            market_change_pct=None,
            profit_factor=None,
            max_drawdown_pct=None,
            trades=None,
            matrix_verdict=None,
            positive_matrix_runs=None,
            stress_negative_runs=None,
            too_few_trade_runs=None,
            primary_failures=["quantitative_evidence_not_materialized_in_registry"],
            next_actions=["Read the strategy's exact registered evidence artifacts before rescoring; do not infer zero performance from a registry pointer."],
        )

    failures: list[str] = []
    score = 0

    base_return = numeric(base.get("total_profit_pct"))
    adjusted_return = numeric(costs.get("adjusted_profit_pct")) if costs else None
    market_change = numeric(base.get("market_change_pct"))
    profit_factor = numeric(base.get("profit_factor"))
    max_drawdown = numeric(base.get("max_drawdown_pct"))
    trades = int(base["trades"]) if base.get("trades") is not None else None

    matrix_verdict = matrix.get("verdict") if matrix else None
    positive_runs = int(matrix["positive_runs"]) if matrix and matrix.get("positive_runs") is not None else None
    stress_negative = int(matrix["stress_negative_runs"]) if matrix and matrix.get("stress_negative_runs") is not None else None
    too_few = int(matrix["too_few_trade_runs"]) if matrix and matrix.get("too_few_trade_runs") is not None else None

    score = add_points(score, failures, base_return is not None and base_return > 0, 12, "negative_or_missing_return")
    score = add_points(score, failures, profit_factor is not None and profit_factor >= 1.2, 12, "weak_profit_factor")
    score = add_points(score, failures, max_drawdown is not None and max_drawdown <= 5, 10, "drawdown_too_high")
    score = add_points(score, failures, trades is not None and trades >= 50, 10, "too_few_full_sample_trades")
    score = add_points(
        score,
        failures,
        market_change is not None and base_return is not None and base_return >= market_change,
        10,
        "underperforms_market",
    )
    if costs:
        score = add_points(
            score,
            failures,
            adjusted_return is not None and adjusted_return > 0,
            12,
            "negative_after_cost",
        )
    else:
        failures.append("cost_not_estimated")

    if matrix:
        score = add_points(
            score,
            failures,
            matrix_verdict in {"watchlist", "robust_candidate"},
            12,
            "fragile_matrix",
        )
        score = add_points(
            score,
            failures,
            too_few is not None and too_few == 0,
            8,
            "too_few_matrix_trades",
        )
        score = add_points(
            score,
            failures,
            stress_negative is not None and stress_negative == 0,
            8,
            "stress_cost_failure",
        )
    else:
        failures.append("matrix_not_tested")
    score = add_points(
        score,
        failures,
        bool(base.get("recursive_analysis")) and bool(base.get("lookahead_analysis")),
        6,
        "lookahead_or_recursive_unverified",
    )

    computed_tier = tier(score, failures)
    active_state = base.get("promotion_state") or base.get("classification") or base.get("state")
    if base.get("pool") == "candidates":
        computed_tier = str(active_state or "research_candidate")
    elif base.get("pool") == "watchlist":
        computed_tier = "watchlist"
    gate_blockers = list(base.get("promotion_blockers") or [])
    next_actions = next_actions_for(failures)
    if gate_blockers:
        next_actions = ["Resolve the exact current promotion blocker: " + blocker for blocker in gate_blockers[:4]]

    return Scorecard(
        strategy=strategy,
        evidence_scope="active_registry_candidate_watchlist",
        source_pool=base.get("pool"),
        source_path=base.get("pool_path"),
        evidence_artifacts=evidence_artifacts,
        active_state=active_state,
        gate_blockers=gate_blockers,
        tier=computed_tier,
        score=score,
        base_return_pct=base_return,
        adjusted_return_pct=adjusted_return,
        market_change_pct=market_change,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown,
        trades=trades,
        matrix_verdict=matrix_verdict,
        positive_matrix_runs=positive_runs,
        stress_negative_runs=stress_negative,
        too_few_trade_runs=too_few,
        primary_failures=failures,
        next_actions=next_actions,
    )


def build_payload() -> dict[str, Any]:
    latest_report = load_json(latest_report_path()) if latest_report_path() else {}
    pool_metrics = load_registry_metrics()
    for strategy, item in load_pool_metrics().items():
        pool_metrics.setdefault(strategy, {}).update(item)
    matrix_payload = load_json(AGENT_ROOT / "matrix_summaries/latest_matrix_summary.json") or {}
    cost_payload = load_json(AGENT_ROOT / "cost_adjustments/latest_trade_cost_estimate.json") or {}
    promotion_payload = load_json(PROMOTION_JSON) or {}

    matrix_by_strategy = index_by_strategy(matrix_payload.get("strategy_summary", []))
    costs_by_strategy = index_by_strategy(cost_payload.get("estimates", []))
    promotion_by_strategy = index_by_strategy(promotion_payload.get("verdicts", []))

    active_names = set(pool_metrics)
    for item in latest_report.get("candidate_pool", []) + latest_report.get("watchlist_pool", []):
        strategy = item.get("strategy") or item.get("name")
        if strategy and strategy in active_names:
            pool_metrics.setdefault(strategy, {}).update(item)

    for item in latest_report.get("results", []):
        strategy = item.get("strategy") or item.get("name")
        if strategy and strategy in active_names:
            pool_metrics[strategy].update(item)

    for strategy, item in promotion_by_strategy.items():
        if strategy not in active_names:
            continue
        pool_metrics[strategy]["promotion_state"] = item.get("state") or item.get("verdict")
        pool_metrics[strategy]["promotion_blockers"] = item.get("blockers", [])

    scorecards = [
        asdict(score_strategy(strategy, base, matrix_by_strategy.get(strategy), costs_by_strategy.get(strategy)))
        for strategy, base in sorted(pool_metrics.items())
    ]

    failure_counts = Counter()
    for card in scorecards:
        failure_counts.update(card["primary_failures"])

    diagnostics_by_strategy = []
    matrix_rows = defaultdict(list)
    for row in matrix_payload.get("rows", []):
        if row.get("strategy"):
            matrix_rows[row["strategy"]].append(row)
    for card in scorecards:
        strategy = card["strategy"]
        rows = matrix_rows.get(strategy, [])
        worst_rows = sorted(
            rows,
            key=lambda item: (
                numeric(item.get("return_pct")) if numeric(item.get("return_pct")) is not None else 999,
                numeric(item.get("profit_factor")) if numeric(item.get("profit_factor")) is not None else 999,
            ),
        )[:3]
        diagnostics_by_strategy.append(
            {
                "strategy": strategy,
                "tier": card["tier"],
                "score": card["score"],
                "primary_failures": card["primary_failures"],
                "next_actions": card["next_actions"],
                "worst_matrix_rows": worst_rows,
            }
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "market_profile": "crypto / Binance USDT-M futures / BTC-ETH only",
        "assessment_scope": {
            "active_sources": ["strategy_registry", "candidates", "watchlist"],
            "rejected_archive_excluded_from_scoring": True,
            "rejected_archive_count": len(list(REJECTED_DIR.glob("*.json"))),
            "matrix_and_cost_join": "exact_strategy_name_only",
        },
        "score_definition": {
            "max_score": 100,
            "dimensions": [
                "positive return",
                "profit factor",
                "drawdown",
                "trade count",
                "market outperformance",
                "cost-adjusted return",
                "matrix robustness",
                "matrix trade sufficiency",
                "stress-cost survival",
                "bias-analysis verification",
            ],
        },
        "scorecards": sorted(
            scorecards,
            key=lambda item: (
                -(item["score"] if item["score"] is not None else -1),
                item["strategy"],
            ),
        ),
        "failure_summary": [
            {"failure": failure, "count": count}
            for failure, count in failure_counts.most_common()
        ],
        "diagnostics": diagnostics_by_strategy,
        "source_artifacts": {
            "strategy_registry": rel_path(REGISTRY_JSON),
            "latest_report": rel_path(latest_report_path()) if latest_report_path() else None,
            "matrix_summary": rel_path(AGENT_ROOT / "matrix_summaries/latest_matrix_summary.json"),
            "trade_cost_estimate": rel_path(AGENT_ROOT / "cost_adjustments/latest_trade_cost_estimate.json"),
            "promotion_report": rel_path(PROMOTION_JSON),
        },
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Strategy Scorecards and Failure Diagnostics",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Market profile: `{payload['market_profile']}`",
        "",
        "## Scorecards",
        "",
        "| Strategy | Tier | Score | Base % | Adjusted % | Market % | PF | DD % | Trades | Matrix | Failures |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in payload["scorecards"]:
        lines.append(
            "| {strategy} | {tier} | {score} | {base_return_pct} | {adjusted_return_pct} | {market_change_pct} | {profit_factor} | {max_drawdown_pct} | {trades} | {matrix_verdict} | {failures} |".format(
                failures=", ".join(item["primary_failures"]),
                **item,
            )
        )
    lines.extend(["", "## Failure Summary", "", "| Failure | Count |", "|---|---:|"])
    for item in payload["failure_summary"]:
        lines.append("| {failure} | {count} |".format(**item))
    lines.extend(["", "## Next Actions", ""])
    for item in payload["diagnostics"]:
        lines.append(f"### {item['strategy']}")
        for action in item["next_actions"]:
            lines.append(f"- {action}")
        if item["worst_matrix_rows"]:
            lines.append("- Worst matrix rows:")
            for row in item["worst_matrix_rows"]:
                lines.append(
                    "  - {experiment}/{regime}: return {return_pct}%, PF {profit_factor}, trades {trades}, reasons {reasons}".format(
                        experiment=row.get("experiment"),
                        regime=row.get("regime"),
                        return_pct=row.get("return_pct"),
                        profit_factor=row.get("profit_factor"),
                        trades=row.get("trades"),
                        reasons=", ".join(row.get("reasons", [])),
                    )
                )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    timestamp = payload["generated_at_utc"]
    json_path = REPORT_DIR / f"strategy_assessment_{timestamp}.json"
    md_path = REPORT_DIR / f"strategy_assessment_{timestamp}.md"
    latest_json = REPORT_DIR / "latest_strategy_assessment.json"
    latest_md = REPORT_DIR / "latest_strategy_assessment.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(md_path, payload)
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {rel_path(json_path)}")
    print(f"Wrote {rel_path(md_path)}")
    print(f"Wrote {rel_path(latest_json)}")
    print(f"Wrote {rel_path(latest_md)}")


if __name__ == "__main__":
    main()
