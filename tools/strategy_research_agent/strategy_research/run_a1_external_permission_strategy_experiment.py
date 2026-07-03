#!/usr/bin/env python3
"""Run A1 external permission strategy validation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def find_repo_root() -> Path:
    for path in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (path / "pyproject.toml").exists() and (path / "user_data").exists():
            return path
    raise RuntimeError("Could not locate freqtrade repo root.")


REPO_ROOT = find_repo_root()
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_RUNNER = SCRIPT_DIR / "run_a1_range_bull_abstain_experiment.py"
PERMISSION_BUILDER = SCRIPT_DIR / "build_a1_external_regime_permission_experiment.py"
if not BASE_RUNNER.exists():
    BASE_RUNNER = REPO_ROOT / "user_data/strategy_research/run_a1_range_bull_abstain_experiment.py"
if not PERMISSION_BUILDER.exists():
    PERMISSION_BUILDER = REPO_ROOT / "user_data/strategy_research/build_a1_external_regime_permission_experiment.py"

spec = importlib.util.spec_from_file_location("a1_range_bull_abstain", BASE_RUNNER)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load base runner: {BASE_RUNNER}")
RUNNER = importlib.util.module_from_spec(spec)
sys.modules["a1_range_bull_abstain"] = RUNNER
spec.loader.exec_module(RUNNER)

builder_spec = importlib.util.spec_from_file_location("a1_external_permission_builder", PERMISSION_BUILDER)
if builder_spec is None or builder_spec.loader is None:
    raise RuntimeError(f"Unable to load permission builder: {PERMISSION_BUILDER}")
BUILDER = importlib.util.module_from_spec(builder_spec)
sys.modules["a1_external_permission_builder"] = BUILDER
builder_spec.loader.exec_module(BUILDER)


RUNNER.RESULT_DIR = REPO_ROOT / "user_data/backtest_results/20260703T_a1_external_permission_strategy"
RUNNER.STRATEGY_INFO = {
    "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060": {
        "family": "downtrend_failed_bounce_short",
        "logic": "Current A1 research baseline.",
        "filters": "ret_96 <= 0.5%, volume_ratio >= 0.60, peak40.",
    },
    "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060ExternalPermissionQ85": {
        "family": "downtrend_failed_bounce_short",
        "logic": "A1 baseline filtered by precomputed external q85 daily regime permission artifact.",
        "filters": "daily artifact: combined BTC/ETH ret30 < 0 and realized vol30 <= rolling q85.",
    },
    "A1NoChaseRet96Max005PeakDrawdown040VolumeConfirm060ExternalPermissionQ90": {
        "family": "downtrend_failed_bounce_short",
        "logic": "A1 baseline filtered by precomputed external q90 daily regime permission artifact.",
        "filters": "daily artifact: combined BTC/ETH ret30 < 0 and realized vol30 <= rolling q90.",
    },
}
RUNNER.STRATEGIES = list(RUNNER.STRATEGY_INFO)


if __name__ == "__main__":
    # Refresh the local permission artifact before running the Freqtrade strategy
    # implementation test. This does not alter dry-run/live config.
    BUILDER.main()
    RUNNER.main()
