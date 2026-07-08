"""Path helpers for strategy research scripts.

Scripts in this package may run from the tracked ``tools`` tree or from the
synced ``user_data`` runtime tree.  Never infer the repo root from a fixed
parent depth.
"""

from __future__ import annotations

from pathlib import Path


def find_repo_root() -> Path:
    """Locate the freqtrade repo root from either tools/ or user_data/ copies."""

    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")
