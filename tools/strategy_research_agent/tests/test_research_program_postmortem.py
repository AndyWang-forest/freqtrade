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


def test_program_scope_and_bucket_contract_are_e1_through_e62() -> None:
    assert list(postmortem.EXPERIMENT_RANGE) == list(range(1, 63))
    records = [
        _record("E1", "risk_gate"),
        _record("E2", "data_blocked"),
        _record("E3", "sample_or_causality"),
        _record("E4", "execution_incompatible"),
        _record("E5", "gross_fail"),
    ]

    postmortem.add_program_buckets(records)

    assert [item["program_bucket"] for item in records] == [
        "retained_effective",
        "waiting_for_data",
        "sample_insufficient",
        "implementation_error",
        "disproven",
    ]


def test_e61_and_e62_remain_unread_evidence_acquisition() -> None:
    e61, _ = postmortem.outcome_for({"outcomes_read": False}, 61)
    e62, _ = postmortem.outcome_for({"outcomes_read": False}, 62)

    assert e61 == "data_blocked"
    assert e62 == "sample_or_causality"


def test_legacy_machine_gate_schema_is_classified_as_disproven() -> None:
    outcome, detail = postmortem.outcome_for(
        {
            "strategy_synthesis_allowed": False,
            "gates": [
                {"candidate": "one", "passed": False},
                {"candidate": "two", "passed": False},
            ],
        },
        2,
    )

    assert outcome == "robustness_fail"
    assert "machine-readable" in detail


def test_prospective_collector_schema_is_classified_as_waiting_for_data() -> None:
    outcome, detail = postmortem.outcome_for(
        {
            "decision": "collector_ready_for_unchanged_prospective_accumulation",
            "outcomes_read": False,
            "strategy_synthesis_allowed": False,
        },
        37,
    )

    assert outcome == "data_blocked"
    assert "collector_ready" in detail
