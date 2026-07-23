from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import mechanism_variant_policy as policy  # noqa: E402


def _event() -> dict:
    return {
        "event_id": "event-1",
        "event_kind": "family_factor_composite",
        "status": "family_composite_edge_candidate",
        "strategy_generation_allowed": True,
        "strategy_family_codes": ["C1"],
        "regime_label": "bear",
        "side": "short",
        "timeframe": "15m",
        "event_definition": {
            "structural_event": "pullback_resume_short",
            "domain": "derivatives",
            "factor": "liquidation_context",
            "tail": "high",
            "data_requirement": "force_order_oi_funding_basis",
        },
        "gross_edge": {"gate": "pass", "independent_events": 80},
        "realistic_cost": {"gate": "pass", "mean_after_fee_pct": 0.12},
        "independent_window_evidence": [
            {"window": "bear_1", "net_positive": True, "mean_after_fee_pct": 0.13},
            {"window": "bear_2", "net_positive": True, "mean_after_fee_pct": 0.11},
        ],
        "execution_compatibility": {"compatible": True},
    }


def test_strategy_generation_rechecks_full_event_contract() -> None:
    event = _event()
    assert policy.validated_event_blockers(event) == []

    event["independent_window_evidence"][1]["net_positive"] = False
    assert "fewer_than_two_positive_home_regime_windows" in policy.validated_event_blockers(event)


def test_duplicate_window_rows_do_not_satisfy_independence_gate() -> None:
    event = _event()
    event["independent_window_evidence"][1]["window"] = "bear_1"

    assert "fewer_than_two_positive_home_regime_windows" in policy.validated_event_blockers(event)


def test_mechanism_fingerprint_changes_only_when_evidence_generation_changes() -> None:
    event = _event()
    same_evidence = _event()
    changed_evidence = _event()
    changed_evidence["independent_window_evidence"][1]["sample"] = 41

    assert policy.mechanism_fingerprint(event) == policy.mechanism_fingerprint(same_evidence)
    assert policy.mechanism_fingerprint(event) != policy.mechanism_fingerprint(changed_evidence)


def test_three_failed_variants_quarantine_unchanged_mechanism() -> None:
    fingerprint = policy.mechanism_fingerprint(_event())
    ledger = policy.empty_ledger()
    ledger["mechanisms"][fingerprint] = {
        "variants": [
            {"variant_id": f"variant-{number}", "outcome": "failed"}
            for number in range(1, 4)
        ]
    }

    state = policy.mechanism_state(fingerprint, ledger)

    assert state["budget_used"] == 3
    assert state["budget_remaining"] == 0
    assert state["failed_variants"] == 3
    assert state["quarantined"] is True
