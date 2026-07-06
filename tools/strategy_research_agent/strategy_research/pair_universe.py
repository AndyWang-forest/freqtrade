#!/usr/bin/env python3
"""Versioned pair universe for the local futures research agent."""

from __future__ import annotations

from dataclasses import dataclass


CORE_FUTURES_PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
RESEARCH_EXTENSION_FUTURES_PAIRS = ["SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT"]

PAIR_SCOPES = {
    "core": CORE_FUTURES_PAIRS,
    "extension": RESEARCH_EXTENSION_FUTURES_PAIRS,
    "research_all": CORE_FUTURES_PAIRS + RESEARCH_EXTENSION_FUTURES_PAIRS,
}

EXCLUDED_PAIR_CLASSES = [
    "meme coins",
    "low-liquidity altcoins",
    "new listings",
    "synthetic stock or commodity contracts",
    "unstable or non-crypto derivative contracts",
]

EXCLUDED_PAIR_TOKENS = {
    "DOGE",
    "ADA",
    "PEPE",
    "SHIB",
    "TRUMP",
    "1000",
}


@dataclass(frozen=True)
class PairUniverseIssue:
    pair: str
    reason: str


def pairs_for_scope(scope: str) -> list[str]:
    """Return futures pairs for a named research scope."""
    try:
        return list(PAIR_SCOPES[scope])
    except KeyError as exc:
        raise ValueError(f"Unknown pair scope {scope!r}; expected one of {sorted(PAIR_SCOPES)}") from exc


def pair_to_stem(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def validate_pair_universe() -> list[PairUniverseIssue]:
    """Validate that the versioned research universe stays inside its safety contract."""
    issues: list[PairUniverseIssue] = []
    seen: set[str] = set()
    for scope, pairs in PAIR_SCOPES.items():
        for pair in pairs:
            if pair in seen and scope != "research_all":
                issues.append(PairUniverseIssue(pair=pair, reason=f"duplicate pair in {scope} scope"))
            seen.add(pair)
            if not pair.endswith(":USDT"):
                issues.append(PairUniverseIssue(pair=pair, reason="pair must be a USDT-M futures symbol"))
            base = pair.split("/", 1)[0].upper()
            if base in EXCLUDED_PAIR_TOKENS or any(base.startswith(token) for token in EXCLUDED_PAIR_TOKENS):
                issues.append(PairUniverseIssue(pair=pair, reason="pair is in an excluded high-manipulation or unstable class"))
    if set(CORE_FUTURES_PAIRS) & set(RESEARCH_EXTENSION_FUTURES_PAIRS):
        issues.append(PairUniverseIssue(pair="core/extension", reason="core and extension scopes must stay disjoint"))
    return issues
