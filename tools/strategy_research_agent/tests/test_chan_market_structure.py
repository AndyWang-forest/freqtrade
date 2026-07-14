from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

import chan_market_structure as chan  # noqa: E402


def test_fractal_is_available_only_after_right_bar_is_locked() -> None:
    frame = pd.DataFrame(
        [
            {"open": 1.2, "high": 2.0, "low": 1.0, "close": 1.5},
            {"open": 3.2, "high": 4.0, "low": 3.0, "close": 3.5},
            {"open": 2.2, "high": 3.0, "low": 2.0, "close": 2.5},
            {"open": 1.7, "high": 2.5, "low": 1.5, "close": 2.0},
        ]
    )

    bars = chan.merge_inclusion_bars(frame)
    fractals = chan.confirmed_fractals(bars)

    assert len(fractals) == 1
    assert fractals[0].kind == "top"
    assert fractals[0].pivot_idx == 1
    assert fractals[0].available_idx == 3
    assert fractals[0].available_idx > fractals[0].pivot_idx


def locked_pivot(
    sequence: int,
    kind: chan.PivotKind,
    value: float,
    locked_at: int | None,
) -> chan.LockedPivot:
    return chan.LockedPivot(
        kind=kind,
        canonical_pos=sequence * 4,
        pivot_idx=sequence * 10,
        value=value,
        available_idx=sequence * 10,
        locked_at_idx=locked_at,
        sequence=sequence,
    )


def third_buy_pivots(lock_retest: bool = True, reenter: bool = False) -> list[chan.LockedPivot]:
    return [
        locked_pivot(0, "bottom", 10.0, 10),
        locked_pivot(1, "top", 20.0, 20),
        locked_pivot(2, "bottom", 12.0, 30),
        locked_pivot(3, "top", 18.0, 40),
        locked_pivot(4, "bottom", 19.0, 50),
        locked_pivot(5, "top", 25.0, 60),
        locked_pivot(6, "bottom", 17.0 if reenter else 20.0, 70 if lock_retest else None),
        locked_pivot(7, "top", 26.0, 80) if lock_retest else locked_pivot(7, "top", 26.0, None),
    ]


def test_third_buy_requires_locked_non_reentry_retest() -> None:
    pivots = third_buy_pivots()
    hubs = chan.build_hubs(chan.build_locked_strokes(pivots))

    events = chan.detect_third_points(pivots, hubs)

    buys = [event for event in events if event.event == "chan_third_buy"]
    assert buys
    assert buys[0].signal_idx == 70
    assert buys[0].signal_idx > buys[0].retest_pivot_idx
    assert buys[0].hub_upper == 18.0


def test_third_buy_is_not_emitted_for_unlocked_or_reentering_retest() -> None:
    unlocked = third_buy_pivots(lock_retest=False)
    unlocked_events = chan.detect_third_points(
        unlocked,
        chan.build_hubs(chan.build_locked_strokes(unlocked)),
    )
    reenter = third_buy_pivots(reenter=True)
    reenter_events = chan.detect_third_points(
        reenter,
        chan.build_hubs(chan.build_locked_strokes(reenter)),
    )

    assert not [event for event in unlocked_events if event.event == "chan_third_buy"]
    assert not [event for event in reenter_events if event.event == "chan_third_buy"]


def test_locked_structure_events_are_prefix_stable() -> None:
    points = [10, 12, 14, 16, 18, 20, 18, 16, 14, 12] * 30
    frame = pd.DataFrame(
        {
            "open": points,
            "high": [value + 0.8 for value in points],
            "low": [value - 0.8 for value in points],
            "close": [value + 0.2 for value in points],
        }
    )
    full = chan.compute_chan_structure(frame)
    cutoff = 220
    prefix = chan.compute_chan_structure(frame.iloc[:cutoff])

    prefix_events = {
        (event.event, event.signal_idx, event.retest_pivot_idx) for event in prefix.events
    }
    full_events = {
        (event.event, event.signal_idx, event.retest_pivot_idx)
        for event in full.events
        if event.signal_idx < cutoff
    }

    assert prefix_events == full_events


def test_overlapping_regime_rows_are_deduplicated_by_event_identity() -> None:
    rows = pd.DataFrame(
        [
            {"pair": "BTC/USDT:USDT", "event": "chan_third_buy", "signal_idx": 10},
            {"pair": "BTC/USDT:USDT", "event": "chan_third_buy", "signal_idx": 10},
            {"pair": "ETH/USDT:USDT", "event": "chan_third_buy", "signal_idx": 10},
        ]
    )

    unique = chan.unique_event_outcomes(rows)

    assert len(unique) == 2


def test_positive_window_count_uses_non_overlapping_intervals() -> None:
    rows = pd.DataFrame(
        [
            {
                "window": "wide",
                "window_start": "2026-01-01",
                "window_end_exclusive": "2026-01-10",
                "realistic_account_pct_8h": 1.0,
            },
            {
                "window": "early",
                "window_start": "2026-01-05",
                "window_end_exclusive": "2026-01-08",
                "realistic_account_pct_8h": 2.0,
            },
            {
                "window": "later",
                "window_start": "2026-01-08",
                "window_end_exclusive": "2026-01-15",
                "realistic_account_pct_8h": 0.5,
            },
            {
                "window": "negative",
                "window_start": "2026-01-20",
                "window_end_exclusive": "2026-01-25",
                "realistic_account_pct_8h": -1.0,
            },
        ]
    )

    selected = chan.select_independent_positive_windows(rows)

    assert selected == ["early", "later"]
