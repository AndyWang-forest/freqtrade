from __future__ import annotations

import sys
from pathlib import Path

import pytest


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from safe_gh_write import build_gh_command, validate_repository  # noqa: E402


def test_official_freqtrade_repository_is_rejected() -> None:
    with pytest.raises(ValueError, match="read-only"):
        validate_repository("freqtrade/freqtrade")


def test_personal_repository_is_allowed_and_bound_to_command() -> None:
    command, environment = build_gh_command(
        "AndyWang-forest/freqtrade",
        ["pr", "create", "--title", "test"],
    )

    assert command == ["gh", "pr", "create", "--title", "test"]
    assert environment["GH_REPO"] == "AndyWang-forest/freqtrade"


def test_explicit_mismatched_repo_argument_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not match"):
        build_gh_command(
            "AndyWang-forest/freqtrade",
            ["pr", "create", "--repo", "freqtrade/freqtrade"],
        )


def test_official_repository_positional_argument_is_rejected() -> None:
    with pytest.raises(ValueError, match="read-only"):
        build_gh_command(
            "AndyWang-forest/freqtrade",
            ["repo", "edit", "https://github.com/freqtrade/freqtrade"],
        )
