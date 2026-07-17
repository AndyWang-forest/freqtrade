from __future__ import annotations

import argparse
import sys
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

import family_risk_gate as gate  # noqa: E402


def test_native_protection_evidence_uses_freqtrade_result_directly(monkeypatch) -> None:
    row = {
        "adjusted_profit_pct": "12.5",
        "profit_total_pct": "15.0",
        "trades": "9",
        "max_drawdown_pct": "4.2",
    }
    monkeypatch.setattr(
        gate,
        "simulate_row_from_trades",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("native evidence must not be replayed")),
    )

    result = gate.simulate_row(
        row,
        drawdown_pause_pct=10.0,
        consecutive_loss_pause=3,
        evidence_model=gate.RISK_EVIDENCE_NATIVE,
    )

    assert result.guarded_profit_pct == 12.5
    assert result.trades_taken == 9
    assert result.evidence_mode == gate.RISK_EVIDENCE_NATIVE


def test_raw_rows_with_trades_cannot_pass_as_guarded_runtime_evidence(monkeypatch) -> None:
    row = {
        "strategy": "FrozenCandidate",
        "strategy_family": "volatility_compression_directional_expansion",
        "slice": "home",
        "window": "home_one",
        "scenario": "realistic_fee_5bps",
        "adjusted_profit_pct": "45.0",
        "trades": "12",
        "max_drawdown_pct": "3.0",
    }
    monkeypatch.setattr(gate, "scenario_is_high_fee", lambda _row: True)
    monkeypatch.setattr(gate, "scenario_is_stress", lambda _row: False)
    monkeypatch.setattr(gate, "family_role_names", lambda _manifest, _family, role: {"home_one"} if role == "home" else set())

    summary = gate.summarize_strategy(
        "FrozenCandidate",
        "volatility_compression_directional_expansion",
        [row],
        10.0,
        3,
        {},
        gate.RISK_EVIDENCE_RAW,
    )

    assert summary["ready_for_manual_dryrun_review"] is False
    assert any("native finite Freqtrade protection evidence is missing" in item for item in summary["blockers"])


def test_candidate_alias_does_not_change_artifact_strategy_name() -> None:
    row = {
        "strategy": "FrozenCandidateRuntimeProtections",
        "candidate_strategy": "FrozenCandidate",
    }

    assert gate.candidate_strategy_name(row, None) == "FrozenCandidate"
    assert gate.row_key(row)[0] == "FrozenCandidateRuntimeProtections"


def test_cli_defaults_to_raw_unprotected(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["family_risk_gate.py"])

    args: argparse.Namespace = gate.parse_args()

    assert args.risk_evidence_model == gate.RISK_EVIDENCE_RAW
