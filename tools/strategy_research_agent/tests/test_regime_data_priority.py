from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

import regime_window_builder as builder  # noqa: E402


def write_ohlcv(path: Path, dates: pd.DatetimeIndex) -> None:
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
            "volume": 10.0,
        }
    )
    frame.to_feather(path)


def test_load_pair_prefers_broader_1h_history_over_slightly_fresher_5m(
    tmp_path: Path, monkeypatch
) -> None:
    pair = "BTC_USDT_USDT"
    hourly = pd.date_range("2021-01-01", periods=48, freq="1h", tz="UTC")
    five_minute = pd.date_range("2024-01-01", periods=600, freq="5min", tz="UTC")
    write_ohlcv(tmp_path / f"{pair}-1h-futures.feather", hourly)
    write_ohlcv(tmp_path / f"{pair}-5m-futures.feather", five_minute)
    monkeypatch.setattr(builder, "DATA_DIR", tmp_path)

    loaded, source = builder.load_pair_1h(pair)

    assert loaded.index.min() == hourly.min()
    assert loaded.index.max() == hourly.max()
    assert source.endswith(f"{pair}-1h-futures.feather")


def test_high_vol_is_an_overlay_on_bear_direction() -> None:
    row = pd.Series(
        {
            "combined_vol_pctile": 0.90,
            "combined_atr_pctile": 0.70,
            "combined_ret_30d": -0.10,
            "combined_ret_60d": -0.20,
            "btc_ret_60d": -0.21,
            "eth_ret_60d": -0.19,
            "combined_ema_gap": -0.05,
            "combined_trend_efficiency": 0.30,
            "direction_agreement_60d": 1.0,
        }
    )

    assert builder.labels_daily(row) == ("bear", "high_vol")


def test_rolling_percentile_does_not_use_future_observations() -> None:
    baseline = pd.Series(range(1, 401), dtype=float)
    changed_future = baseline.copy()
    changed_future.iloc[300:] = -1_000.0

    baseline_pct = builder.rolling_percentile(baseline)
    changed_pct = builder.rolling_percentile(changed_future)

    pd.testing.assert_series_equal(baseline_pct.iloc[:300], changed_pct.iloc[:300])


def test_bear_window_must_finish_lower_for_both_core_pairs() -> None:
    frame = pd.DataFrame(
        {
            "btc_close": [100.0, 90.0, 105.0],
            "eth_close": [100.0, 80.0, 120.0],
        }
    )

    assert builder.window_direction_matches_label("bear", frame) is False


def test_family_roles_do_not_double_count_overlapping_direction_and_volatility() -> None:
    windows = [
        {
            "name": "bear_home",
            "label": "bear",
            "status": "active",
            "start": "2022-05-09",
            "end": "2022-07-07",
            "days": 60,
            "confidence": "high",
            "evidence": {"label_share": 0.87},
        },
        {
            "name": "high_vol_overlap",
            "label": "high_vol",
            "status": "active",
            "start": "2022-05-14",
            "end": "2022-07-12",
            "days": 60,
            "confidence": "medium",
            "evidence": {"label_share": 0.60},
        },
        {
            "name": "bull_hostile",
            "label": "bull",
            "status": "active",
            "start": "2023-10-29",
            "end": "2023-12-27",
            "days": 60,
            "confidence": "medium",
            "evidence": {"label_share": 0.63},
        },
    ]

    roles = builder.family_window_roles(windows)["downside_breakout_continuation_short"]

    assert roles["home"] == ["bear_home"]
    assert roles["hostile"] == ["bull_hostile"]


def test_same_label_validation_episodes_do_not_overlap() -> None:
    windows = [
        {
            "name": "bull_primary",
            "label": "bull",
            "status": "active",
            "start": "2020-11-01",
            "end": "2021-01-14",
            "days": 75,
            "confidence": "high",
            "evidence": {"label_share": 0.79},
        },
        {
            "name": "bull_overlapping",
            "label": "bull",
            "status": "active",
            "start": "2021-01-01",
            "end": "2021-03-01",
            "days": 60,
            "confidence": "medium",
            "evidence": {"label_share": 0.57},
        },
        {
            "name": "bull_independent",
            "label": "bull",
            "status": "active",
            "start": "2023-10-29",
            "end": "2023-12-27",
            "days": 60,
            "confidence": "medium",
            "evidence": {"label_share": 0.63},
        },
    ]

    roles = builder.family_window_roles(windows)["uptrend_pullback_long"]

    assert roles["home"] == ["bull_primary", "bull_independent"]


def test_regime_entry_mask_requires_forward_horizon_inside_window() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01 23:30", periods=7, freq="5min", tz="UTC")
        }
    )
    manifest = {
        "windows": [
            {
                "name": "bull_day",
                "label": "bull",
                "status": "active",
                "start": "2023-01-01",
                "end": "2023-01-01",
            }
        ]
    }

    mask = builder.regime_entry_mask(frame, "bull", 3, "5m", manifest)

    assert mask.tolist() == [True, True, True, False, False, False, False]
