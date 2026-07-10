from __future__ import annotations

import sys
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

import build_strategy_lineage as lineage


def test_latest_family_gate_strategies_enter_lineage_as_research_evidence(monkeypatch) -> None:
    gate_row = {
        "strategy": "C2BullSol15mPullbackResumeVolumeQ80Long",
        "strategy_family": "uptrend_pullback_long",
        "state": "research_candidate",
        "blocks": "home-regime guarded profit 24.2586% <= 30.0%",
        "target_65d_guarded_pct": 24.2586,
        "ready_for_manual_dryrun_review": False,
    }
    gate = {
        "source_csv": "user_data/strategy_research/reports/c2_experiment.csv",
        "risk_controls": {},
    }
    monkeypatch.setattr(lineage, "current_registry", lambda: {"strategies": []})
    monkeypatch.setattr(lineage, "load_family_gate", lambda: ({gate_row["strategy"]: gate_row}, gate))
    monkeypatch.setattr(lineage, "collect_pool_cards", lambda: [])

    payload = lineage.build_payload()

    node = next(item for item in payload["nodes"] if item["name"] == gate_row["strategy"])
    assert node["generation"] == "registered_family_gate_experiment"
    assert node["pool_status"] == "research_evidence"
    assert node["recommended_state"] == "research_candidate"
    assert gate["source_csv"] in node["evidence_paths"]
