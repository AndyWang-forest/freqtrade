from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import run_factor_candidate_event_study as event_study  # noqa: E402
import build_research_consolidation as consolidation  # noqa: E402


def _factor_row(data_requirement: str = "ohlcv") -> dict:
    return {
        "pair": "BTC/USDT:USDT",
        "timeframe": "15m",
        "horizon_bars": 8,
        "factor": "ret_12",
        "factor_column": "ret_12",
        "domain": "price_action",
        "data_requirement": data_requirement,
        "knowledge_cards": [],
        "tail": "high",
        "quantile_threshold": 0.02,
        "quantile_threshold_source": "earliest_home_episode",
        "quantile_calibration_sample": 100,
        "development_window": "bull_0",
        "validation_windows": ["bull_1"],
        "side": "long",
        "raw_sample": 100,
        "independent_sample": 30,
        "mean_forward_return_pct": 0.4,
        "mean_after_fee_pct": 0.25,
        "gross_win_rate": 0.6,
        "win_rate": 0.55,
        "mfe_mae_ratio": 1.4,
        "gross_gate": "pass",
        "cost_gate": "pass",
        "window_evidence": [],
        "verdict": "edge_candidate",
    }


def test_single_factor_edge_is_supporting_only() -> None:
    event = event_study.supporting_factor_event(
        _factor_row(),
        1,
        {"target_family_codes": ["A2"], "regime_label": "bull"},
    )

    assert event["status"] == "supporting_factor_edge"
    assert event["strategy_generation_allowed"] is False
    assert event["event_kind"] == "supporting_factor"


def test_gross_positive_factor_may_enter_composition_without_standalone_authority() -> None:
    row = _factor_row()
    row["verdict"] = "reject_realistic_cost"
    row["cost_gate"] = "fail"
    event = event_study.supporting_factor_event(
        row,
        1,
        {"target_family_codes": ["C1"], "regime_label": "bear"},
    )

    assert event["status"] == "reject_realistic_cost"
    assert event["strategy_generation_allowed"] is False
    assert event_study.factor_can_enter_composition(event) is True


def test_gross_failed_factor_cannot_enter_composition() -> None:
    row = _factor_row()
    row["gross_gate"] = "fail"
    event = event_study.supporting_factor_event(
        row,
        1,
        {"target_family_codes": ["C1"], "regime_label": "bear"},
    )

    assert event_study.factor_can_enter_composition(event) is False


def test_failed_bounce_families_do_not_reuse_controlled_pullback_structures() -> None:
    assert event_study.compatible_structural_events(["A1"], "short") == [
        "failed_bounce_short"
    ]
    assert event_study.compatible_structural_events(["A2"], "long") == [
        "failed_pullback_long"
    ]


def test_allocator_family_codes_route_to_predeclared_structures() -> None:
    assert event_study.compatible_structural_events(["C1"], "short") == [
        "pullback_resume_short"
    ]
    assert event_study.compatible_structural_events(["C2"], "long") == [
        "pullback_resume_atr_q80_long",
        "pullback_resume_long",
        "pullback_resume_volume_q80_long",
    ]
    assert event_study.compatible_structural_events(["B1"], "short") == [
        "false_break_short"
    ]
    assert event_study.compatible_structural_events(["B2"], "long") == [
        "false_break_long"
    ]


def test_offline_open_interest_requires_runtime_adapter() -> None:
    compatibility = event_study.execution_compatibility("open_interest")
    assert compatibility["compatible"] is False
    assert "runtime data adapter" in compatibility["reason"]


def test_factor_mask_uses_frozen_threshold_without_recalibration() -> None:
    frame = pd.DataFrame({"ret_12": [0.01, 0.02, 0.03]})
    mask = event_study.factor_mask(
        frame,
        {
            "factor": "ret_12",
            "factor_column": "ret_12",
            "tail": "high",
            "quantile_threshold": 0.02,
        },
    )
    assert mask.tolist() == [False, True, True]


def test_generated_factor_policy_uses_versioned_composite_contract() -> None:
    policy = consolidation.load_versioned_factor_research_policy()

    assert policy == consolidation.FACTOR_RESEARCH_POLICY
    assert policy["single_factor_is_supporting_only"] is True
    assert policy["gross_factor_may_enter_structural_composition"] is True
    assert policy["family_structural_composite_required"] is True
    assert policy["runtime_data_compatibility_required"] is True
