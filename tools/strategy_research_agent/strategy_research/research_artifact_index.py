#!/usr/bin/env python3
"""Version and index context-specific research reports without pointer drift."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


INDEX_VERSION = 2
MAX_RUNS = 300


def safe_token(value: object) -> str:
    text = str(value or "none").strip().lower()
    return re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-") or "none"


def context_key(payload: dict[str, Any]) -> str:
    scope = safe_token(payload.get("pair_scope") or "explicit")
    regime = safe_token(payload.get("regime_label") or "all")
    target = payload.get("research_target") or {}
    families = target.get("family_codes") or payload.get("target_family_codes") or ["unscoped"]
    family_token = "+".join(safe_token(item) for item in families)
    timeframes = payload.get("timeframes") or [payload.get("timeframe") or "none"]
    timeframe_token = "+".join(safe_token(item) for item in sorted(timeframes))
    pairs = sorted(str(item) for item in payload.get("pairs") or [])
    pair_digest = hashlib.sha256("\0".join(pairs).encode("utf-8")).hexdigest()[:12]
    target_fingerprint = safe_token(target.get("target_fingerprint") or "none")[:12]
    return (
        f"scope={scope}__regime={regime}__families={family_token}"
        f"__timeframes={timeframe_token}__pairs={len(pairs)}-{pair_digest}"
        f"__target={target_fingerprint}"
    )


def load_index(path: Path, artifact_type: str) -> dict[str, Any]:
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("index_version") == INDEX_VERSION:
            return payload
    return {
        "index_version": INDEX_VERSION,
        "artifact_type": artifact_type,
        "latest_by_context": {},
        "current_target": None,
        "runs": [],
    }


def publish_report(
    *,
    payload: dict[str, Any],
    json_path: Path,
    md_path: Path,
    index_path: Path,
    artifact_type: str,
    generic_latest_json: Path,
    generic_latest_md: Path,
    publish_current: bool,
) -> dict[str, Any]:
    """Index a report and update scoped pointers.

    All-history diagnostics are indexed but cannot replace the generic current
    pointer unless the caller explicitly marks them as current-target evidence.
    """

    key = context_key(payload)
    scoped_json = json_path.parent / f"latest_{artifact_type}__{safe_token(key)}.json"
    scoped_md = md_path.parent / f"latest_{artifact_type}__{safe_token(key)}.md"
    shutil.copyfile(json_path, scoped_json)
    shutil.copyfile(md_path, scoped_md)

    index = load_index(index_path, artifact_type)
    record = {
        "generated_at_utc": payload.get("generated_at_utc"),
        "context_key": key,
        "pair_scope": payload.get("pair_scope"),
        "pairs": payload.get("pairs"),
        "timeframe": payload.get("timeframe"),
        "timeframes": payload.get("timeframes"),
        "regime_label": payload.get("regime_label"),
        "research_target": payload.get("research_target"),
        "json_path": str(json_path),
        "md_path": str(md_path),
        "scoped_json_path": str(scoped_json),
        "scoped_md_path": str(scoped_md),
    }
    index["latest_by_context"][key] = record
    index["runs"] = ([record] + list(index.get("runs") or []))[:MAX_RUNS]
    if publish_current:
        shutil.copyfile(json_path, generic_latest_json)
        shutil.copyfile(md_path, generic_latest_md)
        index["current_target"] = record
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def current_target_report(index_path: Path) -> Path:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    record = index.get("current_target") or {}
    path = record.get("json_path")
    if not path:
        raise ValueError(f"No current-target report registered in {index_path}")
    return Path(path)
