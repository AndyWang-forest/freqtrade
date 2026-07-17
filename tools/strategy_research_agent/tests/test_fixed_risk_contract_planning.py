from __future__ import annotations

import sys
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

import plan_memory_guided_hypotheses as planner  # noqa: E402


def test_all_memory_guided_plans_keep_fixed_50x_leverage() -> None:
    blockers = set(planner.BLOCKER_TO_CONCEPTS) | {
        "bias_checks_missing",
        "lookahead_or_recursive_unverified",
        "matrix_not_tested",
        "insufficient_sample",
        "cost_not_estimated",
        "walk_forward_not_passed",
        "unknown_blocker",
    }

    for blocker in blockers:
        proposal = planner.risk_template(blocker)
        assert proposal["leverage_change"] == "none_fixed_50x"
        assert proposal["leverage_cap"] == 50.0
