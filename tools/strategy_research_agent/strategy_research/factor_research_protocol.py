"""Versioned contract for regime-targeted factor discovery evidence."""

from __future__ import annotations

from typing import Any


FACTOR_RESEARCH_METHOD_VERSION = 3
FACTOR_EVENT_METHOD_VERSION = 5
REGIME_THRESHOLD_SOURCE = "earliest_home_episode"
FAMILY_COMPOSITE_REQUIRED = True
GROSS_FACTOR_COMPOSITION_ALLOWED = True


def is_current_regime_factor_report(payload: dict[str, Any]) -> bool:
    """Return whether a targeted report used the current train/validation split."""

    protocol = payload.get("threshold_protocol") or {}
    return (
        payload.get("factor_research_method_version") == FACTOR_RESEARCH_METHOD_VERSION
        and protocol.get("threshold_source") == REGIME_THRESHOLD_SOURCE
        and bool(protocol.get("development_window"))
    )
