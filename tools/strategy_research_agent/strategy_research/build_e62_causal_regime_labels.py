#!/usr/bin/env python3
"""Build next-day-effective daily regime labels for the blind E62 sample gate."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from regime_window_builder import (
    combine_daily_feature_frames,
    daily_features_from_hourly,
    labels_daily,
    read_ohlcv,
    resample_to_1h,
)
from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
RESEARCH_ROOT = REPO_ROOT / "user_data/strategy_research"
DATA_DIR = REPO_ROOT / "user_data/data/binance/futures"
OUTPUT_DIR = RESEARCH_ROOT / "regime_windows"
PREREG_ROOT = RESEARCH_ROOT / "preregistrations"
PAIRS = ("BTC_USDT_USDT", "ETH_USDT_USDT")
REQUIRED_FEATURES = (
    "combined_ret_30d",
    "combined_ret_60d",
    "btc_ret_60d",
    "eth_ret_60d",
    "combined_ema_gap",
    "combined_vol_pctile",
    "combined_atr_pctile",
    "combined_trend_efficiency",
    "direction_agreement_60d",
)
AMENDMENT_VERSION = 3
AMENDMENT_GLOB = f"e62_pre_unblind_protocol_amendment_v{AMENDMENT_VERSION}_*.json"
PREDECESSOR_GLOB = f"e62_pre_unblind_protocol_amendment_v{AMENDMENT_VERSION - 1}_*.json"


def utc_tag() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_preregistration() -> tuple[Path, dict[str, Any]]:
    paths = sorted(PREREG_ROOT.glob("e62_direct_force_order_exhaustion_continuation_v1_*.json"))
    if not paths:
        raise FileNotFoundError("E62 frozen preregistration is missing")
    path = paths[-1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != "E62":
        raise ValueError(f"unexpected experiment in {relative(path)}")
    if payload.get("status") != "frozen_before_prospective_events":
        raise ValueError(f"E62 preregistration is not frozen: {relative(path)}")
    return path, payload


def amendment_source_paths() -> dict[str, Path]:
    runtime_dir = Path(__file__).resolve().parent
    return {
        "e62_causal_label_builder_sha256": Path(__file__).resolve(),
        "e62_pre_unblind_auditor_sha256": runtime_dir / "audit_e62_pre_unblind.py",
        "e62_ohlcv_tail_refresher_sha256": runtime_dir / "refresh_binance_um_ohlcv_tail.py",
        "regime_classifier_sha256": runtime_dir / "regime_window_builder.py",
    }


def verify_predecessor(payload: dict[str, Any]) -> None:
    predecessor_value = payload.get("supersedes_amendment")
    predecessor_hash = payload.get("supersedes_amendment_sha256")
    if not isinstance(predecessor_value, str) or not isinstance(predecessor_hash, str):
        raise ValueError("E62 amendment does not bind its predecessor")
    predecessor_path = REPO_ROOT / predecessor_value
    if not predecessor_path.exists() or sha256(predecessor_path) != predecessor_hash:
        raise ValueError("E62 predecessor amendment hash changed")


def verify_amendment(
    path: Path,
    prereg_path: Path,
    prereg: dict[str, Any],
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != "E62":
        raise ValueError(f"unexpected experiment in {relative(path)}")
    if payload.get("status") != "frozen_before_outcome_read":
        raise ValueError(f"pre-unblind amendment is not frozen: {relative(path)}")
    if payload.get("amendment_version") != AMENDMENT_VERSION:
        raise ValueError(f"unexpected E62 amendment version in {relative(path)}")
    if payload.get("original_preregistration_sha256") != sha256(prereg_path):
        raise ValueError("E62 original preregistration hash changed after amendment")
    verify_predecessor(payload)
    expected_manifest_hash = prereg["source_hashes"]["regime_manifest_sha256"]
    if payload.get("frozen_regime_manifest_sha256") != expected_manifest_hash:
        raise ValueError("E62 amendment does not preserve the frozen manifest hash")
    source_paths = amendment_source_paths()
    for key, source_path in source_paths.items():
        if not source_path.exists():
            raise FileNotFoundError(f"missing amendment source: {source_path}")
        expected = (payload.get("source_hashes") or {}).get(key)
        observed = sha256(source_path)
        if expected != observed:
            raise ValueError(
                f"pre-unblind source hash mismatch for {key}: "
                f"expected {expected}, observed {observed}"
            )
    unchanged = payload.get("unchanged_original_contract") or {}
    if not unchanged or not all(value is True for value in unchanged.values()):
        raise ValueError("E62 amendment changed a frozen event/outcome contract")
    if payload.get("outcomes_read") is not False:
        raise ValueError("E62 amendment violated the blind outcome boundary")
    return payload


def ensure_amendment(
    prereg_path: Path,
    prereg: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    frozen_manifest = RESEARCH_ROOT / "regime_windows/latest_regime_windows.json"
    expected_manifest_hash = prereg["source_hashes"]["regime_manifest_sha256"]
    if not frozen_manifest.exists() or sha256(frozen_manifest) != expected_manifest_hash:
        raise ValueError(
            "the original E62 regime manifest no longer matches its frozen hash; "
            "refusing to amend or rebuild it"
        )
    existing = sorted(PREREG_ROOT.glob(AMENDMENT_GLOB))
    if existing:
        path = existing[-1]
        return path, verify_amendment(path, prereg_path, prereg)

    predecessors = sorted(PREREG_ROOT.glob(PREDECESSOR_GLOB))
    if not predecessors:
        raise FileNotFoundError(f"E62 predecessor amendment is missing: {PREDECESSOR_GLOB}")
    predecessor_path = predecessors[-1]

    source_paths = amendment_source_paths()
    missing = [str(path) for path in source_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("missing pre-unblind source(s): " + ", ".join(missing))
    created = datetime.now(UTC)
    path = PREREG_ROOT / (
        f"e62_pre_unblind_protocol_amendment_v{AMENDMENT_VERSION}_{created.strftime('%Y%m%d')}.json"
    )
    payload = {
        "created_at_utc": created.isoformat(),
        "experiment_id": "E62",
        "amendment_version": AMENDMENT_VERSION,
        "version_reason": (
            "Require authoritative non-empty Binance replacement of the closed "
            "tail, freeze the refresher source, and bind predecessor history."
        ),
        "status": "frozen_before_outcome_read",
        "research_only": True,
        "outcomes_read": False,
        "original_preregistration": relative(prereg_path),
        "original_preregistration_sha256": sha256(prereg_path),
        "supersedes_amendment": relative(predecessor_path),
        "supersedes_amendment_sha256": sha256(predecessor_path),
        "frozen_regime_manifest": relative(frozen_manifest),
        "frozen_regime_manifest_sha256": expected_manifest_hash,
        "purpose": (
            "Resolve only the preregistered blind regime-episode and causal 3m "
            "timestamp-coverage gates before any E62 price outcome is read."
        ),
        "causal_regime_rule": (
            "A regime state computed from completed UTC day D becomes effective on UTC day D+1."
        ),
        "coverage_rule": (
            "The auditor may read only the 3m date column and must require the "
            "first candle open strictly after each event plus the exact +60m open."
        ),
        "unchanged_original_contract": {
            "pairs": True,
            "liquidation_sides": True,
            "event_filter": True,
            "declustering_cooldown": True,
            "direction_lanes": True,
            "entry_rule": True,
            "outcome_horizons": True,
            "cost_model": True,
            "development_outcome_gates": True,
            "validation_reserve_gates": True,
        },
        "source_paths": {key: relative(value) for key, value in source_paths.items()},
        "source_hashes": {key: sha256(value) for key, value in source_paths.items()},
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path, verify_amendment(path, prereg_path, prereg)


def causal_label_rows(
    data: pd.DataFrame,
    as_of_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Return labels from completed source days, effective one UTC day later."""
    current_day = as_of_utc.tz_convert("UTC").floor("1D")
    completed = data.loc[data.index < current_day].dropna(subset=list(REQUIRED_FEATURES))
    rows: list[dict[str, Any]] = []
    for source_date, row in completed.iterrows():
        labels = labels_daily(row)
        effective_date = source_date + pd.Timedelta(days=1)
        rows.append(
            {
                "source_date_utc": source_date.strftime("%Y-%m-%d"),
                "effective_date_utc": effective_date.strftime("%Y-%m-%d"),
                "labels": list(labels),
                "state_key": "+".join(labels),
            }
        )
    return rows


