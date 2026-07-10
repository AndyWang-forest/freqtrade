from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_SOURCE = REPO_ROOT / "tools/strategy_research_agent/strategy_research"
AGENT_INSTALLER = REPO_ROOT / "tools/strategy_research_agent"
sys.path.insert(0, str(AGENT_SOURCE))
sys.path.insert(0, str(AGENT_INSTALLER))

import experiment_provenance  # noqa: E402
import family_exit_risk_contract  # noqa: E402
import family_risk_gate  # noqa: E402
import managed_runtime_files  # noqa: E402
import regime_window_builder  # noqa: E402


def test_family_peak_contract_defaults_off() -> None:
    assert family_exit_risk_contract.validate_contract_table() == []
    for family in family_exit_risk_contract.CANONICAL_FAMILIES:
        assert family_exit_risk_contract.family_exit_contract(family)["default_peak_mode"] == "off"


def test_a1_peak40_is_promotion_allowed() -> None:
    verdict = family_exit_risk_contract.validate_promotion_peak_mode("downtrend_failed_bounce_short", "peak40")
    assert verdict.allowed


def test_e_peak40_is_blocked_even_through_runtime_alias() -> None:
    verdict = family_exit_risk_contract.validate_promotion_peak_mode(
        "volatility_compression_breakout", "peak40"
    )
    assert verdict.family == "volatility_compression_directional_expansion"
    assert not verdict.allowed


def test_other_family_peak_requires_future_contract_update() -> None:
    verdict = family_exit_risk_contract.validate_promotion_peak_mode("uptrend_pullback_long", "peak40")
    assert not verdict.allowed


def test_unknown_family_cannot_bypass_contract_with_peak_off() -> None:
    verdict = family_exit_risk_contract.validate_promotion_peak_mode("unregistered_family", "off")
    assert not verdict.allowed


def test_peak_mode_recognizes_named_presets_only() -> None:
    assert family_exit_risk_contract.peak_mode_from_values(None, None) == "off"
    assert family_exit_risk_contract.peak_mode_from_values(0.40, 0.40) == "peak40"
    assert family_exit_risk_contract.peak_mode_from_values(0.55, 0.40) == "custom_or_invalid"


def regime_frame(labels: list[str]) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(labels), freq="1D", tz="UTC")
    size = len(labels)
    return pd.DataFrame(
        {
            "provided_label": labels,
            "combined_ret_60d": [0.20] * size,
            "combined_ema_gap": [0.05] * size,
            "combined_vol_pctile": [0.50] * size,
            "combined_atr_pctile": [0.50] * size,
            "combined_bb_width_pctile": [0.50] * size,
            "combined_trend_efficiency": [0.30] * size,
            "direction_agreement_60d": [1.0] * size,
            "btc_close": list(range(100, 100 + size)),
            "eth_close": list(range(200, 200 + size)),
            "combined_ret_30d": [0.10] * size,
        },
        index=index,
    )


def test_low_confidence_regime_is_not_active(monkeypatch: pytest.MonkeyPatch) -> None:
    labels = ["bull" if index % 2 == 0 else "mixed" for index in range(180)]
    monkeypatch.setattr(regime_window_builder, "label_daily", lambda row: row["provided_label"])

    windows = regime_window_builder.select_windows(regime_frame(labels))
    bull = [item for item in windows if item["label"] == "bull"]

    assert bull
    assert not any(item["status"] == "active" for item in bull)
    assert any(item["status"] == "insufficient_confidence" for item in bull)


def test_independent_regime_episodes_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    labels = ["bull"] * 75 + ["mixed"] * 55 + ["bull"] * 75 + ["mixed"] * 55
    monkeypatch.setattr(regime_window_builder, "label_daily", lambda row: row["provided_label"])

    windows = regime_window_builder.select_windows(regime_frame(labels))
    active = [item for item in windows if item["label"] == "bull" and item["status"] == "active"]

    assert len(active) >= 2
    assert active[0]["episode_role"] == "primary"
    assert all(item["episode_role"] in {"primary", "validation_episode"} for item in active)


