from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import import_e62_background_data as importer  # noqa: E402


def test_verified_staged_receipt_imports_once(monkeypatch, tmp_path) -> None:
    stage_root = tmp_path / "stage"
    repo_root = tmp_path / "repo"
    relative_segment = (
        "user_data/data/binance/futures_aux/force_order_market_v2/segment.jsonl.gz"
    )
    segment = stage_root / relative_segment
    segment.parent.mkdir(parents=True)
    segment.write_bytes(b"frozen-force-order-segment")
    segment_hash = hashlib.sha256(segment.read_bytes()).hexdigest()
    receipt = (
        stage_root
        / "user_data/strategy_research/data_receipts/force_order_market_v2/2026-07-23/segment.receipt.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "segment": relative_segment,
                "segment_sha256": segment_hash,
                "segment_bytes": segment.stat().st_size,
                "rows": 3,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(importer, "STAGE_ROOT", stage_root)
    monkeypatch.setattr(importer, "REPO_ROOT", repo_root)
    monkeypatch.setattr(importer, "DEST_RECEIPTS", repo_root / "receipts")

    first = importer.import_receipt(receipt)
    second = importer.import_receipt(receipt)

    assert first["segment_status"] == "imported"
    assert first["receipt_status"] == "imported"
    assert second["segment_status"] == "already_present"
    assert second["receipt_status"] == "already_present"
    assert (repo_root / relative_segment).read_bytes() == segment.read_bytes()


def test_build_payload_discovers_dated_receipt_directories(
    monkeypatch, tmp_path
) -> None:
    stage_root = tmp_path / "stage"
    repo_root = tmp_path / "repo"
    receipt_root = (
        stage_root
        / "user_data/strategy_research/data_receipts/force_order_market_v2"
    )
    relative_segment = (
        "user_data/data/binance/futures_aux/force_order_market_v2/2026-07-23/segment.ndjson.gz"
    )
    segment = stage_root / relative_segment
    segment.parent.mkdir(parents=True)
    segment.write_bytes(b"dated-force-order-segment")
    receipt = receipt_root / "2026-07-23/segment.receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "segment": relative_segment,
                "segment_sha256": hashlib.sha256(segment.read_bytes()).hexdigest(),
                "segment_bytes": segment.stat().st_size,
                "rows": 1,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(importer, "STAGE_ROOT", stage_root)
    monkeypatch.setattr(importer, "STAGE_RECEIPTS", receipt_root)
    monkeypatch.setattr(importer, "REPO_ROOT", repo_root)
    monkeypatch.setattr(importer, "DEST_RECEIPTS", repo_root / "receipts")

    payload = importer.build_payload()

    assert payload["staged_receipts"] == 1
    assert payload["new_segments"] == 1
    assert payload["new_receipts"] == 1
    assert payload["outcomes_read"] is False


def test_staged_receipt_rejects_unexpected_segment_tree(monkeypatch, tmp_path) -> None:
    stage_root = tmp_path / "stage"
    receipt = stage_root / "receipt.json"
    stage_root.mkdir()
    receipt.write_text(
        json.dumps(
            {
                "segment": "../../outside.jsonl.gz",
                "segment_sha256": "unused",
                "segment_bytes": 0,
                "rows": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(importer, "STAGE_ROOT", stage_root)

    try:
        importer.import_receipt(receipt)
    except ValueError as exc:
        assert "unexpected staged segment path" in str(exc)
    else:
        raise AssertionError("unexpected segment path was accepted")
