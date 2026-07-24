from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

import audit_e62_pre_unblind as auditor  # noqa: E402
import build_e62_causal_regime_labels as labels  # noqa: E402
import refresh_binance_um_ohlcv_tail as refresh  # noqa: E402


def test_pre_unblind_amendment_version_binds_closed_tail_and_predecessor() -> None:
    assert labels.AMENDMENT_VERSION == 3
    assert labels.AMENDMENT_GLOB == "e62_pre_unblind_protocol_amendment_v3_*.json"
    assert labels.PREDECESSOR_GLOB == "e62_pre_unblind_protocol_amendment_v2_*.json"
    assert (
        labels.amendment_source_paths()["e62_ohlcv_tail_refresher_sha256"].name
        == "refresh_binance_um_ohlcv_tail.py"
    )


def feature_row(state: str) -> dict[str, float]:
    row = {
        "combined_ret_30d": 0.0,
        "combined_ret_60d": 0.0,
        "btc_ret_60d": 0.0,
        "eth_ret_60d": 0.0,
        "combined_ema_gap": 0.0,
        "combined_vol_pctile": 0.3,
        "combined_atr_pctile": 0.3,
        "combined_trend_efficiency": 0.1,
        "direction_agreement_60d": 1.0,
    }
    if state == "bear":
        row.update(
            {
                "combined_ret_30d": -0.18,
                "combined_ret_60d": -0.20,
                "btc_ret_60d": -0.21,
                "eth_ret_60d": -0.19,
                "combined_ema_gap": -0.05,
                "combined_trend_efficiency": 0.30,
            }
        )
    return row


def event_row(symbol: str, timestamp: str) -> dict[str, object]:
    event = pd.Timestamp(timestamp, tz="UTC")
    return {"symbol": symbol, "event_time_ms": int(event.timestamp() * 1000)}


def test_causal_labels_use_completed_days_and_become_effective_next_day() -> None:
    index = pd.date_range("2026-07-20", periods=4, freq="1D", tz="UTC")
    frame = pd.DataFrame(
        [feature_row("mixed"), feature_row("bear"), feature_row("bear"), feature_row("mixed")],
        index=index,
    )

    rows = labels.causal_label_rows(
        frame,
        pd.Timestamp("2026-07-23 12:00:00", tz="UTC"),
    )

    assert [row["source_date_utc"] for row in rows] == [
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    ]
    assert [row["effective_date_utc"] for row in rows] == [
        "2026-07-21",
        "2026-07-22",
        "2026-07-23",
    ]
    assert rows[-1]["state_key"] == "bear"
    assert set(rows[-1]) == {
        "source_date_utc",
        "effective_date_utc",
        "labels",
        "state_key",
    }


def test_regime_gate_counts_distinct_contiguous_episodes() -> None:
    daily = [
        {
            "effective_date_utc": "2026-07-21",
            "state_key": "mixed",
            "labels": ["mixed"],
        },
        {
            "effective_date_utc": "2026-07-22",
            "state_key": "mixed",
            "labels": ["mixed"],
        },
        {
            "effective_date_utc": "2026-07-23",
            "state_key": "bear",
            "labels": ["bear"],
        },
        {
            "effective_date_utc": "2026-07-24",
            "state_key": "bear",
            "labels": ["bear"],
        },
    ]
    events = [
        *[event_row("BTCUSDT", f"2026-07-21 01:{minute:02d}:00") for minute in range(10)],
        *[event_row("ETHUSDT", f"2026-07-23 01:{minute:02d}:00") for minute in range(10)],
    ]

    result = auditor.regime_episode_gate(events, daily, minimum_events=10)

    assert result["label_coverage_pass"] is True
    assert result["eligible_episode_count"] == 2
    assert [episode["state_key"] for episode in result["episodes"]] == [
        "mixed",
        "bear",
    ]
    assert [episode["independent_events"] for episode in result["episodes"]] == [
        10,
        10,
    ]


def test_first_candle_is_strictly_after_event_even_on_boundary() -> None:
    inside = int(pd.Timestamp("2026-07-23 04:53:30", tz="UTC").timestamp() * 1000)
    boundary = int(pd.Timestamp("2026-07-23 04:54:00", tz="UTC").timestamp() * 1000)

    assert auditor.first_3m_open_strictly_after(inside) == pd.Timestamp(
        "2026-07-23 04:54:00", tz="UTC"
    )
    assert auditor.first_3m_open_strictly_after(boundary) == pd.Timestamp(
        "2026-07-23 04:57:00", tz="UTC"
    )


