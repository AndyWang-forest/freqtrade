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
