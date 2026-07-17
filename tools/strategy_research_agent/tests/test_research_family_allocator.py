from __future__ import annotations

import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import research_family_allocator as allocator  # noqa: E402
from factor_research_protocol import (  # noqa: E402
    FACTOR_EVENT_METHOD_VERSION,
    FACTOR_RESEARCH_METHOD_VERSION,
    REGIME_THRESHOLD_SOURCE,
)


def _current_factor_protocol() -> dict[str, object]:
    return {
        "factor_research_method_version": FACTOR_RESEARCH_METHOD_VERSION,
        "threshold_protocol": {
            "threshold_source": REGIME_THRESHOLD_SOURCE,
            "development_window": "bull_0",
            "validation_windows": ["bull_1"],
        },
    }


def _manifest() -> dict[str, object]:
    windows = []
    for label, count in {"bull": 2, "bear": 1, "range": 2, "high_vol": 2}.items():
        windows.extend(
            {"label": label, "name": f"{label}_{index}", "status": "active"}
            for index in range(count)
        )
    return {"windows": windows}


def _postmortem(*, suspend_c2: bool = False) -> dict[str, object]:
    return {
        "family_matrix": [
            {
                "strategy_family": "uptrend_pullback_long",
                "experiments": 0,
                "retained_assets": [],
                "suspended_same_evidence": suspend_c2,
                "portfolio_coverage_saturated": False,
            },
            {
                "strategy_family": "volatility_compression_directional_expansion",
                "experiments": 20,
                "retained_assets": ["E1", "E33"],
                "suspended_same_evidence": False,
                "portfolio_coverage_saturated": True,
            },
        ]
    }


def test_current_range_state_does_not_force_range_research(monkeypatch) -> None:
    payloads = {
        allocator.POSTMORTEM_JSON: _postmortem(),
        allocator.ROUTER_JSON: {
            "current_state": "range_or_compression",
            "state_reason": "current range",
            "family_decisions": [],
        },
        allocator.MANIFEST_JSON: _manifest(),
        allocator.REGISTRY_JSON: {
            "strategies": [
                {"name": "A1Candidate", "family": "downtrend_failed_bounce_short"},
                {"name": "E1Candidate", "family": "volatility_compression_directional_expansion"},
            ]
        },
    }
    monkeypatch.setattr(allocator, "load_json", lambda path: payloads.get(path, {}))
    monkeypatch.setattr(allocator, "global_family_coverage", lambda memory, manifest: {})

    payload = allocator.build_payload()

    assert payload["deployment_permission"]["current_state"] == "range_or_compression"
    assert payload["deployment_permission"]["controls_research_allocation"] is False
    assert payload["research_allocation"]["family_code"] == "C2"
    assert payload["research_allocation"]["regime_label"] == "bull"
    assert payload["research_allocation"]["strategy_synthesis_allowed"] is False


def test_same_evidence_saturation_removes_family_from_selection(monkeypatch) -> None:
    payloads = {
        allocator.POSTMORTEM_JSON: _postmortem(suspend_c2=True),
        allocator.ROUTER_JSON: {},
        allocator.MANIFEST_JSON: _manifest(),
        allocator.REGISTRY_JSON: {"strategies": []},
    }
    monkeypatch.setattr(allocator, "load_json", lambda path: payloads.get(path, {}))
    monkeypatch.setattr(allocator, "global_family_coverage", lambda memory, manifest: {})

    payload = allocator.build_payload()
    c2 = next(item for item in payload["candidate_ranking"] if item["family_code"] == "C2")

    assert c2["eligible"] is False
    assert "same_evidence_failure_limit_reached" in c2["blockers"]
    assert payload["research_allocation"].get("family_code") != "C2"


def test_global_history_prevents_reselecting_heavily_researched_c2(monkeypatch) -> None:
    payloads = {
        allocator.POSTMORTEM_JSON: _postmortem(),
        allocator.ROUTER_JSON: {},
        allocator.MANIFEST_JSON: _manifest(),
        allocator.REGISTRY_JSON: {"strategies": []},
        allocator.RESEARCH_MEMORY_JSON: {},
    }
    monkeypatch.setattr(allocator, "load_json", lambda path: payloads.get(path, {}))
    monkeypatch.setattr(
        allocator,
        "global_family_coverage",
        lambda memory, manifest: {
            "uptrend_pullback_long": {
                "durable_lessons": [f"c2_{index}" for index in range(6)],
                "negative_durable_lessons": [f"c2_{index}" for index in range(4)],
                "event_artifacts": [f"c2_event_{index}" for index in range(4)],
            },
            "uptrend_failed_pullback_long": {
                "durable_lessons": [],
                "negative_durable_lessons": [],
                "event_artifacts": ["a2_event"],
            },
        },
    )

    payload = allocator.build_payload()
    c2 = next(item for item in payload["candidate_ranking"] if item["family_code"] == "C2")

    assert c2["prior_experiments"] == 6
    assert c2["durable_lessons"] == 6
    assert c2["negative_durable_lessons"] == 4
    assert c2["suspended_same_evidence"] is True
    assert "global_negative_durable_lessons" in c2["suspension_sources"]
    assert payload["research_allocation"]["family_code"] != "C2"