def test_epoch_millis_normalization_handles_millisecond_datetime_unit() -> None:
    timestamps = pd.DatetimeIndex(
        ["2026-07-23T04:37:00Z", "2026-07-23T04:40:00Z"],
        dtype="datetime64[ms, UTC]",
    )

    epoch_ms = timestamps.as_unit("ns").asi8 // 1_000_000

    assert epoch_ms.tolist() == [1784781420000, 1784781600000]


def test_timestamp_coverage_requires_entry_and_exact_plus_60m_open() -> None:
    event = event_row("BTCUSDT", "2026-07-23 04:53:30")
    entry = pd.Timestamp("2026-07-23 04:54:00", tz="UTC")
    plus_60m = entry + pd.Timedelta(minutes=60)
    date_sets = {symbol: set() for symbol in auditor.EXPECTED_PAIRS}
    date_sets["BTCUSDT"] = {
        int(entry.timestamp() * 1000),
        int(plus_60m.timestamp() * 1000),
    }

    passed = auditor.timestamp_coverage_gate([event], date_sets)
    date_sets["BTCUSDT"].remove(int(plus_60m.timestamp() * 1000))
    failed = auditor.timestamp_coverage_gate([event], date_sets)

    assert passed["passed"] is True
    assert passed["ohlcv_columns_read"] == ["date"]
    assert passed["candle_values_read"] is False
    assert failed["passed"] is False
    assert failed["missing_examples"][0]["missing"] == "plus_60m"


def test_last_closed_open_excludes_in_progress_candle() -> None:
    now = pd.Timestamp(datetime(2026, 7, 24, 5, 8, 47, tzinfo=UTC))

    assert refresh.last_closed_open(now, "3m") == pd.Timestamp("2026-07-24 05:03:00", tz="UTC")


def test_refresh_trims_unclosed_tail_and_refetches_last_closed_candle(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "BTC_USDT_USDT-3m-futures.feather"
    pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2026-07-24 05:00:00+00:00",
                    "2026-07-24 05:03:00+00:00",
                    "2026-07-24 05:06:00+00:00",
                ]
            ),
            "open": [1.0, 999.0, 3.0],
            "high": [1.0, 999.0, 3.0],
            "low": [1.0, 999.0, 3.0],
            "close": [1.0, 999.0, 3.0],
            "volume": [1.0, 999.0, 3.0],
        }
    ).to_feather(path)

    def fake_fetch(*_args, **_kwargs):
        assert _args[2] == pd.Timestamp("2026-07-24 05:03:00", tz="UTC")
        assert _args[3] == pd.Timestamp("2026-07-24 05:03:00", tz="UTC")
        return (
            pd.DataFrame(
                {
                    "date": [pd.Timestamp("2026-07-24 05:03:00", tz="UTC")],
                    "open": [2.0],
                    "high": [2.0],
                    "low": [2.0],
                    "close": [2.0],
                    "volume": [2.0],
                }
            ),
            "https://fapi.binance.com",
        )

    monkeypatch.setattr(refresh, "pair_data_path", lambda *_args: path)
    monkeypatch.setattr(refresh, "fetch_klines", fake_fetch)

    result = refresh.refresh_pair(
        "BTC/USDT:USDT",
        "3m",
        pd.Timestamp("2026-07-24 05:08:47", tz="UTC"),
        refresh.DEFAULT_BASE_URLS,
        1.0,
    )
    stored = pd.read_feather(path)
    stored["date"] = pd.to_datetime(stored["date"], utc=True)

    assert result["unclosed_rows_removed"] == 1
    assert stored["date"].max() == pd.Timestamp("2026-07-24 05:03:00", tz="UTC")
    assert stored.loc[stored["date"] == stored["date"].max(), "open"].iloc[0] == 2.0


def test_refresh_rejects_empty_response_for_last_closed_candle(monkeypatch, tmp_path) -> None:
    path = tmp_path / "BTC_USDT_USDT-3m-futures.feather"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-24 05:03:00+00:00"]),
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }
    ).to_feather(path)

    monkeypatch.setattr(refresh, "pair_data_path", lambda *_args: path)
    monkeypatch.setattr(
        refresh,
        "fetch_klines",
        lambda *_args, **_kwargs: (
            pd.DataFrame(columns=refresh.OHLCV_COLUMNS),
            "https://fapi.binance.com",
        ),
    )

    with pytest.raises(RuntimeError, match="no authoritative closed candles"):
        refresh.refresh_pair(
            "BTC/USDT:USDT",
            "3m",
            pd.Timestamp("2026-07-24 05:08:47", tz="UTC"),
            refresh.DEFAULT_BASE_URLS,
            1.0,
        )
