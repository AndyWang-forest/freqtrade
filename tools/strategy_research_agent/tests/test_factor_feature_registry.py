from __future__ import annotations

import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

import pandas as pd

from factor_feature_registry import (  # noqa: E402
    FACTOR_SPECS,
    _merge_prior,
    load_oi_metrics,
    registry_payload,
)


def test_registry_covers_all_external_brain_domains() -> None:
    domains = {item.domain for item in FACTOR_SPECS}
    assert domains == {"price_action", "regime", "derivatives", "microstructure", "cross_asset"}


def test_every_factor_declares_data_and_knowledge_provenance() -> None:
    assert all(item.data_requirement for item in FACTOR_SPECS)
    assert all(item.knowledge_cards for item in FACTOR_SPECS)
    assert registry_payload()["causality_contract"]


def test_prior_merge_normalizes_feather_datetime_units() -> None:
    left = pd.DataFrame(
        {
            "date": pd.Series(["2026-01-01T01:00:00Z"], dtype="datetime64[ms, UTC]"),
            "close": [1.0],
        }
    )
    right = pd.DataFrame(
        {
            "available": pd.Series(["2026-01-01T00:00:00Z"], dtype="datetime64[us, UTC]"),
            "signal": [2.0],
        }
    )
    merged = _merge_prior(left, right, "available")
    assert merged.loc[0, "signal"] == 2.0


def test_prior_merge_tolerance_does_not_forward_carry_stale_auxiliary_data() -> None:
    left = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2026-01-01T00:05:00Z", "2026-01-01T00:20:00Z"],
                utc=True,
            )
        }
    )
    right = pd.DataFrame(
        {
            "available": pd.to_datetime(["2026-01-01T00:00:00Z"], utc=True),
            "signal": [2.0],
        }
    )

    merged = _merge_prior(
        left,
        right,
        "available",
        tolerance=pd.Timedelta(minutes=10),
    )

    assert merged.loc[0, "signal"] == 2.0
    assert pd.isna(merged.loc[1, "signal"])


def test_cached_oi_audit_uses_current_max_age_contract(tmp_path: Path) -> None:
    aux_root = tmp_path / "aux"
    cache_root = tmp_path / "cache"
    source_dir = aux_root / "open_interest_metrics" / "BTCUSDT"
    source_dir.mkdir(parents=True)
    cache_root.mkdir(parents=True)
    archive = source_dir / "BTCUSDT-metrics-2026-01-01.zip"
    archive.write_bytes(b"cached-source-signature")
    cache_path = cache_root / "BTCUSDT_oi_metrics.feather"
    pd.DataFrame(
        {
            "oi_date": pd.to_datetime(["2026-01-01T00:00:00Z"], utc=True),
            "top_size_account_divergence": [0.1],
        }
    ).to_feather(cache_path)
    signature = {
        "count": 1,
        "names": [archive.name],
        "sizes": [archive.stat().st_size],
    }
    (cache_root / "BTCUSDT_oi_metrics.meta.json").write_text(
        json.dumps(
            {
                "signature": signature,
                "audit": {
                    "requirement": "open_interest_metrics",
                    "status": "available",
                    "causality": "strictly_prior_5m_metric",
                },
            }
        ),
        encoding="utf-8",
    )

    _, audit = load_oi_metrics("BTC/USDT:USDT", aux_root, cache_root)

    assert audit["causality"] == "strictly_prior_5m_metric_max_age_10m"
    assert audit["max_age_minutes"] == 10
