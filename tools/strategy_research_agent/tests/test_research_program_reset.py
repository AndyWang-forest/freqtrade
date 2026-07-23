from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import research_program_reset as reset  # noqa: E402


def test_pair_coverage_requires_both_liquidation_sides() -> None:
    counts = reset.e62_counts(
        {
            "independent_event_counts": {
                "events": 100,
                "long_liquidation_events": 80,
                "short_liquidation_events": 20,
                "pairs_per_liquidation_side": {"long": 5},
                "independent_utc_dates": 4,
                "observed_utc_hours": 15,
            }
        }
    )

    assert counts["pairs_per_liquidation_side"] == 0


def test_program_reset_rejects_premature_e62_outcome_access(
    monkeypatch, tmp_path
) -> None:
    postmortem = tmp_path / "postmortem.json"
    postmortem.write_text(
        json.dumps({"scope": "E1-E62", "summary": {"program_buckets": {}}}),
        encoding="utf-8",
    )
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps(
            {
                "outcomes_read": True,
                "independent_event_counts": {
                    "events": 1,
                    "long_liquidation_events": 1,
                    "short_liquidation_events": 0,
                    "pairs_per_liquidation_side": {"long": 1, "short": 0},
                    "independent_utc_dates": 1,
                    "observed_utc_hours": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reset, "POSTMORTEM", postmortem)
    monkeypatch.setattr(reset, "E62_READINESS", readiness)

    with pytest.raises(ValueError, match="before the frozen count gate"):
        reset.build_payload()