def load_fresh_3m_features() -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    for pair in PAIRS:
        path = DATA_DIR / f"{pair}-3m-futures.feather"
        if not path.exists():
            raise FileNotFoundError(f"missing E62 regime source: {relative(path)}")
        candles = read_ohlcv(path)
        hourly = resample_to_1h(candles)
        feature_frame, _ = daily_features_from_hourly(
            pair,
            hourly,
            f"{relative(path)} resampled_to_1h",
        )
        frames.append(feature_frame)
        sources.append(
            {
                "pair": pair,
                "path": relative(path),
                "sha256": sha256(path),
                "first_3m_open_utc": candles.index.min().isoformat(),
                "last_3m_open_utc": candles.index.max().isoformat(),
                "rows": len(candles),
            }
        )
    return combine_daily_feature_frames(frames), sources


def write_outputs(payload: dict[str, Any]) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = payload["generated_at_utc"]
    json_path = OUTPUT_DIR / f"e62_causal_daily_regime_labels_{tag}.json"
    md_path = OUTPUT_DIR / f"e62_causal_daily_regime_labels_{tag}.md"
    json_text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    json_path.write_text(json_text, encoding="utf-8")
    (OUTPUT_DIR / "latest_e62_causal_daily_regime_labels.json").write_text(
        json_text,
        encoding="utf-8",
    )
    rows = payload["daily_labels"]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state_key"]] = counts.get(row["state_key"], 0) + 1
    lines = [
        "# E62 Causal Daily Regime Labels",
        "",
        f"- Generated UTC: `{tag}`",
        f"- Completed source days: `{len(rows)}`",
        "- Effective lag: `1 UTC day`",
        "- Source timeframe: `3m resampled to 1h`",
        "- Outcomes read: `False`",
        f"- Amendment: `{payload['amendment']}`",
        "",
        "## State Counts",
        "",
        *[f"- `{state}`: `{count}`" for state, count in sorted(counts.items())],
        "",
        "A state calculated from completed UTC day D is not usable until UTC day D+1. "
        "The artifact contains labels and dates only; it does not publish candle values "
        "or post-event outcomes.",
        "",
    ]
    markdown = "\n".join(lines)
    md_path.write_text(markdown, encoding="utf-8")
    (OUTPUT_DIR / "latest_e62_causal_daily_regime_labels.md").write_text(
        markdown,
        encoding="utf-8",
    )
    return json_path, md_path


