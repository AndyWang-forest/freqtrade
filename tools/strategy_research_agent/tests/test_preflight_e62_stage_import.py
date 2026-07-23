from __future__ import annotations

import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import preflight_research_agent as preflight  # noqa: E402


def test_e62_stage_import_is_optional_before_first_collection(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(preflight, "E62_STAGE_IMPORT", tmp_path / "missing.json")
    checks: list[preflight.Check] = []

    preflight.check_e62_stage_import(checks)

    assert checks == [
        preflight.Check(
            name="e62_stage_import",
            status="warn",
            detail=f"No staged blind receipts imported yet: {tmp_path / 'missing.json'}",
        )
    ]


def test_e62_stage_import_rejects_outcome_access(monkeypatch, tmp_path) -> None:
    path = tmp_path / "latest_stage_import.json"
    path.write_text(
        json.dumps(
            {
                "research_only": True,
                "outcomes_read": True,
                "staged_receipts": 1,
                "rows": [
                    {
                        "segment": "user_data/data/binance/futures_aux/force_order_market_v2/segment.jsonl.gz"
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(preflight, "E62_STAGE_IMPORT", path)
    checks: list[preflight.Check] = []

    preflight.check_e62_stage_import(checks)

    assert checks[0].status == "fail"
    assert "blind acquisition contract" in checks[0].detail


def test_e62_stage_import_accepts_verified_blind_receipts(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "latest_stage_import.json"
    path.write_text(
        json.dumps(
            {
                "research_only": True,
                "outcomes_read": False,
                "staged_receipts": 2,
                "rows": [
                    {
                        "segment": "user_data/data/binance/futures_aux/force_order_market_v2/segment.jsonl.gz"
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(preflight, "E62_STAGE_IMPORT", path)
    checks: list[preflight.Check] = []

    preflight.check_e62_stage_import(checks)

    assert checks[0].status == "ok"
    assert "receipts=2" in checks[0].detail
    assert "outcomes unread" in checks[0].detail


def test_e62_stage_import_warns_until_first_receipt(monkeypatch, tmp_path) -> None:
    path = tmp_path / "latest_stage_import.json"
    path.write_text(
        json.dumps(
            {
                "research_only": True,
                "outcomes_read": False,
                "staged_receipts": 0,
                "rows": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(preflight, "E62_STAGE_IMPORT", path)
    checks: list[preflight.Check] = []

    preflight.check_e62_stage_import(checks)

    assert checks[0].status == "warn"
    assert "no staged receipts" in checks[0].detail
