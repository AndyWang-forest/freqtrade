from __future__ import annotations

import argparse
import sys
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

import analyze_trade_behavior as trade_behavior
import event_execution_alignment as alignment
import family_risk_gate as family_gate
import run_event_study as event_study


def empty_alignment_args() -> argparse.Namespace:
    return argparse.Namespace(
        target_file=None,
        strategy=None,
        name=None,
        event_module=None,
        event=None,
        pair=None,
        pair_key=None,
        timeframe=None,
        timerange=None,
        artifact=None,
        experiment_csv=None,
        window=None,
        scenario=None,
        side=None,
        horizon=None,
        startup_candles=0,
    )


def test_alignment_defaults_to_registered_experiment_metadata(monkeypatch, tmp_path: Path) -> None:
    csv_path = tmp_path / "experiment.csv"
    csv_path.write_text("strategy,window,scenario,artifact,timerange\n", encoding="utf-8")
    target = {"name": "current-experiment-target"}
    monkeypatch.setattr(
        alignment,
        "resolve_experiment",
        lambda register_explicit=False: (
            csv_path,
            {"metadata": {"alignment_targets": [target]}},
        ),
    )
    monkeypatch.setattr(alignment, "DEFAULT_TARGETS", tmp_path / "missing.json")

    assert alignment.load_targets(empty_alignment_args()) == [target]


def test_family_gate_preserves_registered_alignment_metadata(monkeypatch, tmp_path: Path) -> None:
    csv_path = tmp_path / "experiment.csv"
    csv_path.write_text("strategy,window,scenario,artifact,timerange\n", encoding="utf-8")
    metadata = {"alignment_targets": [{"name": "current-experiment-target"}]}
    captured: dict[str, object] = {}
    args = argparse.Namespace(csv_path=str(csv_path), json=False)

    monkeypatch.setattr(family_gate, "parse_args", lambda: args)
    def resolve(explicit_csv=None, **kwargs):
        if explicit_csv is not None:
            return csv_path, {"metadata": {}}
        return csv_path, {"metadata": metadata}

    monkeypatch.setattr(family_gate, "resolve_experiment", resolve)
    monkeypatch.setattr(family_gate, "build_payload", lambda *args, **kwargs: {})
    monkeypatch.setattr(family_gate, "write_json", lambda payload: tmp_path / "gate.json")
    monkeypatch.setattr(family_gate, "write_markdown", lambda payload: tmp_path / "gate.md")

    def capture_registration(path, *, producer, metadata=None):
        captured["metadata"] = metadata
        return {"metadata": metadata}

    monkeypatch.setattr(family_gate, "register_experiment", capture_registration)

    assert family_gate.main() == 0
    assert captured["metadata"] == metadata


def test_trade_behavior_uses_primary_scenario_artifacts_once(monkeypatch, tmp_path: Path) -> None:
    primary_zip = tmp_path / "primary.zip"
    stress_zip = tmp_path / "stress.zip"
    primary_zip.touch()
    stress_zip.touch()
    csv_path = tmp_path / "experiment.csv"
    csv_path.write_text(
        "strategy,scenario,artifact\n"
        "one,realistic_fee_5bps,primary.zip\n"
        "two,realistic_fee_5bps,primary.zip\n"
        "one,stress_fee_20bps,stress.zip\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(trade_behavior, "REPO_ROOT", tmp_path)

    assert trade_behavior.zips_from_experiment_csv(csv_path) == [primary_zip.resolve()]


def test_q80_event_window_represents_24_days_on_each_timeframe() -> None:
    assert event_study.rolling_days_bars("3m", 24) == 11520
    assert event_study.rolling_days_bars("5m", 24) == 6912
    assert event_study.rolling_days_bars("15m", 24) == 2304
