from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


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


def test_forward_labels_enter_at_next_candle_open() -> None:
    frame = pd.DataFrame(
        {
            "open": [100.0, 110.0, 118.0],
            "high": [101.0, 116.0, 122.0],
            "low": [99.0, 108.0, 117.0],
            "close": [100.0, 115.0, 120.0],
        }
    )

    labelled = factor_research.add_forward_labels(frame, 2)

    assert labelled.loc[0, "entry_price"] == 110.0
    assert labelled.loc[0, "forward_return"] == pytest.approx(120.0 / 110.0 - 1.0)


def test_forward_labels_clamp_favorable_only_mae_to_zero() -> None:
    long_favorable = pd.DataFrame(
        {
            "open": [100.0, 100.0, 105.0],
            "high": [101.0, 106.0, 110.0],
            "low": [99.0, 101.0, 104.0],
            "close": [100.0, 105.0, 109.0],
        }
    )
    short_favorable = pd.DataFrame(
        {
            "open": [100.0, 100.0, 95.0],
            "high": [101.0, 99.0, 96.0],
            "low": [99.0, 94.0, 90.0],
            "close": [100.0, 95.0, 91.0],
        }
    )

    long_labelled = factor_research.add_forward_labels(long_favorable, 2)
    short_labelled = factor_research.add_forward_labels(short_favorable, 2)

    assert long_labelled.loc[0, "long_mae"] == 0.0
    assert short_labelled.loc[0, "short_mae"] == 0.0


def test_top_evaluations_prefers_observed_negative_rows_to_uncovered_zeroes() -> None:
    ranked = factor_research.top_evaluations(
        [
            {"sample": 0, "mean_after_fee_pct": 0.0, "factor": "uncovered"},
            {"sample": 100, "mean_after_fee_pct": -0.02, "factor": "observed"},
        ]
    )

    assert [item["factor"] for item in ranked] == ["observed", "uncovered"]


def test_decluster_sample_removes_overlapping_forward_horizons() -> None:
    sample = pd.DataFrame({"value": range(7)}, index=[1, 2, 5, 10, 11, 19, 20])
    result = factor_research.decluster_sample(sample, 8)
    assert list(result.index) == [1, 10, 19]


def test_quantile_threshold_is_frozen_on_development_window() -> None:
    frame = pd.DataFrame(
        {
            "regime_window": ["development"] * 5 + ["validation"] * 5,
            "factor": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0, 200.0, 300.0, 400.0, 500.0],
            "forward_return": [0.01] * 10,
            "long_mfe": [0.02] * 10,
            "long_mae": [0.01] * 10,
            "short_mfe": [0.02] * 10,
            "short_mae": [0.01] * 10,
        }
    )

    sample, threshold = factor_research.quantile_sample(
        frame,
        "factor",
        "high",
        calibration_window="development",
    )

    assert threshold == 4.2
    assert sample["factor"].tolist() == [5.0, 100.0, 200.0, 300.0, 400.0, 500.0]


def test_validation_values_cannot_change_frozen_threshold() -> None:
    base = pd.DataFrame(
        {
            "regime_window": ["development"] * 5 + ["validation"] * 5,
            "factor": [1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 30.0, 40.0, 50.0],
            "forward_return": [0.01] * 10,
            "long_mfe": [0.02] * 10,
            "long_mae": [0.01] * 10,
            "short_mfe": [0.02] * 10,
            "short_mae": [0.01] * 10,
        }
    )
    changed = base.copy()
    changed.loc[changed["regime_window"] == "validation", "factor"] *= 1_000

    _, base_threshold = factor_research.quantile_sample(
        base, "factor", "high", calibration_window="development"
    )
    _, changed_threshold = factor_research.quantile_sample(
        changed, "factor", "high", calibration_window="development"
    )

    assert base_threshold == changed_threshold == 4.2


def test_side_score_requires_positive_validation_replication() -> None:
    sample = pd.DataFrame(
        {
            "regime_window": ["development"] * 13 + ["validation"] * 11,
            "forward_return": [0.0040] * 13 + [0.0014] * 11,
            "long_mfe": [0.0050] * 24,
            "long_mae": [0.0010] * 24,
            "short_mfe": [0.0010] * 24,
            "short_mae": [0.0050] * 24,
        }
    )

    result = factor_research.side_score(
        sample,
        "long",
        regime_label="bull",
        window_roles={"development": "development", "validation": "validation"},
    )

    assert result["gross_gate"] == "pass"
    assert result["cost_gate"] == "pass"
    assert result["positive_independent_windows"] == 1
    assert result["positive_validation_windows"] == 0
    assert result["verdict"] == "needs_independent_window"