def test_current_completed_gross_fail_pauses_target_family(monkeypatch) -> None:
    payloads = {
        allocator.POSTMORTEM_JSON: _postmortem(),
        allocator.ROUTER_JSON: {},
        allocator.MANIFEST_JSON: _manifest(),
        allocator.REGISTRY_JSON: {"strategies": []},
        allocator.RESEARCH_MEMORY_JSON: {},
        allocator.FAILURE_FUNNEL_JSON: {
            "current_target": {
                "family_codes": ["A2"],
                "strategy_families": ["uptrend_failed_pullback_long"],
            },
            "current_target_decision": {
                "decision": "stop_adjacent_variant_generation",
                "factor_target_stale": False,
                "validated_factor_events": 0,
            },
            "entries": [
                {
                    "experiment": "CURRENT_FACTOR_DISCOVERY",
                    "primary_category": "gross_fail",
                }
            ],
        },
        allocator.FACTOR_REPORT_JSON: _current_factor_protocol(),
    }
    monkeypatch.setattr(allocator, "load_json", lambda path: payloads.get(path, {}))
    monkeypatch.setattr(allocator, "global_family_coverage", lambda memory, manifest: {})

    payload = allocator.build_payload()
    a2 = next(item for item in payload["candidate_ranking"] if item["family_code"] == "A2")

    assert a2["eligible"] is False
    assert "current_completed_gross_fail" in a2["suspension_sources"]
    assert payload["research_allocation"].get("family_code") != "A2"


def test_stale_factor_target_does_not_suspend_new_allocation(monkeypatch) -> None:
    funnel = {
        "current_target": {"family_codes": ["C1"]},
        "current_target_decision": {
            "decision": "stop_adjacent_variant_generation",
            "factor_target_stale": True,
            "validated_factor_events": 0,
        },
        "entries": [
            {
                "experiment": "CURRENT_FACTOR_TARGET_MISMATCH",
                "primary_category": "data_blocked",
            }
        ],
    }

    assert allocator.current_gross_fail_families(funnel) == set()


def test_family_with_one_home_window_cannot_enter_two_window_gate(monkeypatch) -> None:
    row = allocator.score_family(
        "downtrend_pullback_short",
        allocator.STRATEGY_TAXONOMY["downtrend_pullback_short"],
        {},
        registry_count=0,
        windows=[{"name": "bear_only"}],
        has_long_registry=False,
        coverage_row={},
    )

    assert row["eligible"] is False
    assert "insufficient_independent_home_windows" in row["blockers"]


def test_explicit_b_alias_maps_to_both_range_families() -> None:
    assert allocator.normalized_families("B") == {
        "range_upper_reversion_short",
        "range_lower_reversion_long",
    }
    assert allocator.normalized_families("range_mean_reversion") == {
        "range_upper_reversion_short",
        "range_lower_reversion_long",
    }


def test_target_fingerprint_is_stable_and_tracks_window_evidence() -> None:
    allocation = {
        "action": "research",
        "selected_family": "uptrend_pullback_long",
        "family_code": "C2",
        "regime_label": "bull",
        "allowed_sides": ["long"],
        "active_home_windows": ["bull_0", "bull_1"],
    }
    manifest = {
        "windows": [
            {
                "name": "bull_0",
                "label": "bull",
                "start": "2024-01-01",
                "end": "2024-03-01",
                "status": "active",
                "evidence": {"label_share": 0.7},
            },
            {
                "name": "bull_1",
                "label": "bull",
                "start": "2025-01-01",
                "end": "2025-03-01",
                "status": "active",
                "evidence": {"label_share": 0.8},
            },
        ]
    }

    first = allocator.allocation_target_fingerprint(allocation, manifest)
    second = allocator.allocation_target_fingerprint(allocation, manifest)
    changed_manifest = __import__("copy").deepcopy(manifest)
    changed_manifest["windows"][1]["evidence"]["label_share"] = 0.9

    assert first == second
    assert first != allocator.allocation_target_fingerprint(allocation, changed_manifest)