def test_disjoint_regime_windows_have_zero_overlap() -> None:
    assert regime_window_builder.overlap_share(
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2025-01-31", tz="UTC"),
        pd.Timestamp("2025-03-01", tz="UTC"),
        pd.Timestamp("2025-03-31", tz="UTC"),
    ) == 0.0


def test_registered_experiment_rejects_mutated_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "experiment.csv"
    pointer = tmp_path / "pointer.json"
    csv_path.write_text("strategy,profit\nA,1\n", encoding="utf-8")
    experiment_provenance.register_experiment(
        csv_path,
        producer="test",
        pointer_path=pointer,
    )
    csv_path.write_text("strategy,profit\nA,2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after registration"):
        experiment_provenance.resolve_experiment(pointer_path=pointer)


def test_runtime_manifest_only_removes_previously_managed_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "managed.py").write_text("value = 1\n", encoding="utf-8")
    managed_runtime_files.sync_manifest(source, target)
    (target / "managed.py").write_text("value = 1\n", encoding="utf-8")
    (target / "local_experiment.py").write_text("value = 2\n", encoding="utf-8")
    (source / "managed.py").unlink()

    payload = managed_runtime_files.sync_manifest(source, target)

    assert payload["removed_files"] == ["managed.py"]
    assert not (target / "managed.py").exists()
    assert (target / "local_experiment.py").exists()


def test_family_gate_applies_explicit_stress_floor() -> None:
    family = "downtrend_failed_bounce_short"
    strategy = "TestStrategy"
    base = {
        "strategy": strategy,
        "strategy_family": family,
        "slice": "manifest",
        "window": "manifest_bear_bear_episode",
        "timerange": "20250101-20250301",
        "trades": "10",
        "profit_total_pct": "35",
        "adjusted_profit_pct": "35",
        "artifact": "",
    }
    rows = [
        {**base, "scenario": "realistic_fee_5bps"},
        {**base, "scenario": "stress_fee_20bps", "adjusted_profit_pct": "-20"},
    ]
    manifest = {
        "windows": [
            {"name": "bear_episode", "label": "bear", "status": "active"},
            {"name": "bear_validation", "label": "bear", "status": "active"},
        ],
        "family_window_roles": {
            family: {"home": ["bear_episode", "bear_validation"], "hostile": []}
        },
    }

    verdict = family_risk_gate.summarize_strategy(strategy, family, rows, 10.0, 3, manifest)

    assert any("stress home total" in blocker for blocker in verdict["blockers"])
    assert any("stress home worst" in blocker for blocker in verdict["blockers"])
    assert any("validation episodes missing" in blocker for blocker in verdict["blockers"])


def test_family_gate_accepts_manifest_named_multi_episode_rows() -> None:
    row = {
        "slice": "multi_episode_high_vol",
        "window": "high_vol_episode_2_high_vol_20240314_20240512",
    }

    assert family_risk_gate.manifest_row_matches(row, {"high_vol_20240314_20240512"})


def test_family_gate_ranks_repeated_home_edge_before_higher_aggregate_profit() -> None:
    repeated = {
        "ready_for_manual_dryrun_review": False,
        "home_episode_positive": 2,
        "home_episode_total": 3,
        "target_65d_guarded_pct": 21.0,
        "stress_home_total_guarded_pct": 1.0,
        "hostile_guarded_worst_pct": 0.0,
    }
    lucky = {
        **repeated,
        "home_episode_positive": 1,
        "target_65d_guarded_pct": 30.0,
        "stress_home_total_guarded_pct": 10.0,
    }

    assert family_risk_gate.family_candidate_rank(repeated) > family_risk_gate.family_candidate_rank(lucky)


def test_experiment_pointer_is_machine_readable(tmp_path: Path) -> None:
    csv_path = tmp_path / "experiment.csv"
    pointer = tmp_path / "pointer.json"
    csv_path.write_text("strategy,profit\nA,1\n", encoding="utf-8")

    payload = experiment_provenance.register_experiment(
        csv_path,
        producer="test",
        pointer_path=pointer,
    )

    assert json.loads(pointer.read_text(encoding="utf-8"))["csv_sha256"] == payload["csv_sha256"]
