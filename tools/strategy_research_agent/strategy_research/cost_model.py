"""Shared cost scenarios for Freqtrade strategy research.

The main edge screen should use a realistic futures trading-cost estimate.
Stress scenarios are safety checks, not the primary filter for deciding whether
a signal has edge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee: float
    slippage_bps: float
    role: str
    description: str


REALISTIC_SCENARIO = CostScenario(
    name="realistic_fee_5bps",
    fee=0.0005,
    slippage_bps=5.0,
    role="primary_edge",
    description=(
        "Primary edge screen for liquid Binance USDT-M futures: 5bps "
        "single-side fee plus 5bps notional slippage adjustment."
    ),
)

STRESS_SCENARIO = CostScenario(
    name="stress_fee_20bps",
    fee=0.0020,
    slippage_bps=20.0,
    role="stress",
    description=(
        "Stress test only: 20bps single-side fee plus 20bps notional "
        "slippage/funding buffer."
    ),
)

LEGACY_HIGH_SCENARIO = CostScenario(
    name="high_fee_12bps",
    fee=0.0015,
    slippage_bps=12.0,
    role="legacy_conservative",
    description=(
        "Legacy conservative research screen. Kept for historical reports and "
        "old CSV compatibility; do not use as the primary filter for new work."
    ),
)

DEFAULT_SCENARIOS: list[tuple[str, float, float]] = [
    (REALISTIC_SCENARIO.name, REALISTIC_SCENARIO.fee, REALISTIC_SCENARIO.slippage_bps),
    (STRESS_SCENARIO.name, STRESS_SCENARIO.fee, STRESS_SCENARIO.slippage_bps),
]

PRIMARY_SCENARIO = REALISTIC_SCENARIO.name
STRESS_SCENARIO_NAME = STRESS_SCENARIO.name
LEGACY_PRIMARY_SCENARIOS = {LEGACY_HIGH_SCENARIO.name}


def is_primary_scenario(name: str | None) -> bool:
    """Return true for the current primary scenario, with old CSV fallback."""

    if not name:
        return True
    return name == PRIMARY_SCENARIO or name in LEGACY_PRIMARY_SCENARIOS


def is_stress_scenario(name: str | None) -> bool:
    """Return true only for the current stress safety scenario."""

    return name == STRESS_SCENARIO_NAME


def scenario_label(name: str | None) -> str:
    if not name:
        return PRIMARY_SCENARIO
    return name


def margin_cost_pct(fee: float, slippage_bps: float, leverage: float = 50.0) -> float:
    """Convert round-trip trading friction to account-return percentage points."""

    return leverage * ((2 * fee) + slippage_bps / 10000.0) * 100.0