def build_payload() -> dict[str, Any]:
    prereg_path, prereg = load_preregistration()
    amendment_path, amendment = ensure_amendment(prereg_path, prereg)
    features, sources = load_fresh_3m_features()
    as_of = pd.Timestamp.now(tz="UTC")
    rows = causal_label_rows(features, as_of)
    if not rows:
        raise ValueError("no completed causal daily regime labels are available")
    return {
        "generated_at_utc": utc_tag(),
        "experiment_id": "E62",
        "status": "causal_daily_regime_labels_built_before_outcome_read",
        "research_only": True,
        "outcomes_read": False,
        "original_preregistration": relative(prereg_path),
        "original_preregistration_sha256": sha256(prereg_path),
        "frozen_regime_manifest_sha256": prereg["source_hashes"]["regime_manifest_sha256"],
        "amendment": relative(amendment_path),
        "amendment_sha256": sha256(amendment_path),
        "amendment_source_hashes": amendment["source_hashes"],
        "data_sources": sources,
        "source_timeframe": "3m_resampled_to_1h",
        "completed_utc_days_only": True,
        "effective_lag_days": 1,
        "label_model": "direction_state_plus_orthogonal_high_vol_overlay",
        "label_causality": ("completed source day D becomes effective on UTC day D+1"),
        "daily_labels": rows,
    }


def main() -> int:
    payload = build_payload()
    json_path, md_path = write_outputs(payload)
    print(relative(json_path))
    print(relative(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
