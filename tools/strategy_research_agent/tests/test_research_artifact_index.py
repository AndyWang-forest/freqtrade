from __future__ import annotations

import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(AGENT_DIR))

from research_artifact_index import context_key, publish_report  # noqa: E402


def write_run(tmp_path: Path, name: str, payload: dict) -> tuple[Path, Path]:
    json_path = tmp_path / f"{name}.json"
    md_path = tmp_path / f"{name}.md"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    md_path.write_text(name, encoding="utf-8")
    return json_path, md_path


def test_all_history_run_cannot_replace_current_target_pointer(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    latest_json = tmp_path / "latest.json"
    latest_md = tmp_path / "latest.md"
    targeted = {
        "generated_at_utc": "targeted",
        "pair_scope": "core",
        "regime_label": "bear",
        "research_target": {"family_codes": ["A1", "D1"]},
    }
    targeted_paths = write_run(tmp_path, "targeted", targeted)
    publish_report(
        payload=targeted,
        json_path=targeted_paths[0],
        md_path=targeted_paths[1],
        index_path=index,
        artifact_type="factor",
        generic_latest_json=latest_json,
        generic_latest_md=latest_md,
        publish_current=True,
    )
    diagnostic = {
        "generated_at_utc": "diagnostic",
        "pair_scope": "core",
        "regime_label": None,
        "research_target": None,
    }
    diagnostic_paths = write_run(tmp_path, "diagnostic", diagnostic)
    publish_report(
        payload=diagnostic,
        json_path=diagnostic_paths[0],
        md_path=diagnostic_paths[1],
        index_path=index,
        artifact_type="factor",
        generic_latest_json=latest_json,
        generic_latest_md=latest_md,
        publish_current=False,
    )

    assert json.loads(latest_json.read_text())["generated_at_utc"] == "targeted"
    assert json.loads(index.read_text())["current_target"]["generated_at_utc"] == "targeted"


def test_context_key_separates_timeframe_and_explicit_pair_sets() -> None:
    base = {
        "pair_scope": "explicit",
        "regime_label": "bear",
        "research_target": {"family_codes": ["A1"]},
    }

    key_5m_btc = context_key({**base, "timeframe": "5m", "pairs": ["BTC/USDT:USDT"]})
    key_15m_btc = context_key({**base, "timeframe": "15m", "pairs": ["BTC/USDT:USDT"]})
    key_5m_eth = context_key({**base, "timeframe": "5m", "pairs": ["ETH/USDT:USDT"]})

    assert len({key_5m_btc, key_15m_btc, key_5m_eth}) == 3
