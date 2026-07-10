#!/usr/bin/env python3
"""Canonical family-level exit-risk contract for strategy research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PEAK_EXIT_PRESETS: dict[str, dict[str, float] | None] = {
    "off": None,
    "peak40": {"activation_profit": 0.40, "giveback": 0.40},
    "peak60_giveback40": {"activation_profit": 0.60, "giveback": 0.40},
}
FAMILY_ALIASES = {
    "volatility_compression_directional_expansion": "volatility_compression_breakout",
}
CANONICAL_FAMILIES = {
    "downtrend_failed_bounce_short",
    "uptrend_failed_pullback_long",
    "range_upper_reversion_short",
    "range_lower_reversion_long",
    "downtrend_pullback_short",
    "uptrend_pullback_long",
    "downside_breakout_continuation_short",
    "upside_breakout_continuation_long",
    "volatility_compression_breakout",
    "defense_no_trade",
}


@dataclass(frozen=True)
class PeakModeValidation:
    family: str
    mode: str
    allowed: bool
    detail: str


def canonical_family_id(family: str | None) -> str:
    raw = str(family or "").strip()
    return FAMILY_ALIASES.get(raw, raw)


def family_exit_contract(family: str | None) -> dict[str, Any]:
    canonical = canonical_family_id(family)
    contract: dict[str, Any] = {
        "strategy_family": canonical,
        "default_peak_mode": "off",
        "promotion_allowed_peak_modes": ["off"],
        "research_only_peak_modes": ["peak40", "peak60_giveback40"],
        "unchanged_entry_ab_required": True,
        "rule": (
            "Peak protection is off by default. A family may promote a Peak preset only after "
            "unchanged-entry A/B evidence and an explicit tracked contract update."
        ),
    }
    if canonical == "downtrend_failed_bounce_short":
        contract.update(
            {
                "promotion_allowed_peak_modes": ["off", "peak40"],
                "research_only_peak_modes": ["peak60_giveback40"],
                "validated_evidence": "A1 unchanged-entry comparison validated peak40 as an optional family exit.",
            }
        )
    elif canonical == "volatility_compression_breakout":
        contract.update(
            {
                "research_only_peak_modes": [],
                "rule": (
                    "E-family directional expansion must keep Peak protection off. Peak40 truncated "
                    "the expansion tail and is blocked from promotion and dry-run review."
                ),
            }
        )
    elif canonical == "defense_no_trade":
        contract.update({"research_only_peak_modes": [], "unchanged_entry_ab_required": False})
    return contract


def peak_mode_from_values(activation_profit: Any, giveback: Any) -> str:
    if activation_profit is None and giveback is None:
        return "off"
    try:
        activation = float(activation_profit)
        drawdown = float(giveback)
    except (TypeError, ValueError):
        return "custom_or_invalid"
    for mode, preset in PEAK_EXIT_PRESETS.items():
        if preset is None:
            continue
        if abs(activation - preset["activation_profit"]) < 1e-12 and abs(drawdown - preset["giveback"]) < 1e-12:
            return mode
    return "custom_or_invalid"


def strategy_peak_mode(strategy: Any) -> str:
    return peak_mode_from_values(
        getattr(strategy, "peak_drawdown_activation_profit", None),
        getattr(strategy, "peak_drawdown_giveback", None),
    )


def validate_promotion_peak_mode(family: str | None, mode: str) -> PeakModeValidation:
    canonical = canonical_family_id(family)
    if not canonical:
        return PeakModeValidation(canonical, mode, False, "strategy_family is missing")
    if canonical not in CANONICAL_FAMILIES:
        return PeakModeValidation(canonical, mode, False, "unknown strategy family has no exit-risk contract")
    contract = family_exit_contract(canonical)
    allowed = mode in contract["promotion_allowed_peak_modes"]
    detail = f"{canonical} permits promotion mode {mode}" if allowed else contract["rule"]
    return PeakModeValidation(canonical, mode, allowed, detail)


def validate_contract_table() -> list[str]:
    issues: list[str] = []
    for family in sorted(CANONICAL_FAMILIES):
        contract = family_exit_contract(family)
        if contract.get("default_peak_mode") != "off":
            issues.append(f"{family}: default Peak mode must be off")
        if "off" not in contract.get("promotion_allowed_peak_modes", []):
            issues.append(f"{family}: off must remain promotion-allowed")
    if "peak40" not in family_exit_contract("downtrend_failed_bounce_short")["promotion_allowed_peak_modes"]:
        issues.append("A1: validated peak40 mode is missing")
    if family_exit_contract("volatility_compression_breakout")["promotion_allowed_peak_modes"] != ["off"]:
        issues.append("E: Peak protection must be blocked for promotion")
    return issues