def test_completed_factor_failure_is_scoped_to_matching_current_windows(tmp_path) -> None:
    factor_report = tmp_path / "factor_report.json"
    factor_report.write_text(
        __import__("json").dumps(
            {
                "regime_label": "bull",
                "regime_windows": ["bull_0", "bull_1"],
                "research_target": {"family_codes": ["A2"]},
                **_current_factor_protocol(),
            }
        ),
        encoding="utf-8",
    )
    event_payload = {
        "factor_report": str(factor_report),
        "regime_label": "bull",
        "research_target": {"family_codes": ["A2"]},
        "summary": {
            "validated_events": 0,
            "verdict": "no_validated_factor_event",
        },
    }
    (tmp_path / "latest_factor_candidate_event_study__a2.json").write_text(
        __import__("json").dumps(event_payload),
        encoding="utf-8",
    )

    matching = allocator.completed_factor_failure_records(_manifest(), tmp_path)
    changed_manifest = _manifest()
    changed_manifest["windows"].append(
        {"label": "bull", "name": "bull_2", "status": "active"}
    )
    changed = allocator.completed_factor_failure_records(changed_manifest, tmp_path)

    assert len(matching["uptrend_failed_pullback_long"]) == 1
    assert changed["uptrend_failed_pullback_long"] == []


def test_completed_factor_failure_is_scoped_to_requested_pair_universe(tmp_path) -> None:
    factor_report = tmp_path / "factor_report.json"
    factor_report.write_text(
        __import__("json").dumps(
            {
                "pair_scope": "core",
                "regime_label": "bull",
                "regime_windows": ["bull_0", "bull_1"],
                "research_target": {"family_codes": ["A2"]},
                **_current_factor_protocol(),
            }
        ),
        encoding="utf-8",
    )
    event_payload = {
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION,
        "factor_report": str(factor_report),
        "pair_scope": "core",
        "regime_label": "bull",
        "research_target": {"family_codes": ["A2"]},
        "summary": {
            "gross_factor_candidates": 2,
            "validated_events": 0,
            "verdict": "no_validated_factor_event",
        },
    }
    (tmp_path / "latest_factor_candidate_event_study__a2.json").write_text(
        __import__("json").dumps(event_payload),
        encoding="utf-8",
    )

    core = allocator.completed_factor_failure_records(
        _manifest(), tmp_path, pair_scope="core"
    )
    expanded = allocator.completed_factor_failure_records(
        _manifest(), tmp_path, pair_scope="research_all"
    )

    assert len(core["uptrend_failed_pullback_long"]) == 1
    assert expanded["uptrend_failed_pullback_long"] == []


def test_legacy_factor_protocol_reopens_same_window_family(tmp_path) -> None:
    factor_report = tmp_path / "factor_report.json"
    factor_report.write_text(
        __import__("json").dumps(
            {
                "regime_label": "bull",
                "regime_windows": ["bull_0", "bull_1"],
                "research_target": {"family_codes": ["A2"]},
            }
        ),
        encoding="utf-8",
    )
    event_payload = {
        "factor_report": str(factor_report),
        "regime_label": "bull",
        "research_target": {"family_codes": ["A2"]},
        "summary": {
            "validated_events": 0,
            "verdict": "no_validated_factor_event",
        },
    }
    (tmp_path / "latest_factor_candidate_event_study__a2.json").write_text(
        __import__("json").dumps(event_payload),
        encoding="utf-8",
    )

    records = allocator.completed_factor_failure_records(_manifest(), tmp_path)

    assert records["uptrend_failed_pullback_long"] == []


def test_old_composition_method_reopens_gross_positive_family(tmp_path) -> None:
    factor_report = tmp_path / "factor_report.json"
    factor_report.write_text(
        __import__("json").dumps(
            {
                "regime_label": "bear",
                "regime_windows": ["bear_0"],
                "research_target": {"family_codes": ["C1"]},
                **_current_factor_protocol(),
            }
        ),
        encoding="utf-8",
    )
    event_payload = {
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION - 1,
        "factor_report": str(factor_report),
        "regime_label": "bear",
        "research_target": {"family_codes": ["C1"]},
        "summary": {
            "gross_factor_candidates": 1,
            "validated_events": 0,
            "verdict": "no_validated_factor_event",
        },
    }
    (tmp_path / "latest_factor_candidate_event_study__c1.json").write_text(
        __import__("json").dumps(event_payload),
        encoding="utf-8",
    )

    manifest = _manifest()
    records = allocator.completed_factor_failure_records(manifest, tmp_path)

    assert records["downtrend_pullback_short"] == []


