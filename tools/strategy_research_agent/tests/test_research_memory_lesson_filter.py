from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from build_research_memory import (  # noqa: E402
    active_manual_lessons,
    build_next_focus,
    quarantine_implementation_lessons,
)


def test_active_manual_lessons_excludes_superseded_and_quarantined() -> None:
    lessons = [
        {"id": "active", "status": "active_research_lesson"},
        {"id": "legacy_default"},
        {"id": "superseded", "status": "superseded"},
        {"id": "retired", "status": "retired"},
        {"id": "quarantined", "status": "quarantined"},
        {"id": "legacy_regime", "quarantine_status": "needs_regime_relabel"},
        {"id": "implementation", "quarantine_status": "implementation_remediation"},
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

    focus = build_next_focus(
        agenda,
        [],
        funnel,
        {
            "e62_background_acquisition": {
                "count_gate_ready": False,
            }
        },
    )

    assert [item["strategy"] for item in focus] == [
        "current_B_E_research_target",
        "E62_blind_force_order_acquisition",
    ]
    assert all(item["blocker"] for item in focus)


def test_implementation_error_lessons_are_quarantined_without_deletion() -> None:
    lessons = [
        {"id": "e32_old_wait", "memory_rule": "Keep E32 outcomes unread."},
        {"id": "e23_wait", "memory_rule": "Keep E23 outcomes unread."},
    ]

    filtered = quarantine_implementation_lessons(
        lessons, {"implementation_remediation": ["E32"]}
    )

    assert len(filtered) == 2
    assert filtered[0]["quarantine_status"] == "implementation_remediation"
    assert filtered[0]["implementation_experiments"] == ["E32"]
    assert "quarantine_status" not in filtered[1]
    assert [item["id"] for item in active_manual_lessons(filtered)] == ["e23_wait"]


def test_cross_reference_to_implementation_error_does_not_quarantine_lesson() -> None:
    lessons = [
        {
            "id": "e20_branch_closed_after_sample_ceiling",
            "next_test": "A fresh mechanism must differ from E7 and E32.",
            "evidence": ["event_studies/e20_sample_ceiling.json"],
        },
        {
            "id": "e42_bookdepth_not_e32_equivalent",
            "evidence": ["event_studies/e42_bookdepth.json"],
        },
    ]

    filtered = quarantine_implementation_lessons(
        lessons, {"implementation_remediation": ["E7", "E32"]}
    )

    assert all("quarantine_status" not in item for item in filtered)


def test_no_allocation_does_not_fall_back_to_factor_research() -> None:
    focus = build_next_focus(
        {},
        [],
        {
            "current_target": {"family_codes": []},
            "current_target_decision": {
                "decision": "no_research_allocation",
                "strategy_synthesis_allowed": False,
                "next_action": "Continue the frozen evidence axis.",
            },
        },
        {},
    )

    assert focus[0]["next_command"].endswith("--research-reset")
    assert "--factor-research" not in focus[0]["next_command"]
