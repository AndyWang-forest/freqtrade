from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import factor_to_strategy_plan as plan  # noqa: E402
from factor_research_protocol import FACTOR_EVENT_METHOD_VERSION  # noqa: E402
from research_target import resolve_target_from_allocation  # noqa: E402


def _target_payload(fingerprint: str) -> dict[str, object]:
    return {
        "generated_at_utc": "20260717T000000Z",
        "target_fingerprint": fingerprint,
        "deployment_permission": {"current_state": "bull_trend"},
        "research_allocation": {
            "action": "research",
            "selected_family": "uptrend_pullback_long",
            "family_code": "C2",
            "regime_label": "bull",
            "allowed_sides": ["long"],
        },
    }


def _validated_event() -> dict[str, object]:
    return {
        "event_id": "event-1",
        "event_kind": "family_factor_composite",
        "status": "family_composite_edge_candidate",
        "strategy_generation_allowed": True,
        "strategy_family_codes": ["C2"],
        "regime_label": "bull",
        "pair": "ETH/USDT:USDT",
        "timeframe": "15m",
        "side": "long",
        "event_definition": {
            "structural_event": "pullback_resume_long",
            "domain": "derivatives",
            "factor": "liquidation_context",
            "tail": "high",
            "data_requirement": "force_order_oi_funding_basis",
        },
        "gross_edge": {
            "gate": "pass",
            "independent_events": 80,
            "mean_return_pct": 0.18,
            "win_rate": 0.62,
            "mfe_mae_ratio": 1.7,
        },
        "realistic_cost": {
            "gate": "pass",
            "mean_after_fee_pct": 0.11,
            "win_rate": 0.58,
        },
        "independent_window_evidence": [
            {"window": "bull_1", "net_positive": True, "mean_after_fee_pct": 0.12},
            {"window": "bull_2", "net_positive": True, "mean_after_fee_pct": 0.10},
        ],
        "execution_compatibility": {"compatible": True},
    }


def test_allocator_change_blocks_old_event_report(monkeypatch, tmp_path) -> None:
    current_target = resolve_target_from_allocation(_target_payload("current-target"))
    stale_target = resolve_target_from_allocation(_target_payload("old-target"))
    event_report = {
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION,
        "generated_at_utc": "20260716T000000Z",
        "regime_label": "bull",
        "research_target": stale_target.as_dict(),
        "validated_events": [],
    }
    event_path = tmp_path / "old_event.json"

    monkeypatch.setattr(plan, "load_current_research_target", lambda: current_target)
    monkeypatch.setattr(
        plan,
        "load_current_event_report",
        lambda: (event_path, event_report),
    )

    payload = plan.build_payload()

    assert payload["hypotheses"] == []
    assert payload["summary"]["verdict"] == "blocked_stale_or_wrong_target_event"
    assert "fingerprint" in payload["blocked_reason"]


def test_index_without_current_target_fails_closed(monkeypatch, tmp_path) -> None:
    index_path = tmp_path / "event_index.json"
    index_path.write_text(
        '{"index_version": 2, "current_target": null}\n', encoding="utf-8"
    )
    monkeypatch.setattr(plan, "EVENT_INDEX", index_path)

    path, payload = plan.load_current_event_report()

    assert path is None
    assert payload == {}


def test_no_research_allocation_is_reported_as_allocator_block(monkeypatch) -> None:
    target_payload = _target_payload("no-allocation")
    target_payload["research_allocation"] = {
        "action": "no_research_allocation",
        "selected_family": None,
        "family_code": None,
        "reason": "Continue frozen evidence acquisition only.",
    }
    target = resolve_target_from_allocation(target_payload)
    monkeypatch.setattr(plan, "load_current_research_target", lambda: target)
    monkeypatch.setattr(plan, "load_current_event_report", lambda: (None, {}))

    payload = plan.build_payload()

    assert payload["hypotheses"] == []
    assert payload["summary"]["verdict"] == "blocked_by_allocator_no_research_target"
    assert payload["blocked_reason"] == "Continue frozen evidence acquisition only."


def test_declared_validated_event_is_rejected_without_two_positive_windows(
    monkeypatch, tmp_path
) -> None:
    target = resolve_target_from_allocation(_target_payload("current-target"))
    event = _validated_event()
    event["independent_window_evidence"] = [
        {"window": "bull_1", "net_positive": True}
    ]
    report = {
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION,
        "generated_at_utc": "20260717T000000Z",
        "regime_label": "bull",
        "research_target": target.as_dict(),
        "validated_events": [event],
    }

    monkeypatch.setattr(plan, "load_current_research_target", lambda: target)
    monkeypatch.setattr(
        plan,
        "load_current_event_report",
        lambda: (tmp_path / "event.json", report),
    )
    monkeypatch.setattr(plan, "load_ledger", lambda: {"mechanisms": {}})

    payload = plan.build_payload()

    assert payload["hypotheses"] == []
    assert payload["summary"]["verdict"] == "blocked_stale_or_wrong_target_event"
    assert "fewer_than_two_positive_home_regime_windows" in payload["blocked_reason"]


def test_full_event_contract_allows_one_explainable_hypothesis(
    monkeypatch, tmp_path
) -> None:
    target = resolve_target_from_allocation(_target_payload("current-target"))
    event = _validated_event()
    report = {
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION,
        "generated_at_utc": "20260717T000000Z",
        "regime_label": "bull",
        "research_target": target.as_dict(),
        "validated_events": [event],
    }

    monkeypatch.setattr(plan, "load_current_research_target", lambda: target)
    monkeypatch.setattr(
        plan,
        "load_current_event_report",
        lambda: (tmp_path / "event.json", report),
    )
    monkeypatch.setattr(plan, "load_ledger", lambda: {"mechanisms": {}})

    payload = plan.build_payload()

    assert payload["summary"]["verdict"] == "ready_for_explainable_strategy_hypothesis"
    assert len(payload["hypotheses"]) == 1
    assert payload["hypotheses"][0]["source_event_id"] == "event-1"
