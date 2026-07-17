from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from generate_memory_guided_strategies import selected_hypotheses  # noqa: E402


def test_old_memory_hypothesis_cannot_generate_without_validated_event() -> None:
    plan = {"hypotheses": [{"hypothesis_id": "old", "strategy": "OldStrategy", "blocker": "weak_profit_factor"}]}
    generated, skipped = selected_hypotheses(plan, 8, {"factor_event_001"})
    assert generated == []
    assert skipped[0]["reason"] == "missing_validated_factor_event"


def test_linked_memory_hypothesis_can_reach_generator() -> None:
    plan = {
        "hypotheses": [
            {
                "hypothesis_id": "linked",
                "strategy": "LinkedStrategy",
                "blocker": "weak_profit_factor",
                "source_event_id": "factor_event_001",
            }
        ]
    }
    generated, skipped = selected_hypotheses(plan, 8, {"factor_event_001"})
    assert [item["hypothesis_id"] for item in generated] == ["linked"]
    assert skipped == []
