from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import research_program_postmortem as postmortem  # noqa: E402


def _record(experiment_id: str, outcome: str) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "strategy_family": "uptrend_pullback_long",
        "outcome": outcome,
        "lifecycle": "closed",
        "mechanism_cluster": "compression_price_action",
        "data_source_cluster": "futures_ohlcv",
    }


def test_data_blocker_is_not_an_edge_failure_or_streak_reset() -> None:
    records = [
        _record("E1", "gross_fail"),
        _record("E2", "data_blocked"),
        _record("E3", "cost_killed"),
        _record("E4", "validation_reversal"),
    ]

    postmortem.add_evidence_novelty(records)
    rows = postmortem.family_summaries(records, {})
    row = next(item for item in rows if item["strategy_family"] == "uptrend_pullback_long")

    assert row["edge_failures"] == 3
    assert row["non_edge_blockers"] == 1
    assert row["max_same_evidence_failure_streak"] == 3
    assert row["suspended_same_evidence"] is True


def test_prospective_e23_is_never_reclassified_as_failed_edge() -> None:
    outcome, detail = postmortem.outcome_for(
        {"decision": "sample pending", "outcomes_read": False},
        23,
    )

    assert outcome == "data_blocked"
    assert "unread" in detail


def test_e33_remains_a_frozen_research_asset() -> None:
    outcome, detail = postmortem.outcome_for({"strategy_synthesis_allowed": True}, 33)

    assert outcome == "risk_gate"
    assert "frozen" in detail