def test_legacy_current_gross_fail_does_not_block_new_protocol_run() -> None:
    funnel = {
        "current_target": {"family_codes": ["D1"]},
        "current_target_decision": {
            "decision": "stop_adjacent_variant_generation",
            "factor_target_stale": False,
            "validated_factor_events": 0,
        },
        "entries": [
            {
                "experiment": "CURRENT_FACTOR_EVENT_GATE",
                "primary_category": "gross_fail",
            }
        ],
    }

    assert allocator.current_gross_fail_families(funnel, {}) == set()


def test_old_composition_method_does_not_pause_matching_current_target() -> None:
    funnel = {
        "current_target": {
            "family_codes": ["C1"],
            "strategy_families": ["downtrend_pullback_short"],
        },
        "current_target_decision": {
            "decision": "stop_adjacent_variant_generation",
            "factor_target_stale": False,
            "validated_factor_events": 0,
        },
        "entries": [
            {
                "experiment": "CURRENT_FACTOR_EVENT_GATE",
                "primary_category": "gross_fail",
            }
        ],
    }
    event_report = {
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION - 1,
        "research_target": {"family_codes": ["C1"]},
        "summary": {"gross_factor_candidates": 6, "validated_events": 0},
    }

    assert allocator.current_gross_fail_families(
        funnel,
        _current_factor_protocol(),
        event_report,
    ) == set()


def test_old_method_for_different_family_does_not_reopen_current_failure() -> None:
    funnel = {
        "current_target": {"family_codes": ["A2"]},
        "current_target_decision": {
            "decision": "stop_adjacent_variant_generation",
            "factor_target_stale": False,
            "validated_factor_events": 0,
        },
        "entries": [
            {
                "experiment": "CURRENT_FACTOR_EVENT_GATE",
                "primary_category": "gross_fail",
            }
        ],
    }
    event_report = {
        "factor_event_method_version": FACTOR_EVENT_METHOD_VERSION - 1,
        "research_target": {"family_codes": ["C1"]},
        "summary": {"gross_factor_candidates": 6, "validated_events": 0},
    }

    assert allocator.current_gross_fail_families(
        funnel,
        _current_factor_protocol(),
        event_report,
    ) == {"uptrend_failed_pullback_long"}


def test_completed_factor_failure_suspends_same_window_family() -> None:
    row = allocator.score_family(
        "uptrend_failed_pullback_long",
        allocator.STRATEGY_TAXONOMY["uptrend_failed_pullback_long"],
        {},
        registry_count=0,
        windows=[{"name": "bull_0"}, {"name": "bull_1"}],
        has_long_registry=False,
        coverage_row={"completed_factor_failures": ["a2_factor_fail.json"]},
    )

    assert row["eligible"] is False
    assert "completed_factor_failure_same_windows" in row["suspension_sources"]


def test_new_auxiliary_archives_reopen_completed_factor_evidence(tmp_path) -> None:
    source = tmp_path / "BTCUSDT"
    source.mkdir()
    (source / "one.zip").write_bytes(b"one")
    report = {
        "data_audit": [
            {
                "auxiliary": [
                    {
                        "requirement": "open_interest_metrics",
                        "status": "available",
                        "path": str(source),
                        "archives": 1,
                    }
                ]
            }
        ]
    }

    assert allocator.factor_report_auxiliary_inputs_current(report) is True
    (source / "two.zip").write_bytes(b"two")
    assert allocator.factor_report_auxiliary_inputs_current(report) is False


def test_portfolio_saturated_family_is_not_eligible() -> None:
    row = allocator.score_family(
        "volatility_compression_directional_expansion",
        allocator.STRATEGY_TAXONOMY["volatility_compression_directional_expansion"],
        {"portfolio_coverage_saturated": True},
        registry_count=0,
        windows=[{"name": "high_vol_0"}, {"name": "high_vol_1"}],
        has_long_registry=False,
        coverage_row={},
    )

    assert row["eligible"] is False
    assert "portfolio_coverage_saturated" in row["blockers"]


