#!/usr/bin/env python3
"""Register and resolve deterministic experiment inputs for research gates."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def find_repo_root() -> Path:
    for start in [Path.cwd(), Path(__file__).resolve()]:
        for path in [start, *start.parents]:
            if (path / "pyproject.toml").exists():
                return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/reports"
LATEST_SOURCE = REPORT_DIR / "latest_experiment_source.json"
SCHEMA_VERSION = 1


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_csv_path(path: str | Path) -> Path:
    csv_path = Path(path)
    if not csv_path.is_absolute():
        csv_path = REPO_ROOT / csv_path
    csv_path = csv_path.resolve()
    if not csv_path.exists() or not csv_path.is_file():
        raise FileNotFoundError(f"Experiment CSV does not exist: {csv_path}")
    if csv_path.suffix.lower() != ".csv":
        raise ValueError(f"Experiment input must be CSV: {csv_path}")
    return csv_path


def register_experiment(
    csv_path: str | Path,
    *,
    producer: str,
    metadata: dict[str, Any] | None = None,
    pointer_path: Path = LATEST_SOURCE,
) -> dict[str, Any]:
    path = normalize_csv_path(csv_path)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "registered_at_utc": now_utc(),
        "producer": producer,
        "csv_path": rel(path),
        "csv_sha256": sha256_file(path),
        "csv_size_bytes": path.stat().st_size,
        "metadata": metadata or {},
    }
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def resolve_experiment(
    explicit_csv: str | Path | None = None,
    *,
    pointer_path: Path = LATEST_SOURCE,
    register_explicit: bool = True,
    producer: str = "explicit_gate_input",
) -> tuple[Path, dict[str, Any]]:
    if explicit_csv is not None:
        path = normalize_csv_path(explicit_csv)
        if register_explicit:
            payload = register_experiment(path, producer=producer, pointer_path=pointer_path)
        else:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "producer": producer,
                "csv_path": rel(path),
                "csv_sha256": sha256_file(path),
            }
        return path, payload

    if not pointer_path.exists():
        raise FileNotFoundError(
            "No deterministic experiment source is registered. Pass --csv <path> once; "
            f"the gate will register {rel(pointer_path)} for repeatable reruns."
        )
    payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported experiment source schema: {payload.get('schema_version')}")
    path = normalize_csv_path(payload.get("csv_path") or "")
    actual_hash = sha256_file(path)
    if actual_hash != payload.get("csv_sha256"):
        raise ValueError(
            f"Experiment CSV changed after registration: {rel(path)}. "
            "Register the intended file again with --csv."
        )
    return path, payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="CSV to register as the deterministic gate input.")
    parser.add_argument("--producer", default="manual_registration")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = register_experiment(args.csv, producer=args.producer)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
