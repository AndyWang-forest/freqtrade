from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from build_research_memory import active_manual_lessons, build_next_focus  # noqa: E402


def test_active_manual_lessons_excludes_superseded_and_quarantined() -> None:
    lessons = [
        {"id": "active", "status": "active_research_lesson"},
        {"id": "legacy_default"},
        {"id": "superseded", "status": "superseded"},
        {"id": "retired", "status": "retired"},
        {"id": "quarantined", "status": "quarantined"},
        {"id": "legacy_regime", "quarantine_status": "needs_regime_relabel"},
    ]

    assert [item["id"] for item in active_manual_lessons(lessons)] == [
        "active",
        "legacy_default",
    ]


def test_failure_funnel_replaces_unknown_agenda_with_current_blocker() -> None:
    agenda = {
        "top_priorities": [
            {"strategy": "OldStrategy", "blocker": None, "objective": "stale"},
        ]
    }
    funnel = {
        "current_target": {"family_codes": ["B", "E"]},
        "current_target_decision": {
            "decision": "stop_adjacent_variant_generation",
            "strategy_synthesis_allowed": False,
            "next_action": "Gather new causal evidence.",
        },
        "entries": [
            {"experiment": "E36", "primary_category": "data_blocked"},
        ],
    }

    focus = build_next_focus(agenda, [], funnel)

    assert [item["strategy"] for item in focus] == [
        "current_B_E_research_target",
        "E32_E36_prospective_L1_evidence",
    ]
    assert all(item["blocker"] for item in focus)
