from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from run_event_study import study_event  # noqa: E402


def test_event_study_enters_at_next_candle_open() -> None:
    rows = 13
    frame = pd.DataFrame(
        {
            "open": [100.0] + [200.0] * (rows - 1),
            "high": [101.0] + [225.0] * (rows - 1),
            "low": [99.0] + [195.0] * (rows - 1),
            "close": [100.0, 205.0, 210.0, 220.0] + [220.0] * (rows - 4),
        }
    )
    mask = pd.Series([True] + [False] * (rows - 1))

    result = study_event(frame, mask, "test_long", "BTC/USDT:USDT", "long", 1)

    assert result.mean_ret_3 == pytest.approx(220.0 / 200.0 - 1.0)
