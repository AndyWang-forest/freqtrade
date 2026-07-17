#!/usr/bin/env python3
"""Run GitHub CLI writes against one explicitly bound non-upstream repository."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


OFFICIAL_REPOSITORY = "freqtrade/freqtrade"


def normalize_repository(repository: str) -> str:
    normalized = repository.strip().rstrip("/")
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    )
    for prefix in prefixes:
        if normalized.lower().startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]
    return normalized.strip("/")


def validate_repository(repository: str) -> str:
    normalized = normalize_repository(repository)
    parts = normalized.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected GitHub repository in owner/name form, got {repository!r}.")
    if normalized.lower() == OFFICIAL_REPOSITORY:
        raise ValueError(
            "The official freqtrade/freqtrade repository is read-only for this Agent."
        )
    return normalized


def explicit_repositories(arguments: Sequence[str]) -> list[str]:
    repositories: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in {"--repo", "-R"}:
            if index + 1 >= len(arguments):
                raise ValueError(f"{token} requires a repository value.")
            repositories.append(normalize_repository(arguments[index + 1]))
            index += 2
            continue
        if token.startswith("--repo=") or token.startswith("-R="):
            repositories.append(normalize_repository(token.split("=", 1)[1]))
        elif token.startswith("-R") and len(token) > 2:
            repositories.append(normalize_repository(token[2:]))
        index += 1
    return repositories


def argument_targets_official_repository(argument: str) -> bool:
    normalized = normalize_repository(argument)
    return normalized.lower() == OFFICIAL_REPOSITORY


def build_gh_command(
    repository: str,
    arguments: Sequence[str],
) -> tuple[list[str], dict[str, str]]:
    target = validate_repository(repository)
    command_arguments = list(arguments)
    if not command_arguments:
        raise ValueError("A GitHub CLI command is required.")
    if command_arguments[0] == "api":
        raise ValueError(
            "Direct gh api calls are not allowed by the safe write wrapper because "
            "they can bypass repository binding."
        )
    for explicit in explicit_repositories(command_arguments):
        if explicit.lower() != target.lower():
            raise ValueError(
                f"Explicit repository {explicit!r} does not match bound target {target!r}."
            )
    if any(argument_targets_official_repository(item) for item in command_arguments):
        raise ValueError(
            "The official freqtrade/freqtrade repository is read-only for this Agent."
        )
    environment = os.environ.copy()
    environment["GH_REPO"] = target
    return ["gh", *command_arguments], environment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="Writable owner/name repository.")
    parser.add_argument("--dry-run", action="store_true", help="Print the bound command only.")
    parser.add_argument("gh_arguments", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command_arguments = list(args.gh_arguments)
    if command_arguments[:1] == ["--"]:
        command_arguments = command_arguments[1:]
    try:
        command, environment = build_gh_command(args.repository, command_arguments)
    except ValueError as exc:
        print(f"safe_gh_write: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            json.dumps(
                {"repository": environment["GH_REPO"], "command": command},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
