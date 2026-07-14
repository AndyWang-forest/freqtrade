#!/usr/bin/env python3
"""Causal, research-only market-structure translation for Chan hypotheses.

This module intentionally implements a conservative subset only:

1. online K-line inclusion handling;
2. right-confirmed top/bottom fractals;
3. alternating pivots whose endpoints are locked by a later opposite pivot;
4. three-stroke overlap hubs; and
5. strict third-point departure/retest events.

It is not presented as the only canonical interpretation of Chan theory.  The
important contract is causal availability: no structure can trigger an event
before the raw bar index recorded in ``signal_idx``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import pandas as pd


Direction = Literal["up", "down"]
PivotKind = Literal["top", "bottom"]


@dataclass(frozen=True)
class CanonicalBar:
    start_idx: int
    end_idx: int
    open: float
    high: float
    low: float
    close: float
    high_idx: int
    low_idx: int


@dataclass(frozen=True)
class Fractal:
    kind: PivotKind
    canonical_pos: int
    pivot_idx: int
    value: float
    available_idx: int


@dataclass
class LockedPivot:
    kind: PivotKind
    canonical_pos: int
    pivot_idx: int
    value: float
    available_idx: int
    locked_at_idx: int | None = None
    sequence: int = -1


@dataclass(frozen=True)
class LockedStroke:
    sequence: int
    start_pivot_sequence: int
    end_pivot_sequence: int
    low: float
    high: float
    confirmed_idx: int


@dataclass(frozen=True)
class Hub:
    sequence: int
    first_stroke_sequence: int
    last_stroke_sequence: int
    last_pivot_sequence: int
    lower: float
    upper: float
    confirmed_idx: int


@dataclass(frozen=True)
class ThirdPointEvent:
    event: str
    side: Literal["long", "short"]
    signal_idx: int
    retest_pivot_idx: int
    departure_pivot_idx: int
    hub_confirmed_idx: int
    hub_lower: float
    hub_upper: float


@dataclass(frozen=True)
class ChanStructure:
    canonical_bars: list[CanonicalBar]
    fractals: list[Fractal]
    pivots: list[LockedPivot]
    strokes: list[LockedStroke]
    hubs: list[Hub]
    events: list[ThirdPointEvent]


def _has_inclusion(left: CanonicalBar, right: CanonicalBar) -> bool:
    left_contains = left.high >= right.high and left.low <= right.low
    right_contains = right.high >= left.high and right.low <= left.low
    return left_contains or right_contains


def _direction(
    previous: CanonicalBar | None, current: CanonicalBar, new: CanonicalBar
) -> Direction:
    if previous is not None:
        if current.high > previous.high and current.low > previous.low:
            return "up"
        if current.high < previous.high and current.low < previous.low:
            return "down"
    return "up" if new.close >= current.close else "down"


def _pick(
    left_value: float,
    left_idx: int,
    right_value: float,
    right_idx: int,
    mode: Literal["min", "max"],
) -> tuple[float, int]:
    if mode == "max":
        return (right_value, right_idx) if right_value >= left_value else (left_value, left_idx)
    return (right_value, right_idx) if right_value <= left_value else (left_value, left_idx)


def merge_inclusion_bars(frame: pd.DataFrame) -> list[CanonicalBar]:
    """Merge inclusion bars with information available in chronological order."""

    required = {"open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")

    bars: list[CanonicalBar] = []
    for raw_idx, row in frame.reset_index(drop=True).iterrows():
        new = CanonicalBar(
            start_idx=int(raw_idx),
            end_idx=int(raw_idx),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            high_idx=int(raw_idx),
            low_idx=int(raw_idx),
        )
        if not bars or not _has_inclusion(bars[-1], new):
            bars.append(new)
            continue

        current = bars[-1]
        previous = bars[-2] if len(bars) >= 2 else None
        direction = _direction(previous, current, new)
        high_mode: Literal["min", "max"] = "max" if direction == "up" else "min"
        low_mode: Literal["min", "max"] = "max" if direction == "up" else "min"
        high, high_idx = _pick(current.high, current.high_idx, new.high, new.high_idx, high_mode)
        low, low_idx = _pick(current.low, current.low_idx, new.low, new.low_idx, low_mode)
        bars[-1] = CanonicalBar(
            start_idx=current.start_idx,
            end_idx=new.end_idx,
            open=current.open,
            high=high,
            low=low,
            close=new.close,
            high_idx=high_idx,
            low_idx=low_idx,
        )
    return bars


def confirmed_fractals(bars: list[CanonicalBar]) -> list[Fractal]:
    """Return fractals only after the right canonical bar is itself locked.

    The extra canonical bar at ``center + 2`` proves that the right bar no
    longer participates in an inclusion merge.  Its first raw bar close is the
    earliest causal availability time for the fractal.
    """

    fractals: list[Fractal] = []
    for center_pos in range(1, len(bars) - 2):
        left = bars[center_pos - 1]
        center = bars[center_pos]
        right = bars[center_pos + 1]
        available_idx = bars[center_pos + 2].start_idx
        is_top = (
            center.high > left.high
            and center.high > right.high
            and center.low > left.low
            and center.low > right.low
        )
        is_bottom = (
            center.low < left.low
            and center.low < right.low
            and center.high < left.high
            and center.high < right.high
        )
        if is_top:
            fractals.append(Fractal("top", center_pos, center.high_idx, center.high, available_idx))
        elif is_bottom:
            fractals.append(
                Fractal("bottom", center_pos, center.low_idx, center.low, available_idx)
            )
    return fractals


def _more_extreme(candidate: LockedPivot, fractal: Fractal) -> bool:
    if candidate.kind == "top":
        return fractal.value >= candidate.value
    return fractal.value <= candidate.value


def lock_alternating_pivots(
    fractals: list[Fractal],
    min_canonical_gap: int = 4,
) -> list[LockedPivot]:
    """Build alternating pivots while keeping only the last endpoint provisional."""

    pivots: list[LockedPivot] = []
    for fractal in sorted(fractals, key=lambda item: item.available_idx):
        incoming = LockedPivot(
            kind=fractal.kind,
            canonical_pos=fractal.canonical_pos,
            pivot_idx=fractal.pivot_idx,
            value=fractal.value,
            available_idx=fractal.available_idx,
        )
        if not pivots:
            pivots.append(incoming)
            continue
        current = pivots[-1]
        if current.kind == incoming.kind:
            if _more_extreme(current, fractal):
                pivots[-1] = incoming
            continue
        if incoming.canonical_pos - current.canonical_pos < min_canonical_gap:
            continue
        current.locked_at_idx = incoming.available_idx
        pivots.append(incoming)

    for sequence, pivot in enumerate(pivots):
        pivot.sequence = sequence
    return pivots


def build_locked_strokes(pivots: list[LockedPivot]) -> list[LockedStroke]:
    strokes: list[LockedStroke] = []
    for start_pos in range(len(pivots) - 1):
        start = pivots[start_pos]
        end = pivots[start_pos + 1]
        if end.locked_at_idx is None:
            continue
        strokes.append(
            LockedStroke(
                sequence=len(strokes),
                start_pivot_sequence=start.sequence,
                end_pivot_sequence=end.sequence,
                low=min(start.value, end.value),
                high=max(start.value, end.value),
                confirmed_idx=end.locked_at_idx,
            )
        )
    return strokes


def build_hubs(strokes: list[LockedStroke]) -> list[Hub]:
    hubs: list[Hub] = []
    for end_pos in range(2, len(strokes)):
        group = strokes[end_pos - 2 : end_pos + 1]
        lower = max(stroke.low for stroke in group)
        upper = min(stroke.high for stroke in group)
        if lower >= upper:
            continue
        hubs.append(
            Hub(
                sequence=len(hubs),
                first_stroke_sequence=group[0].sequence,
                last_stroke_sequence=group[-1].sequence,
                last_pivot_sequence=group[-1].end_pivot_sequence,
                lower=lower,
                upper=upper,
                confirmed_idx=group[-1].confirmed_idx,
            )
        )
    return hubs


def detect_third_points(
    pivots: list[LockedPivot],
    hubs: list[Hub],
    max_follow_pivots: int = 8,
) -> list[ThirdPointEvent]:
    """Detect strict non-reentry third points from locked pivots only."""

    events: dict[tuple[str, int], ThirdPointEvent] = {}
    for hub in hubs:
        future = [
            pivot
            for pivot in pivots
            if pivot.sequence > hub.last_pivot_sequence
            and pivot.locked_at_idx is not None
            and pivot.available_idx >= hub.confirmed_idx
        ][:max_follow_pivots]

        departure_top: LockedPivot | None = None
        departure_bottom: LockedPivot | None = None
        for pivot in future:
            if pivot.kind == "top":
                if pivot.value > hub.upper:
                    departure_top = pivot
                if departure_bottom is not None:
                    if pivot.value < hub.lower:
                        event = ThirdPointEvent(
                            event="chan_third_sell",
                            side="short",
                            signal_idx=int(pivot.locked_at_idx),
                            retest_pivot_idx=pivot.pivot_idx,
                            departure_pivot_idx=departure_bottom.pivot_idx,
                            hub_confirmed_idx=hub.confirmed_idx,
                            hub_lower=hub.lower,
                            hub_upper=hub.upper,
                        )
                        key = (event.side, event.signal_idx)
                        existing = events.get(key)
                        if existing is None or event.hub_confirmed_idx > existing.hub_confirmed_idx:
                            events[key] = event
                        break
                    departure_bottom = None
            else:
                if pivot.value < hub.lower:
                    departure_bottom = pivot
                if departure_top is not None:
                    if pivot.value > hub.upper:
                        event = ThirdPointEvent(
                            event="chan_third_buy",
                            side="long",
                            signal_idx=int(pivot.locked_at_idx),
                            retest_pivot_idx=pivot.pivot_idx,
                            departure_pivot_idx=departure_top.pivot_idx,
                            hub_confirmed_idx=hub.confirmed_idx,
                            hub_lower=hub.lower,
                            hub_upper=hub.upper,
                        )
                        key = (event.side, event.signal_idx)
                        existing = events.get(key)
                        if existing is None or event.hub_confirmed_idx > existing.hub_confirmed_idx:
                            events[key] = event
                        break
                    departure_top = None
    return sorted(events.values(), key=lambda item: (item.signal_idx, item.side))


def compute_chan_structure(
    frame: pd.DataFrame,
    min_canonical_gap: int = 4,
    max_follow_pivots: int = 8,
) -> ChanStructure:
    canonical = merge_inclusion_bars(frame)
    fractals = confirmed_fractals(canonical)
    pivots = lock_alternating_pivots(fractals, min_canonical_gap=min_canonical_gap)
    strokes = build_locked_strokes(pivots)
    hubs = build_hubs(strokes)
    events = detect_third_points(pivots, hubs, max_follow_pivots=max_follow_pivots)
    return ChanStructure(canonical, fractals, pivots, strokes, hubs, events)


def structure_counts(structure: ChanStructure) -> dict[str, int]:
    return {
        "raw_confirmed_fractals": len(structure.fractals),
        "locked_or_provisional_pivots": len(structure.pivots),
        "locked_strokes": len(structure.strokes),
        "candidate_hubs": len(structure.hubs),
        "third_point_events": len(structure.events),
    }


def event_records(structure: ChanStructure) -> list[dict[str, Any]]:
    return [asdict(event) for event in structure.events]


def unique_event_outcomes(trades: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate event outcomes introduced by overlapping regime windows."""

    required = {"pair", "event", "signal_idx"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"Missing event identity columns: {sorted(missing)}")
    return trades.drop_duplicates(["pair", "event", "signal_idx"]).copy()


def select_independent_positive_windows(trades: pd.DataFrame) -> list[str]:
    """Return a maximum-size non-overlapping set of positive windows."""

    if trades.empty:
        return []
    required = {
        "window",
        "window_start",
        "window_end_exclusive",
        "realistic_account_pct_8h",
    }
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"Missing window evidence columns: {sorted(missing)}")
    windows = (
        trades.groupby(
            ["window", "window_start", "window_end_exclusive"],
            as_index=False,
            dropna=False,
        )["realistic_account_pct_8h"]
        .mean()
        .rename(columns={"realistic_account_pct_8h": "mean_return"})
    )
    positive = windows[windows["mean_return"] > 0].sort_values(
        ["window_end_exclusive", "window_start", "window"]
    )
    selected: list[str] = []
    last_end: pd.Timestamp | None = None
    for row in positive.itertuples(index=False):
        start = pd.Timestamp(row.window_start)
        end = pd.Timestamp(row.window_end_exclusive)
        if last_end is not None and start < last_end:
            continue
        selected.append(str(row.window))
        last_end = end
    return selected
