from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from mature_researcher import build_failure_funnel_decision  # noqa: E402


def test_failure_funnel_produces_actionable_current_target_decision() -> None:
    decision = build_failure_funnel_decision(
        {
            "current_target": {
                "current_state": "range_or_compression",
                "family_codes": ["B", "E"],
            },
            "category_counts": {"gross_fail": 2},
            "current_target_decision": {
                "strategy_synthesis_allowed": False,
                "validated_factor_events": 0,
                "blocker_fingerprint": "abc",
                "next_action": "Collect new evidence.",
            },
        }
    )

    assert decision.priority == 110
    assert decision.strategy == "current_B_E_research_target"
    assert "No adjacent strategy generation" in decision.promotion_block
    assert decision.next_command == (
        "manual:acquire_new_causal_or_preregistered_prospective_data"
    )
    assert "--factor-research" not in decision.next_command


def test_failure_funnel_does_not_claim_zero_events_when_event_is_validated() -> None:
    decision = build_failure_funnel_decision(
        {
            "current_target": {
                "current_state": "range_or_compression",
                "family_codes": ["C1"],
            },
            "category_counts": {},
            "current_target_decision": {
                "strategy_synthesis_allowed": True,
                "validated_factor_events": 1,
                "blocker_fingerprint": "abc",
                "next_action": "Preserve the event and change the blocker.",
            },
        }
    )

    assert "validated event exists" in decision.promotion_block
    assert "event count is zero" not in decision.promotion_block
    assert "current-router permission" in decision.promotion_block
