from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import factor_research  # noqa: E402


def test_factor_research_uses_shared_realistic_friction() -> None:
    assert factor_research.REALISTIC_ROUND_TRIP_FRICTION == 0.0015


def test_side_score_deducts_fee_and_slippage() -> None:
    sample = pd.DataFrame(
        {
            "forward_return": [0.0016],
            "long_mfe": [0.0020],
            "long_mae": [-0.0010],
            "short_mfe": [0.0010],
            "short_mae": [-0.0020],
        }
    )

    result = factor_research.side_score(sample, "long")

    assert result["mean_forward_return_pct"] == 0.16
    assert result["mean_after_fee_pct"] == 0.01


def test_top_evaluations_prefers_observed_negative_rows_to_uncovered_zeroes() -> None:
    ranked = factor_research.top_evaluations(
        [
            {"sample": 0, "mean_after_fee_pct": 0.0, "factor": "uncovered"},
            {"sample": 100, "mean_after_fee_pct": -0.02, "factor": "observed"},
        ]
    )

    assert [item["factor"] for item in ranked] == ["observed", "uncovered"]
