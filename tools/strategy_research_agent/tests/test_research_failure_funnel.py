from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "strategy_research"
    / "research_failure_funnel.py"
)
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("research_failure_funnel", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_categories_are_closed_and_explicit() -> None:
    assert MODULE.CATEGORIES == (
        "gross_fail",
        "cost_killed",
        "validation_reversal",
        "data_blocked",
        "execution_incompatible",
        "gate_semantic_block",
    )


def test_zero_validated_events_blocks_adjacent_variants(monkeypatch) -> None:
    payloads = {
        MODULE.CURRENT_ROUTER: {},
        MODULE.CURRENT_FACTOR: {
            "generated_at_utc": "20260715T000000Z",
            "research_target": {
                "current_state": "range_or_compression",
                "action": "research",
                "family_codes": ["B", "E"],
                "allowed_sides": ["long", "short"],
            },
            "summary": {"evaluations": 10, "gross_candidates": 0, "verdict": "no_gross_edge_candidates"},
        },
        MODULE.CURRENT_FACTOR_EVENT: {
            "summary": {"validated_events": 0, "verdict": "no_validated_factor_event"}
        },
        MODULE.CURRENT_FAMILY_GATE: {},
    }
    monkeypatch.setattr(MODULE, "load_json", lambda path: payloads.get(path, {}))
    monkeypatch.setattr(MODULE, "classify_monitored_studies", lambda: [])
    monkeypatch.setattr(MODULE, "classify_active_candidates", lambda: [])

    payload = MODULE.build_payload()

    decision = payload["current_target_decision"]
    assert decision["decision"] == "stop_adjacent_variant_generation"
    assert decision["strategy_synthesis_allowed"] is False
    assert decision["adjacent_variant_generation_allowed"] is False
    assert payload["category_counts"] == {"gross_fail": 2}


def test_pending_prospective_entries_never_imply_outcomes_read() -> None:
    row = MODULE.entry(
        "E36",
        "data_blocked",
        "sample pending",
        Path("pending.json"),
        outcomes_read=False,
    )
    assert row["outcomes_read"] is False


def test_stale_factor_target_is_data_blocked_not_new_family_edge_failure(monkeypatch) -> None:
    payloads = {
        MODULE.CURRENT_ROUTER: {"current_state": "range_or_compression"},
        MODULE.CURRENT_ALLOCATION: {
            "deployment_permission": {"current_state": "range_or_compression"},
            "research_allocation": {
                "action": "research",
                "selected_family": "uptrend_pullback_long",
                "family_code": "C2",
                "regime_label": "bull",
                "allowed_sides": ["long"],
            },
        },
        MODULE.CURRENT_FACTOR: {
            "research_target": {
                "action": "research",
                "regime_label": "range",
                "family_codes": ["B", "E"],
                "allowed_sides": ["long", "short"],
            },
            "summary": {"gross_candidates": 0},
        },
        MODULE.CURRENT_FACTOR_EVENT: {"summary": {"validated_events": 3}},
        MODULE.CURRENT_FAMILY_GATE: {},
    }
    monkeypatch.setattr(MODULE, "load_json", lambda path: payloads.get(path, {}))
    monkeypatch.setattr(MODULE, "classify_monitored_studies", lambda: [])
    monkeypatch.setattr(MODULE, "classify_active_candidates", lambda: [])

    payload = MODULE.build_payload()

    decision = payload["current_target_decision"]
    assert payload["current_target"]["family_codes"] == ["C2"]
    assert decision["factor_target_stale"] is True
    assert decision["validated_factor_events"] == 0
    assert payload["entries"][0]["primary_category"] == "data_blocked"


def test_no_research_allocation_does_not_reuse_stale_factor_target(monkeypatch) -> None:
    payloads = {
        MODULE.CURRENT_ROUTER: {
            "deployment_target": {
                "current_state": "range_or_compression",
                "action": "research",
                "family_codes": ["B", "E"],
            }
        },
        MODULE.CURRENT_ALLOCATION: {
            "deployment_permission": {"current_state": "range_or_compression"},
            "research_allocation": {
                "action": "no_research_allocation",
                "selected_family": None,
                "reason": "No unsuspended family has enough independent data-derived home windows.",
                "strategy_synthesis_allowed": False,
            },
        },
        MODULE.CURRENT_FACTOR: {
            "research_target": {
                "action": "research",
                "regime_label": "bear",
                "family_codes": ["A1"],
                "allowed_sides": ["short"],
            },
            "summary": {"gross_candidates": 0},
        },
        MODULE.CURRENT_FACTOR_EVENT: {"summary": {"validated_events": 2}},
        MODULE.CURRENT_FAMILY_GATE: {},
    }
    monkeypatch.setattr(MODULE, "load_json", lambda path: payloads.get(path, {}))
    monkeypatch.setattr(MODULE, "classify_monitored_studies", lambda: [])
    monkeypatch.setattr(MODULE, "classify_active_candidates", lambda: [])

    payload = MODULE.build_payload()

    target = payload["current_target"]
    decision = payload["current_target_decision"]
    assert target["action"] == "no_research_allocation"
    assert target["family_codes"] == []
    assert target["selection_source"] == "research_family_allocator"
    assert decision["decision"] == "no_research_allocation"
    assert decision["strategy_synthesis_allowed"] is False
    assert decision["factor_target_stale"] is True
    assert decision["validated_factor_events"] == 0
    assert payload["entries"][0]["experiment"] == "CURRENT_RESEARCH_ALLOCATION_ABSENT"
