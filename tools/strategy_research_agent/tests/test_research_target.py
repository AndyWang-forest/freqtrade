from __future__ import annotations

import sys
from pathlib import Path

import pytest


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from research_target import (  # noqa: E402
    load_current_research_target,
    resolve_target_from_allocation,
    resolve_target_from_payload,
    target_mismatch_reason,
)
from current_market_state_family_router import family_decisions  # noqa: E402


def test_bear_router_targets_only_short_a1_c1_d1() -> None:
    target = resolve_target_from_payload(
        {"current_state": "bear_continuation", "state_reason": "bear evidence", "generated_utc": "t"}
    )
    assert target.regime_label == "bear"
    assert target.family_codes == ("A1", "C1", "D1")
    assert target.allowed_sides == ("short",)
    assert target.action == "research"


def test_c1_family_decision_requires_bear_continuation() -> None:
    features = {
        "combined": {
            "ret_5d": -0.01,
            "ret_30d": -0.10,
            "ret_65d": -0.15,
            "vol_pctile": 0.4,
            "bb_width_pctile": 0.5,
            "position_5d": 0.3,
        }
    }

    bear_decision = next(
        item for item in family_decisions(features, "bear_continuation") if item.family_code == "C1"
    )
    range_decision = next(
        item for item in family_decisions(features, "range_or_compression") if item.family_code == "C1"
    )

    assert bear_decision.status == "conditional_watch"
    assert range_decision.status == "off_or_wait"


def test_unknown_router_blocks_discovery_instead_of_all_history_fallback() -> None:
    target = resolve_target_from_payload({"current_state": "mixed_unknown"})
    assert target.action == "no_trade"
    assert target.regime_label is None
    assert target.allowed_sides == ()


def test_independent_allocator_target_is_not_current_router_family() -> None:
    target = resolve_target_from_allocation(
        {
            "generated_at_utc": "t",
            "deployment_permission": {"current_state": "range_or_compression"},
            "research_allocation": {
                "action": "research",
                "selected_family": "uptrend_pullback_long",
                "family_code": "C2",
                "regime_label": "bull",
                "allowed_sides": ["long"],
            },
        }
    )

    assert target.current_state == "range_or_compression"
    assert target.family_codes == ("C2",)
    assert target.regime_label == "bull"
    assert target.allowed_sides == ("long",)
    assert target.selection_source == "research_family_allocator"


def test_missing_allocator_fails_closed_instead_of_using_deployment_router(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="independent research allocator"):
        load_current_research_target(tmp_path / "missing_allocator.json")


def test_target_fingerprint_invalidates_wrong_allocator_evidence() -> None:
    target = resolve_target_from_allocation(
        {
            "generated_at_utc": "t",
            "target_fingerprint": "target-a",
            "deployment_permission": {"current_state": "range_or_compression"},
            "research_allocation": {
                "action": "research",
                "selected_family": "uptrend_pullback_long",
                "family_code": "C2",
                "regime_label": "bull",
                "allowed_sides": ["long"],
            },
        }
    )
    stale = target.as_dict()
    stale["target_fingerprint"] = "target-b"

    assert target_mismatch_reason(target.as_dict(), target) is None
    assert "fingerprint" in str(target_mismatch_reason(stale, target))
