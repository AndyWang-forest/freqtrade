#!/usr/bin/env python3
"""Track installer-owned runtime files and remove only retired managed files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MANIFEST_NAME = ".managed_agent_runtime.json"


def managed_source_files(source: Path) -> set[str]:
    return {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def sync_manifest(source: Path, target: Path) -> dict[str, object]:
    source = source.resolve()
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / MANIFEST_NAME
    previous: set[str] = set()
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous = {str(item) for item in payload.get("managed_files", [])}
    current = managed_source_files(source)
    removed: list[str] = []
    for relative in sorted(previous - current):
        candidate = (target / relative).resolve()
        if target not in candidate.parents:
            raise ValueError(f"Refusing to remove path outside runtime target: {candidate}")
        if candidate.is_file():
            candidate.unlink()
            removed.append(relative)
    payload = {"schema_version": 1, "managed_files": sorted(current), "removed_files": removed}
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    payload = sync_manifest(args.source, args.target)
    print(
        f"Managed runtime files: {len(payload['managed_files'])}; "
        f"retired removed: {len(payload['removed_files'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