def test_explicit_evidence_wait_suspends_registered_family() -> None:
    row = allocator.score_family(
        "downtrend_pullback_short",
        allocator.STRATEGY_TAXONOMY["downtrend_pullback_short"],
        {},
        registry_count=1,
        windows=[{"name": "bear_0"}, {"name": "bear_1"}],
        has_long_registry=False,
        coverage_row={
            "evidence_wait_controls": [
                {
                    "lesson_id": "c1_negative_control",
                    "status": allocator.EVIDENCE_WAIT_STATUS,
                    "active_home_windows": ["bear_0", "bear_1"],
                    "blocker_fingerprint": "frozen-blocker",
                }
            ]
        },
        current_blocker_fingerprint="frozen-blocker",
    )

    assert row["eligible"] is False
    assert row["evidence_wait_for_new_episode"] is True
    assert allocator.EVIDENCE_WAIT_STATUS in row["blockers"]
    assert "same_evidence_failure_limit_reached" not in row["blockers"]


def test_explicit_evidence_wait_reopens_after_window_or_blocker_change() -> None:
    coverage = {
        "evidence_wait_controls": [
            {
                "lesson_id": "c1_negative_control",
                "status": allocator.EVIDENCE_WAIT_STATUS,
                "active_home_windows": ["bear_0", "bear_1"],
                "blocker_fingerprint": "frozen-blocker",
            }
        ]
    }
    changed_window = allocator.score_family(
        "downtrend_pullback_short",
        allocator.STRATEGY_TAXONOMY["downtrend_pullback_short"],
        {},
        registry_count=1,
        windows=[{"name": "bear_0"}, {"name": "bear_2"}],
        has_long_registry=False,
        coverage_row=coverage,
        current_blocker_fingerprint="frozen-blocker",
    )
    changed_blocker = allocator.score_family(
        "downtrend_pullback_short",
        allocator.STRATEGY_TAXONOMY["downtrend_pullback_short"],
        {},
        registry_count=1,
        windows=[{"name": "bear_0"}, {"name": "bear_1"}],
        has_long_registry=False,
        coverage_row=coverage,
        current_blocker_fingerprint="new-blocker",
    )

    assert changed_window["eligible"] is True
    assert changed_blocker["eligible"] is True
    assert changed_window["evidence_wait_for_new_episode"] is False
    assert changed_blocker["evidence_wait_for_new_episode"] is False


def test_other_family_blocker_does_not_release_evidence_wait() -> None:
    row = allocator.score_family(
        "downtrend_pullback_short",
        allocator.STRATEGY_TAXONOMY["downtrend_pullback_short"],
        {},
        registry_count=1,
        windows=[{"name": "bear_0"}, {"name": "bear_1"}],
        has_long_registry=False,
        coverage_row={
            "evidence_wait_controls": [
                {
                    "lesson_id": "c1_negative_control",
                    "status": allocator.EVIDENCE_WAIT_STATUS,
                    "active_home_windows": ["bear_0", "bear_1"],
                    "blocker_fingerprint": "c1-blocker",
                }
            ]
        },
        current_blocker_fingerprint=None,
    )

    assert row["eligible"] is False
    assert row["evidence_wait_for_new_episode"] is True


def test_global_coverage_deduplicates_event_pointers(tmp_path) -> None:
    event_payload = {"rows": [{"family": "uptrend_pullback_long"}]}
    (tmp_path / "latest_one.json").write_text(__import__("json").dumps(event_payload), encoding="utf-8")
    (tmp_path / "latest_copy.json").write_text(__import__("json").dumps(event_payload), encoding="utf-8")
    memory = {
        "manual_lessons": [
            {
                "id": "c2_failed",
                "strategy_family": "uptrend_pullback_long",
                "lesson": "The event failed locked validation.",
            },
            {
                "id": "range_composite",
                "strategy_family": "B1|B2",
                "lesson": "Research record only.",
            },
        ]
    }

    coverage = allocator.global_family_coverage(memory, _manifest(), tmp_path)

    assert coverage["uptrend_pullback_long"]["durable_lessons"] == ["c2_failed"]
    assert coverage["uptrend_pullback_long"]["negative_durable_lessons"] == ["c2_failed"]
    assert len(coverage["uptrend_pullback_long"]["event_artifacts"]) == 1
    assert coverage["range_upper_reversion_short"]["durable_lessons"] == ["range_composite"]
    assert coverage["range_lower_reversion_long"]["durable_lessons"] == ["range_composite"]
