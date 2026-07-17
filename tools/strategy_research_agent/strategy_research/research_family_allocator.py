#!/usr/bin/env python3
"""Allocate research effort independently from current trade permission.

The deployment router answers what may trade now.  This allocator answers which
missing strategy family deserves the next evidence study across historical,
data-derived home regimes.  It never enables a strategy or generates code.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from family_exit_risk_contract import canonical_family_id
from factor_research_protocol import FACTOR_EVENT_METHOD_VERSION, is_current_regime_factor_report
from repo_paths import find_repo_root
from strategy_taxonomy import STRATEGY_TAXONOMY


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
POSTMORTEM_JSON = AGENT_ROOT / "postmortems/latest_research_program_postmortem.json"
ROUTER_JSON = AGENT_ROOT / "reports/latest_current_market_state_family_router.json"
MANIFEST_JSON = AGENT_ROOT / "regime_windows/latest_regime_windows.json"
REGISTRY_JSON = AGENT_ROOT / "strategy_registry.json"
RESEARCH_MEMORY_JSON = AGENT_ROOT / "research_memory/latest_research_memory.json"
FAILURE_FUNNEL_JSON = AGENT_ROOT / "failure_funnel/latest_research_failure_funnel.json"
FACTOR_REPORT_JSON = AGENT_ROOT / "factors/latest_factor_research.json"
FACTOR_EVENT_REPORT_JSON = AGENT_ROOT / "event_studies/latest_factor_candidate_event_study.json"
EVENT_STUDY_DIR = AGENT_ROOT / "event_studies"
OUTPUT_DIR = AGENT_ROOT / "research_allocation"
LATEST_JSON = OUTPUT_DIR / "latest_research_family_allocator.json"
LATEST_MD = OUTPUT_DIR / "latest_research_family_allocator.md"

HOME_LABELS = {
    "downtrend_failed_bounce_short": "bear",
    "uptrend_failed_pullback_long": "bull",
    "range_upper_reversion_short": "range",
    "range_lower_reversion_long": "range",
    "downtrend_pullback_short": "bear",
    "uptrend_pullback_long": "bull",
    "downside_breakout_continuation_short": "high_vol",
    "upside_breakout_continuation_long": "high_vol",
    "volatility_compression_directional_expansion": "high_vol",
}

# Coarse priors favor interpretable missing modules.  They are tie-breakers,
# not alpha claims and never override data readiness or saturation blocks.
INTERPRETABILITY_PRIOR = {
    "uptrend_pullback_long": 12,
    "range_lower_reversion_long": 10,
    "range_upper_reversion_short": 9,
    "upside_breakout_continuation_long": 8,
    "downtrend_pullback_short": 7,
    "downside_breakout_continuation_short": 6,
    "uptrend_failed_pullback_long": 5,
    "downtrend_failed_bounce_short": 4,
    "volatility_compression_directional_expansion": 0,
}

FAMILY_CODE_TO_ID = {
    str(details["code"]).upper(): family
    for family, details in STRATEGY_TAXONOMY.items()
}
EXPLICIT_COMPOSITE_FAMILY_ALIASES = {
    "b": {
        "range_upper_reversion_short",
        "range_lower_reversion_long",
    },
    "range_mean_reversion": {
        "range_upper_reversion_short",
        "range_lower_reversion_long",
    },
}
SAME_EVIDENCE_FAILURE_LIMIT = 3
MIN_INDEPENDENT_HOME_WINDOWS = 2
EVIDENCE_WAIT_STATUS = "evidence_saturated_wait_for_new_episode"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def allocation_target_fingerprint(
    allocation: dict[str, Any], manifest: dict[str, Any]
) -> str:
    """Hash the semantic research target and its exact home-window evidence."""

    active_names = set(allocation.get("active_home_windows") or [])
    active_windows = [
        {
            "name": item.get("name"),
            "label": item.get("label"),
            "start": item.get("start"),
            "end": item.get("end"),
            "status": item.get("status"),
            "evidence": item.get("evidence"),
        }
        for item in manifest.get("windows") or []
        if item.get("name") in active_names
    ]
    contract = {
        "action": allocation.get("action"),
        "selected_family": allocation.get("selected_family"),
        "family_code": allocation.get("family_code"),
        "regime_label": allocation.get("regime_label"),
        "allowed_sides": sorted(allocation.get("allowed_sides") or []),
        "active_home_windows": sorted(
            active_windows, key=lambda item: str(item.get("name") or "")
        ),
    }
    encoded = json.dumps(
        contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def active_windows(manifest: dict[str, Any], label: str) -> list[dict[str, Any]]:
    return [
        item
        for item in manifest.get("windows") or []
        if item.get("label") == label and item.get("status") == "active"
    ]


def deployment_permission(router: dict[str, Any]) -> dict[str, Any]:
    decisions = router.get("family_decisions") or []
    enabled_statuses = {"enabled", "active", "trade_allowed"}
    enabled = [item for item in decisions if str(item.get("status") or "").lower() in enabled_statuses]
    return {
        "source": rel(ROUTER_JSON),
        "generated_utc": router.get("generated_utc"),
        "current_state": router.get("current_state") or "unknown",
        "state_reason": router.get("state_reason") or "Current deployment state is unavailable.",
        "trade_permission": "family_event_required" if enabled else "no_trade_until_family_event_and_gate",
        "enabled_family_codes": [str(item.get("family_code")) for item in enabled],
        "family_decisions": decisions,
        "controls_research_allocation": False,
    }


def registry_counts(registry: dict[str, Any]) -> Counter[str]:
    return Counter(
        canonical_family_id(item.get("family"))
        for item in registry.get("strategies") or []
        if item.get("family")
    )


def family_postmortem(postmortem: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("strategy_family")): item
        for item in postmortem.get("family_matrix") or []
        if item.get("strategy_family")
    }


def normalized_families(value: Any) -> set[str]:
    """Return only explicit canonical families or taxonomy codes.

    Historical memory contains composite values such as ``B1|B2``.  We split
    those, but deliberately avoid fuzzy text classification here: allocator
    coverage must be auditable and cannot be inferred from incidental prose.
    """

    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(normalized_families(item))
        return result
    if not isinstance(value, str):
        return set()
    result: set[str] = set()
    for token in value.split("|"):
        raw = token.strip()
        explicit_alias = EXPLICIT_COMPOSITE_FAMILY_ALIASES.get(raw.lower())
        if explicit_alias:
            result.update(explicit_alias)
            continue
        canonical = canonical_family_id(raw)
        if canonical in STRATEGY_TAXONOMY:
            result.add(canonical)
            continue
        code_family = FAMILY_CODE_TO_ID.get(raw.upper())
        if code_family:
            result.add(code_family)
    return result


def payload_families(value: Any, *, parent_key: str = "") -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"family", "strategy_family", "families", "strategy_families"}:
                result.update(normalized_families(item))
            result.update(payload_families(item, parent_key=key))
    elif isinstance(value, list):
        for item in value:
            result.update(payload_families(item, parent_key=parent_key))
    return result


def is_negative_durable_lesson(lesson: dict[str, Any]) -> bool:
    text = " ".join(
        str(lesson.get(key) or "")
        for key in ("lesson", "memory_rule", "next_test", "status", "verdict")
    ).lower()
    markers = (
        "not robust",
        "negative gross",
        "negative expectancy",
        "failed",
        "failure",
        "no incremental edge",
        "insufficient edge",
        "do not promote",
        "close c",
        "close d",
        "reject",
    )
    return any(marker in text for marker in markers)


def factor_report_auxiliary_inputs_current(factor_report: dict[str, Any]) -> bool:
    """Return false when a report predates newly available OI metric archives."""

    for audit in factor_report.get("data_audit") or []:
        if not isinstance(audit, dict):
            continue
        for auxiliary in audit.get("auxiliary") or []:
            if not isinstance(auxiliary, dict):
                continue
            if (
                auxiliary.get("requirement") != "open_interest_metrics"
                or auxiliary.get("status") != "available"
            ):
                continue
            expected = auxiliary.get("archives")
            source_value = auxiliary.get("path")
            if expected is None or not isinstance(source_value, str) or not source_value:
                continue
            source = Path(source_value)
            if not source.is_absolute():
                source = REPO_ROOT / source
            if not source.exists() or len(list(source.glob("*.zip"))) != int(expected):
                return False
    return True


def completed_factor_failure_records(
    manifest: dict[str, Any],
    event_dir: Path = EVENT_STUDY_DIR,
) -> dict[str, list[str]]:
    """Find completed factor searches that used the current home windows.

    Scoped latest pointers survive after the generic failure funnel advances to
    another family.  A family stays paused while its last completed no-event
    search used exactly the same active data-derived home windows.  A genuine
    window change automatically reopens it.
    """

    records = {family: [] for family in STRATEGY_TAXONOMY}
    if not event_dir.exists():
        return records

    for path in sorted(event_dir.glob("latest_factor_candidate_event_study__*.json")):
        payload = load_json(path)
        summary = payload.get("summary") or {}
        if (
            summary.get("verdict") != "no_validated_factor_event"
            or int(summary.get("validated_events") or 0) > 0
        ):
            continue
        # A composition-method upgrade only reopens failures that actually had
        # gross-positive context available to compose.  Zero-gross-edge runs
        # remain valid failures because the changed composition gate could not
        # alter their outcome.
        if factor_event_method_change_reopens(payload):
            continue

        factor_report_value = payload.get("factor_report")
        if not isinstance(factor_report_value, str) or not factor_report_value:
            continue
        factor_report_path = Path(factor_report_value)
        if not factor_report_path.is_absolute():
            factor_report_path = REPO_ROOT / factor_report_path
        factor_report = load_json(factor_report_path)
        if not factor_report:
            continue
        if not is_current_regime_factor_report(factor_report):
            continue
        if not factor_report_auxiliary_inputs_current(factor_report):
            continue

        report_windows = {
            str(name)
            for name in factor_report.get("regime_windows") or []
            if name
        }
        target = payload.get("research_target") or factor_report.get("research_target") or {}
        families = normalized_families(target.get("family_codes")) | normalized_families(
            target.get("strategy_families")
        )
        report_regime = str(payload.get("regime_label") or factor_report.get("regime_label") or "")
        for family in families:
            home_label = HOME_LABELS.get(family)
            expected_windows = {
                str(item.get("name"))
                for item in active_windows(manifest, home_label or "")
                if item.get("name")
            }
            if home_label != report_regime or not expected_windows:
                continue
            if report_windows == expected_windows:
                records[family].append(rel(path))
    return records


def global_family_coverage(
    memory: dict[str, Any],
    manifest: dict[str, Any],
    event_dir: Path = EVENT_STUDY_DIR,
) -> dict[str, dict[str, Any]]:
    completed_factor_failures = completed_factor_failure_records(manifest, event_dir)
    coverage = {
        family: {
            "durable_lessons": [],
            "negative_durable_lessons": [],
            "event_artifacts": [],
            "completed_factor_failures": completed_factor_failures.get(family, []),
            "evidence_wait_controls": [],
        }
        for family in STRATEGY_TAXONOMY
    }
    for index, lesson in enumerate(memory.get("manual_lessons") or []):
        if not isinstance(lesson, dict):
            continue
        families = normalized_families(lesson.get("strategy_family") or lesson.get("family"))
        lesson_id = str(lesson.get("id") or lesson.get("source_file") or f"manual_lesson_{index}")
        for family in families:
            coverage[family]["durable_lessons"].append(lesson_id)
            if is_negative_durable_lesson(lesson):
                coverage[family]["negative_durable_lessons"].append(lesson_id)
            control = lesson.get("research_allocation_control") or {}
            if control.get("status") == EVIDENCE_WAIT_STATUS:
                coverage[family]["evidence_wait_controls"].append(
                    {
                        "lesson_id": lesson_id,
                        "status": EVIDENCE_WAIT_STATUS,
                        "active_home_windows": list(control.get("active_home_windows") or []),
                        "blocker_fingerprint": str(control.get("blocker_fingerprint") or ""),
                    }
                )

    seen_hashes: dict[str, set[str]] = {family: set() for family in STRATEGY_TAXONOMY}
    if event_dir.exists():
        for path in sorted(event_dir.glob("latest_*.json")):
            payload = load_json(path)
            if not payload:
                continue
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            for family in payload_families(payload):
                if digest in seen_hashes[family]:
                    continue
                seen_hashes[family].add(digest)
                coverage[family]["event_artifacts"].append(rel(path))
    return coverage


def current_gross_fail_families(
    funnel: dict[str, Any],
    factor_report: dict[str, Any] | None = None,
    factor_event_report: dict[str, Any] | None = None,
) -> set[str]:
    """Pause the completed target until its causal evidence changes.

    A missing or stale factor artifact is a data task, not an edge failure.  A
    current, completed gross-fail result is different: selecting the same
    family again would rerun the unchanged discovery surface.
    """

    if factor_report is not None and not is_current_regime_factor_report(factor_report):
        return set()
    decision = funnel.get("current_target_decision") or {}
    if decision.get("decision") != "stop_adjacent_variant_generation":
        return set()
    if decision.get("factor_target_stale") or int(decision.get("validated_factor_events") or 0) > 0:
        return set()
    has_current_gross_fail = any(
        str(item.get("experiment") or "").startswith("CURRENT_")
        and item.get("primary_category") == "gross_fail"
        for item in funnel.get("entries") or []
        if isinstance(item, dict)
    )
    if not has_current_gross_fail:
        return set()
    target = funnel.get("current_target") or {}
    target_families = normalized_families(target.get("family_codes")) | normalized_families(
        target.get("strategy_families")
    )
    event_target = (factor_event_report or {}).get("research_target") or {}
    event_families = normalized_families(event_target.get("family_codes")) | normalized_families(
        event_target.get("strategy_families")
    )
    if target_families == event_families and factor_event_method_change_reopens(
        factor_event_report or {}
    ):
        return set()
    return target_families


def factor_event_method_change_reopens(payload: dict[str, Any]) -> bool:
    """Return whether changed composition semantics can alter an old failure."""
    summary = payload.get("summary") or {}
    return (
        payload.get("factor_event_method_version") != FACTOR_EVENT_METHOD_VERSION
        and int(summary.get("gross_factor_candidates") or 0) > 0
    )


def active_evidence_wait_controls(
    coverage_row: dict[str, Any],
    windows: list[dict[str, Any]],
    blocker_fingerprint: str | None,
) -> list[dict[str, Any]]:
    """Keep an evidence hold active only while its frozen evidence is unchanged."""

    current_windows = {
        str(item.get("name"))
        for item in windows
        if item.get("name")
    }
    active: list[dict[str, Any]] = []
    for control in coverage_row.get("evidence_wait_controls") or []:
        frozen_windows = {
            str(name)
            for name in control.get("active_home_windows") or []
            if name
        }
        frozen_fingerprint = str(control.get("blocker_fingerprint") or "")
        windows_unchanged = bool(frozen_windows) and frozen_windows == current_windows
        blocker_unchanged = (
            blocker_fingerprint is None
            or not frozen_fingerprint
            or frozen_fingerprint == blocker_fingerprint
        )
        if windows_unchanged and blocker_unchanged:
            active.append(control)
    return active


def score_family(
    family: str,
    taxonomy: dict[str, Any],
    family_row: dict[str, Any],
    registry_count: int,
    windows: list[dict[str, Any]],
    has_long_registry: bool,
    coverage_row: dict[str, Any],
    current_gross_fail: bool = False,
    current_blocker_fingerprint: str | None = None,
) -> dict[str, Any]:
    retained_count = len(family_row.get("retained_assets") or [])
    program_experiment_count = int(family_row.get("experiments") or 0)
    durable_lesson_count = len(coverage_row.get("durable_lessons") or [])
    negative_lesson_count = len(coverage_row.get("negative_durable_lessons") or [])
    event_artifact_count = len(coverage_row.get("event_artifacts") or [])
    completed_factor_failure_count = len(coverage_row.get("completed_factor_failures") or [])
    experiment_count = max(program_experiment_count, durable_lesson_count, event_artifact_count)
    postmortem_suspended = bool(family_row.get("suspended_same_evidence"))
    global_failure_limit_reached = (
        negative_lesson_count >= SAME_EVIDENCE_FAILURE_LIMIT
        and registry_count == 0
        and retained_count == 0
    )
    suspension_sources: list[str] = []
    if postmortem_suspended:
        suspension_sources.append("program_postmortem")
    if global_failure_limit_reached:
        suspension_sources.append("global_negative_durable_lessons")
    if completed_factor_failure_count:
        suspension_sources.append("completed_factor_failure_same_windows")
    if current_gross_fail:
        suspension_sources.append("current_completed_gross_fail")
    evidence_wait_controls = active_evidence_wait_controls(
        coverage_row,
        windows,
        current_blocker_fingerprint,
    )
    if evidence_wait_controls:
        suspension_sources.append("explicit_evidence_wait_for_new_episode")
    suspended = bool(suspension_sources)
    coverage_saturated = bool(family_row.get("portfolio_coverage_saturated"))
    direction = str(taxonomy.get("direction") or "")

    components = {
        "missing_registry_family": 50 if registry_count == 0 else 0,
        "no_retained_asset": 25 if retained_count == 0 else 0,
        "active_home_window_evidence": min(15, len(windows) * 5),
        "missing_long_direction": 15 if direction == "long" and not has_long_registry else 0,
        "unexplored_family": 10 if experiment_count == 0 else 0,
        "interpretability_prior": INTERPRETABILITY_PRIOR.get(family, 0),
        "experiment_concentration_penalty": -min(30, experiment_count * 2),
        "durable_failure_pressure_penalty": -min(30, negative_lesson_count * 5),
        "covered_family_penalty": -25 if registry_count or retained_count else 0,
        "portfolio_saturation_penalty": -40 if coverage_saturated else 0,
        "same_evidence_suspension_penalty": -100 if suspended else 0,
    }
    score = sum(components.values())
    enough_independent_windows = len(windows) >= MIN_INDEPENDENT_HOME_WINDOWS
    eligible = enough_independent_windows and not suspended and not coverage_saturated
    blockers: list[str] = []
    if not windows:
        blockers.append("no_active_data_derived_home_window")
    elif not enough_independent_windows:
        blockers.append("insufficient_independent_home_windows")
    same_evidence_suspended = any(
        source != "explicit_evidence_wait_for_new_episode"
        for source in suspension_sources
    )
    if same_evidence_suspended:
        blockers.append("same_evidence_failure_limit_reached")
    if evidence_wait_controls:
        blockers.append(EVIDENCE_WAIT_STATUS)
    if coverage_saturated:
        blockers.append("portfolio_coverage_saturated")
    return {
        "strategy_family": family,
        "family_code": taxonomy["code"],
        "direction": direction,
        "home_regime_label": HOME_LABELS[family],
        "active_home_windows": [item.get("name") for item in windows],
        "registry_strategies": registry_count,
        "retained_assets": list(family_row.get("retained_assets") or []),
        "prior_experiments": experiment_count,
        "program_experiments": program_experiment_count,
        "durable_lessons": durable_lesson_count,
        "negative_durable_lessons": negative_lesson_count,
        "event_artifacts": event_artifact_count,
        "completed_factor_failures": completed_factor_failure_count,
        "coverage_evidence": coverage_row,
        "suspended_same_evidence": suspended,
        "suspension_sources": suspension_sources,
        "evidence_wait_for_new_episode": bool(evidence_wait_controls),
        "evidence_wait_sources": [
            str(control.get("lesson_id") or "")
            for control in evidence_wait_controls
        ],
        "portfolio_coverage_saturated": coverage_saturated,
        "eligible": eligible,
        "blockers": blockers,
        "score_components": components,
        "score": score,
    }


def build_payload() -> dict[str, Any]:
    postmortem = load_json(POSTMORTEM_JSON)
    router = load_json(ROUTER_JSON)
    manifest = load_json(MANIFEST_JSON)
    registry = load_json(REGISTRY_JSON)
    memory = load_json(RESEARCH_MEMORY_JSON)
    failure_funnel = load_json(FAILURE_FUNNEL_JSON)
    factor_report = load_json(FACTOR_REPORT_JSON)
    factor_event_report = load_json(FACTOR_EVENT_REPORT_JSON)
    if not postmortem:
        raise FileNotFoundError(f"Missing postmortem: {rel(POSTMORTEM_JSON)}")
    if not manifest:
        raise FileNotFoundError(f"Missing regime manifest: {rel(MANIFEST_JSON)}")

    counts = registry_counts(registry)
    postmortem_by_family = family_postmortem(postmortem)
    global_coverage = global_family_coverage(memory, manifest)
    current_gross_fail = current_gross_fail_families(
        failure_funnel,
        factor_report,
        factor_event_report,
    )
    current_blocker_fingerprint = str(
        (failure_funnel.get("current_target_decision") or {}).get("blocker_fingerprint") or ""
    )
    current_blocker_families = normalized_families(
        (failure_funnel.get("current_target") or {}).get("family_codes")
    ) | normalized_families(
        (failure_funnel.get("current_target") or {}).get("strategy_families")
    )
    has_long_registry = any(
        STRATEGY_TAXONOMY.get(family, {}).get("direction") == "long" and count > 0
        for family, count in counts.items()
    )
    candidates: list[dict[str, Any]] = []
    for family, taxonomy in STRATEGY_TAXONOMY.items():
        if family == "defense_no_trade":
            continue
        label = HOME_LABELS[family]
        candidates.append(
            score_family(
                family,
                taxonomy,
                postmortem_by_family.get(family, {}),
                counts.get(family, 0),
                active_windows(manifest, label),
                has_long_registry,
                global_coverage.get(family, {}),
                current_gross_fail=family in current_gross_fail,
                current_blocker_fingerprint=(
                    current_blocker_fingerprint
                    if family in current_blocker_families
                    else None
                ),
            )
        )
    candidates.sort(key=lambda item: (-int(item["eligible"]), -item["score"], item["family_code"]))
    selected = next((item for item in candidates if item["eligible"]), None)
    if selected is None:
        allocation = {
            "action": "no_research_allocation",
            "selected_family": None,
            "reason": "No unsuspended family has enough independent data-derived home windows.",
            "strategy_synthesis_allowed": False,
        }
    else:
        allocation = {
            "action": "research",
            "selected_family": selected["strategy_family"],
            "family_code": selected["family_code"],
            "regime_label": selected["home_regime_label"],
            "allowed_sides": [selected["direction"]] if selected["direction"] in {"long", "short"} else ["long", "short"],
            "active_home_windows": selected["active_home_windows"],
            "score": selected["score"],
            "reason": (
                f"{selected['family_code']} has the highest adjusted research priority after global historical coverage, "
                f"with a coverage floor of {selected['prior_experiments']} evidence branches and "
                f"{len(selected['active_home_windows'])} active data-derived {selected['home_regime_label']} windows."
            ),
            "strategy_synthesis_allowed": False,
            "next_stage": "factor_and_event_research_only",
        }
    payload = {
        "generated_at_utc": now_utc(),
        "schema_version": 7,
        "research_only": True,
        "deployment_permission": deployment_permission(router),
        "research_allocation": allocation,
        "candidate_ranking": candidates,
        "separation_contract": {
            "deployment_router_controls_current_trade_permission": True,
            "research_allocator_controls_historical_research_family": True,
            "research_allocation_never_enables_trading": True,
            "no_trade_is_valid_deployment_output": True,
            "data_blocked_is_not_edge_failure": True,
            "same_family_same_evidence_failure_limit": SAME_EVIDENCE_FAILURE_LIMIT,
            "minimum_independent_home_windows": MIN_INDEPENDENT_HOME_WINDOWS,
            "explicit_evidence_wait_reopens_on_window_or_blocker_change": True,
        },
        "frozen_or_waiting_branches": {
            "frozen_research_assets": ["E1", "E33"],
            "wait_for_new_prospective_data": ["E23", "E32"],
            "must_not_be_retested_unchanged": True,
        },
        "source_artifacts": {
            "postmortem": rel(POSTMORTEM_JSON),
            "deployment_router": rel(ROUTER_JSON),
            "regime_manifest": rel(MANIFEST_JSON),
            "strategy_registry": rel(REGISTRY_JSON),
            "research_memory": rel(RESEARCH_MEMORY_JSON),
            "research_failure_funnel": rel(FAILURE_FUNNEL_JSON),
            "event_studies": rel(EVENT_STUDY_DIR),
        },
    }
    payload["target_fingerprint"] = allocation_target_fingerprint(allocation, manifest)
    return payload


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    deployment = payload["deployment_permission"]
    allocation = payload["research_allocation"]
    lines = [
        "# Research Family Allocator",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Current deployment state: `{deployment['current_state']}`",
        f"- Current trade permission: `{deployment['trade_permission']}`",
        f"- Research allocation: `{allocation.get('family_code') or 'none'}` / `{allocation.get('selected_family') or 'none'}`",
        f"- Historical home regime: `{allocation.get('regime_label') or 'none'}`",
        "- Research allocation never enables trading and does not authorize strategy synthesis.",
        "",
        "## Why These Are Separate",
        "",
        deployment["state_reason"],
        "",
        allocation["reason"],
        "",
        "## Candidate Ranking",
        "",
        "| Rank | Code | Family | Home | Windows | Registry | Retained | Coverage | Program | Lessons | Events | Completed fail | Score | Eligible | State |",
        "|---:|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for index, item in enumerate(payload["candidate_ranking"], start=1):
        state = (
            "wait_for_new_episode"
            if item["evidence_wait_for_new_episode"]
            else (
                "suspended:" + ",".join(item["suspension_sources"])
                if item["suspended_same_evidence"]
                else ("coverage_saturated" if item["portfolio_coverage_saturated"] else "open")
            )
        )
        lines.append(
            f"| {index} | {item['family_code']} | {item['strategy_family']} | {item['home_regime_label']} | "
            f"{len(item['active_home_windows'])} | {item['registry_strategies']} | {', '.join(item['retained_assets']) or '-'} | "
            f"{item['prior_experiments']} | {item['program_experiments']} | {item['durable_lessons']} | "
            f"{item['event_artifacts']} | {item['completed_factor_failures']} | {item['score']} | {item['eligible']} | {state} |"
        )
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            "Run only factor/event evidence discovery for the selected family in the listed data-derived home windows. Do not generate a strategy class until a current validated event passes the existing evidence chain.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(payload: dict[str, Any]) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = payload["generated_at_utc"]
    json_path = OUTPUT_DIR / f"research_family_allocator_{timestamp}.json"
    md_path = OUTPUT_DIR / f"research_family_allocator_{timestamp}.md"
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    json_path.write_text(rendered, encoding="utf-8")
    LATEST_JSON.write_text(rendered, encoding="utf-8")
    write_markdown(md_path, payload)
    LATEST_MD.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    payload = build_payload()
    json_path, md_path = write_outputs(payload)
    allocation = payload["research_allocation"]
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Research allocation: {allocation.get('family_code') or 'none'} ({allocation.get('regime_label') or 'none'})")
    print("Strategy synthesis: not authorized by the allocator; the failure funnel controls the next evidence stage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
