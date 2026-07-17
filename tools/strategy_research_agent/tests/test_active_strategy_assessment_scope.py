from __future__ import annotations

import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import analyze_strategy_research as assessment  # noqa: E402


def test_rejected_archive_is_not_an_active_score_source() -> None:
    assert all(path.name != "rejected" for path in assessment.POOL_DIRS)


def test_registry_is_loaded_as_active_evidence(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "strategy_registry.json"
    registry.write_text(
        json.dumps(
            {
                "strategies": [
                    {
                        "name": "CurrentCandidate",
                        "family": "range_mean_reversion",
                        "state": "research_candidate",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(assessment, "REGISTRY_JSON", registry)

    loaded = assessment.load_registry_metrics()

    assert set(loaded) == {"CurrentCandidate"}
    assert loaded["CurrentCandidate"]["pool"] == "registry"
