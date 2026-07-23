#!/usr/bin/env python3
"""Enforce event evidence and a three-variant budget per mechanism."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
OUTPUT_DIR = AGENT_ROOT / "mechanism_variants"
LEDGER_PATH = OUTPUT_DIR / "mechanism_variant_ledger.json"
LATEST_JSON = OUTPUT_DIR / "latest_mechanism_variant_policy.json"
LATEST_MD = OUTPUT_DIR / "latest_mechanism_variant_policy.md"
MAX_STRUCTURAL_VARIANTS = 3


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def mechanism_descriptor(item: dict[str, Any]) -> dict[str, Any]:
    definition = item.get("event_definition") or {}
    return {
        "strategy_family_codes": sorted(item.get("strategy_family_codes") or []),
        "regime_label": item.get("regime_label"),
        "side": item.get("side"),
        "timeframe": item.get("timeframe"),
        "structural_event": definition.get("structural_event"),
        "factor_domain": definition.get("domain"),
        "factor": definition.get("factor"),
        "factor_tail": definition.get("tail"),
        "data_requirement": definition.get("data_requirement"),
        "evidence_generation": evidence_generation(item),
    }


def evidence_generation(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in item.get("independent_window_evidence") or []:
        window = str(row.get("window") or "").strip()
        if not window:
            continue
        rows.append(
            {
                "window": window,
                "role": row.get("role"),
                "sample": int(row.get("sample") or 0),
            }
        )
    return sorted(rows, key=lambda row: (row["window"], str(row["role"])))


def mechanism_fingerprint(item: dict[str, Any]) -> str:
    encoded = json.dumps(
        mechanism_descriptor(item), sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def positive_window_count(item: dict[str, Any]) -> int:
    return len(
        {
            str(row.get("window"))
            for row in item.get("independent_window_evidence") or []
            if row.get("window")
            and bool(row.get("net_positive"))
            and float(row.get("mean_after_fee_pct") or 0.0) > 0.0
        }
    )


def validated_event_blockers(item: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    definition = item.get("event_definition") or {}
    gross = item.get("gross_edge") or {}
    cost = item.get("realistic_cost") or {}
    execution = item.get("execution_compatibility") or {}
    if item.get("strategy_generation_allowed") is not True:
        blockers.append("event_did_not_authorize_strategy_generation")
    if item.get("event_kind") != "family_factor_composite":
        blockers.append("not_a_family_factor_composite")
    if item.get("status") != "family_composite_edge_candidate":
        blockers.append("composite_status_not_validated")
    if not definition.get("structural_event"):
        blockers.append("missing_predeclared_structural_event")
    if gross.get("gate") != "pass" or int(gross.get("independent_events") or 0) <= 0:
        blockers.append("gross_edge_gate_failed")
    if cost.get("gate") != "pass" or float(cost.get("mean_after_fee_pct") or 0.0) <= 0.0:
        blockers.append("realistic_cost_gate_failed")
    if positive_window_count(item) < 2:
        blockers.append("fewer_than_two_positive_home_regime_windows")
    if execution.get("compatible") is not True:
        blockers.append("causal_freqtrade_runtime_path_missing")
    return blockers


def empty_ledger() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "research_only": True,
        "max_structural_variants_per_mechanism": MAX_STRUCTURAL_VARIANTS,
        "mechanisms": {},
    }


def load_ledger() -> dict[str, Any]:
    ledger = load_json(LEDGER_PATH) or empty_ledger()
    ledger.setdefault("mechanisms", {})
    return ledger


def mechanism_state(
    fingerprint: str, ledger: dict[str, Any] | None = None
) -> dict[str, Any]:
    ledger = ledger or load_ledger()
    item = (ledger.get("mechanisms") or {}).get(fingerprint) or {}
    variants = item.get("variants") or []
    failures = [row for row in variants if row.get("outcome") == "failed"]
    quarantined = len(failures) >= MAX_STRUCTURAL_VARIANTS
    budget_used = len({str(row.get("variant_id")) for row in variants if row.get("variant_id")})
    return {
        "fingerprint": fingerprint,
        "budget_max": MAX_STRUCTURAL_VARIANTS,
        "budget_used": budget_used,
        "budget_remaining": max(0, MAX_STRUCTURAL_VARIANTS - budget_used),
        "failed_variants": len(failures),
        "quarantined": quarantined,
        "requires_new_evidence_to_reopen": quarantined,
    }


def register_variant(
    event: dict[str, Any], variant_id: str, *, source_event_id: str | None = None
) -> dict[str, Any]:
    blockers = validated_event_blockers(event)
    if blockers:
        raise ValueError("event evidence gate failed: " + ", ".join(blockers))
    fingerprint = mechanism_fingerprint(event)
    ledger = load_ledger()
    state = mechanism_state(fingerprint, ledger)
    mechanisms = ledger["mechanisms"]
    record = mechanisms.setdefault(
        fingerprint,
        {
            "descriptor": mechanism_descriptor(event),
            "source_event_ids": [],
            "variants": [],
        },
    )
    if source_event_id and source_event_id not in record["source_event_ids"]:
        record["source_event_ids"].append(source_event_id)
    existing = next(
        (row for row in record["variants"] if row.get("variant_id") == variant_id),
        None,
    )
    if existing is None:
        if state["budget_remaining"] <= 0:
            raise ValueError(
                f"mechanism {fingerprint} exhausted its {MAX_STRUCTURAL_VARIANTS}-variant budget"
            )
        record["variants"].append(
            {
                "variant_id": variant_id,
                "registered_at_utc": now_utc(),
                "outcome": "pending",
                "source_event_id": source_event_id,
            }
        )
    ledger["updated_at_utc"] = now_utc()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_status(ledger)
    return mechanism_state(fingerprint, ledger)


def record_outcome(fingerprint: str, variant_id: str, outcome: str) -> dict[str, Any]:
    if outcome not in {"failed", "passed", "blocked"}:
        raise ValueError(f"unsupported outcome: {outcome}")
    ledger = load_ledger()
    mechanism = (ledger.get("mechanisms") or {}).get(fingerprint)
    if not mechanism:
        raise ValueError(f"unknown mechanism: {fingerprint}")
    variant = next(
        (row for row in mechanism.get("variants") or [] if row.get("variant_id") == variant_id),
        None,
    )
    if variant is None:
        raise ValueError(f"unknown variant {variant_id} for mechanism {fingerprint}")
    variant["outcome"] = outcome
    variant["outcome_recorded_at_utc"] = now_utc()
    ledger["updated_at_utc"] = now_utc()
    LEDGER_PATH.write_text(
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_status(ledger)
    return mechanism_state(fingerprint, ledger)


def build_status(ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    ledger = ledger or load_ledger()
    states = [
        mechanism_state(fingerprint, ledger)
        for fingerprint in sorted((ledger.get("mechanisms") or {}).keys())
    ]
    return {
        "generated_at_utc": now_utc(),
        "research_only": True,
        "max_structural_variants_per_mechanism": MAX_STRUCTURAL_VARIANTS,
        "mechanisms": states,
        "quarantined_mechanisms": [
            row["fingerprint"] for row in states if row["quarantined"]
        ],
        "rule": "At most three structural variants may be evaluated for one unchanged mechanism. Three failed variants quarantine the mechanism until genuinely new evidence changes its fingerprint.",
    }


def write_status(ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = build_status(ledger)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# Mechanism Variant Policy",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Max structural variants per mechanism: `{MAX_STRUCTURAL_VARIANTS}`",
        f"- Quarantined mechanisms: `{len(payload['quarantined_mechanisms'])}`",
        "",
        "| Mechanism | Used | Remaining | Failed | Quarantined |",
        "|---|---:|---:|---:|---|",
    ]
    for row in payload["mechanisms"]:
        lines.append(
            f"| `{row['fingerprint']}` | {row['budget_used']} | {row['budget_remaining']} | "
            f"{row['failed_variants']} | {row['quarantined']} |"
        )
    if not payload["mechanisms"]:
        lines.append("| none | 0 | 3 | 0 | False |")
    LATEST_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-outcome", nargs=3, metavar=("FINGERPRINT", "VARIANT", "OUTCOME"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.record_outcome:
        fingerprint, variant_id, outcome = args.record_outcome
        print(json.dumps(record_outcome(fingerprint, variant_id, outcome), indent=2))
    else:
        payload = write_status()
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
