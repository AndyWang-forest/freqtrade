#!/usr/bin/env python3
"""Resolve deployment permission and research allocation without conflating them."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
ROUTER_JSON = (
    REPO_ROOT
    / "user_data/strategy_research/reports/latest_current_market_state_family_router.json"
)
ALLOCATION_JSON = (
    REPO_ROOT
    / "user_data/strategy_research/research_allocation/latest_research_family_allocator.json"
)


@dataclass(frozen=True)
class ResearchTarget:
    current_state: str
    action: str
    regime_label: str | None
    family_codes: tuple[str, ...]
    strategy_families: tuple[str, ...]
    allowed_sides: tuple[str, ...]
    reason: str
    source_generated_utc: str | None
    source_path: str
    target_fingerprint: str | None
    selection_source: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("family_codes", "strategy_families", "allowed_sides"):
            payload[key] = list(payload[key])
        return payload


STATE_TARGETS: dict[str, dict[str, Any]] = {
    "bull_trend": {
        "action": "research",
        "regime_label": "bull",
        "family_codes": ("A2", "C2", "D2"),
        "strategy_families": (
            "uptrend_failed_pullback_long",
            "uptrend_pullback_long",
            "upside_breakout_continuation_long",
        ),
        "allowed_sides": ("long",),
    },
    "bear_continuation": {
        "action": "research",
        "regime_label": "bear",
        "family_codes": ("A1", "C1", "D1"),
        "strategy_families": (
            "downtrend_failed_bounce_short",
            "downtrend_pullback_short",
            "downside_breakout_continuation_short",
        ),
        "allowed_sides": ("short",),
    },
    "range_or_compression": {
        "action": "research",
        "regime_label": "range",
        "family_codes": ("B", "E"),
        "strategy_families": (
            "range_mean_reversion",
            "volatility_compression_directional_expansion",
        ),
        "allowed_sides": ("long", "short"),
    },
    "high_vol_mixed": {
        "action": "research",
        "regime_label": "high_vol",
        "family_codes": ("D1", "D2", "E"),
        "strategy_families": (
            "downside_breakout_continuation_short",
            "upside_breakout_continuation_long",
            "volatility_compression_directional_expansion",
        ),
        "allowed_sides": ("long", "short"),
    },
    "bear_relief_or_mixed": {
        "action": "no_trade",
        "regime_label": None,
        "family_codes": ("F",),
        "strategy_families": ("defense_no_trade",),
        "allowed_sides": (),
    },
    "mixed_unknown": {
        "action": "no_trade",
        "regime_label": None,
        "family_codes": ("F",),
        "strategy_families": ("defense_no_trade",),
        "allowed_sides": (),
    },
}


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def resolve_target_from_payload(payload: dict[str, Any]) -> ResearchTarget:
    """Resolve the current deployment router.

    This remains available for trade-permission reporting. Normal factor/event
    research must load the independent
    research-family allocator through ``load_current_research_target``.
    """
    state = str(payload.get("current_state") or "mixed_unknown")
    contract = STATE_TARGETS.get(state, STATE_TARGETS["mixed_unknown"])
    reason = str(payload.get("state_reason") or "Router state has no supported research mapping.")
    if contract["action"] == "no_trade":
        reason = f"{reason} Automatic discovery is blocked until a supported market state appears."
    return ResearchTarget(
        current_state=state,
        action=str(contract["action"]),
        regime_label=contract["regime_label"],
        family_codes=tuple(contract["family_codes"]),
        strategy_families=tuple(contract["strategy_families"]),
        allowed_sides=tuple(contract["allowed_sides"]),
        reason=reason,
        source_generated_utc=payload.get("generated_utc"),
        source_path=rel(ROUTER_JSON),
        target_fingerprint=None,
        selection_source="deployment_router_fallback",
    )


def load_current_deployment_target(path: Path = ROUTER_JSON) -> ResearchTarget:
    if not path.exists():
        raise FileNotFoundError(f"Missing current-market router output: {rel(path)}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return resolve_target_from_payload(payload)


def resolve_target_from_allocation(payload: dict[str, Any]) -> ResearchTarget:
    allocation = payload.get("research_allocation") or {}
    deployment = payload.get("deployment_permission") or {}
    action = str(allocation.get("action") or "no_research_allocation")
    family = allocation.get("selected_family")
    family_code = allocation.get("family_code")
    if action != "research" or not family or not family_code:
        return ResearchTarget(
            current_state=str(deployment.get("current_state") or "unknown"),
            action="no_trade",
            regime_label=None,
            family_codes=("F",),
            strategy_families=("defense_no_trade",),
            allowed_sides=(),
            reason=str(allocation.get("reason") or "No research family is currently eligible."),
            source_generated_utc=payload.get("generated_at_utc"),
            source_path=rel(ALLOCATION_JSON),
            target_fingerprint=payload.get("target_fingerprint"),
            selection_source="research_family_allocator",
        )
    return ResearchTarget(
        current_state=str(deployment.get("current_state") or "unknown"),
        action="research",
        regime_label=str(allocation.get("regime_label") or "") or None,
        family_codes=(str(family_code),),
        strategy_families=(str(family),),
        allowed_sides=tuple(str(item) for item in allocation.get("allowed_sides") or []),
        reason=str(allocation.get("reason") or "Independent research-family allocation."),
        source_generated_utc=payload.get("generated_at_utc"),
        source_path=rel(ALLOCATION_JSON),
        target_fingerprint=payload.get("target_fingerprint"),
        selection_source="research_family_allocator",
    )


def target_mismatch_reason(
    payload: dict[str, Any], target: ResearchTarget
) -> str | None:
    """Return a fail-closed reason when evidence belongs to another target."""

    if not target.target_fingerprint:
        return "Current independent allocator target has no target fingerprint."
    if payload.get("selection_source") != target.selection_source:
        return "Evidence selection source does not match the current allocator."
    if payload.get("target_fingerprint") != target.target_fingerprint:
        return "Evidence target fingerprint does not match the current allocator."
    if payload.get("action") != target.action:
        return "Evidence action does not match the current allocator."
    if (payload.get("regime_label") or None) != target.regime_label:
        return "Evidence regime does not match the current allocator."
    if tuple(sorted(payload.get("family_codes") or [])) != tuple(
        sorted(target.family_codes)
    ):
        return "Evidence family codes do not match the current allocator."
    if tuple(sorted(payload.get("allowed_sides") or [])) != tuple(
        sorted(target.allowed_sides)
    ):
        return "Evidence allowed sides do not match the current allocator."
    return None


def load_current_research_target(path: Path = ALLOCATION_JSON) -> ResearchTarget:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing independent research allocator output: {rel(path)}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return resolve_target_from_allocation(payload)


def main() -> int:
    print(json.dumps(load_current_research_target().as_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
