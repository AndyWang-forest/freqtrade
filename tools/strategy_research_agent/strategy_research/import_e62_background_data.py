#!/usr/bin/env python3
"""Verify and import staged E62 blind receipts into the local research tree."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
RESEARCH_ROOT = REPO_ROOT / "user_data/strategy_research"
STAGE_ROOT = Path(
    os.environ.get(
        "E62_STAGE_ROOT",
        Path.home()
        / "Library/Application Support/FreqtradeStrategyResearch/e62-runtime",
    )
).expanduser()
STAGE_RECEIPTS = (
    STAGE_ROOT / "user_data/strategy_research/data_receipts/force_order_market_v2"
)
DEST_RECEIPTS = RESEARCH_ROOT / "data_receipts/force_order_market_v2"
OUTPUT_DIR = RESEARCH_ROOT / "background/e62"
LATEST_JSON = OUTPUT_DIR / "latest_stage_import.json"
LATEST_MD = OUTPUT_DIR / "latest_stage_import.md"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_stage_path(relative_path: str) -> Path:
    candidate = (STAGE_ROOT / relative_path).resolve()
    if not candidate.is_relative_to(STAGE_ROOT.resolve()):
        raise ValueError(f"staged receipt escapes stage root: {relative_path}")
    return candidate


def copy_verified(source: Path, destination: Path, expected_sha256: str) -> str:
    observed = sha256(source)
    if observed != expected_sha256:
        raise ValueError(f"sha256 mismatch for {source}")
    if destination.exists():
        if sha256(destination) != expected_sha256:
            raise ValueError(f"existing destination differs: {destination}")
        return "already_present"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)
    return "imported"


def import_receipt(path: Path) -> dict[str, Any]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    relative_segment = str(receipt["segment"])
    if not relative_segment.startswith(
        "user_data/data/binance/futures_aux/force_order_market_v2/"
    ):
        raise ValueError(f"unexpected staged segment path: {relative_segment}")
    segment = safe_stage_path(relative_segment)
    if not segment.exists():
        raise FileNotFoundError(segment)
    if segment.stat().st_size != int(receipt["segment_bytes"]):
        raise ValueError(f"segment byte mismatch for {segment}")
    segment_status = copy_verified(
        segment,
        REPO_ROOT / relative_segment,
        str(receipt["segment_sha256"]),
    )
    receipt_status = copy_verified(path, DEST_RECEIPTS / path.name, sha256(path))
    return {
        "receipt": path.name,
        "segment": relative_segment,
        "rows": int(receipt["rows"]),
        "segment_status": segment_status,
        "receipt_status": receipt_status,
    }


def build_payload() -> dict[str, Any]:
    rows = []
    if STAGE_RECEIPTS.exists():
        rows = [
            import_receipt(path)
            for path in sorted(STAGE_RECEIPTS.rglob("*.receipt.json"))
        ]
    return {
        "generated_at_utc": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "research_only": True,
        "stage_root": str(STAGE_ROOT),
        "staged_receipts": len(rows),
        "new_segments": sum(row["segment_status"] == "imported" for row in rows),
        "new_receipts": sum(row["receipt_status"] == "imported" for row in rows),
        "rows": rows,
        "outcomes_read": False,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# E62 Staged Import",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Staged receipts: `{payload['staged_receipts']}`",
        f"- New segments: `{payload['new_segments']}`",
        f"- New receipts: `{payload['new_receipts']}`",
        "- Outcomes read: `False`",
        "",
    ]
    LATEST_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
