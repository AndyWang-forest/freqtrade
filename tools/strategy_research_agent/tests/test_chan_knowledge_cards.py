from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
KNOWLEDGE_DIR = MODULE_DIR / "knowledge"
QUARANTINED_DIR = KNOWLEDGE_DIR / "knowledge_cards_quarantined"
ACTIVE_DIR = KNOWLEDGE_DIR / "knowledge_cards"
sys.path.insert(0, str(MODULE_DIR))

import build_price_action_knowledge_layer as layer  # noqa: E402


EXPECTED_IDS = {
    "chan_kline_inclusion_confirmed_fractal",
    "chan_causal_stroke_segment_state_machine",
    "chan_hub_overlap_regime_structure",
    "chan_third_point_breakout_retest",
    "chan_divergence_measurable_hypothesis",
    "chan_anti_repaint_lookahead_contract",
}


def test_chan_cards_are_quarantined_and_have_causality_contracts() -> None:
    paths = sorted(QUARANTINED_DIR.glob("chan_*.json"))
    cards = [json.loads(path.read_text(encoding="utf-8")) for path in paths]

    assert {card["id"] for card in cards} == EXPECTED_IDS
    assert not list(ACTIVE_DIR.glob("chan_*.json"))
    for card in cards:
        contract = card["causality_contract"]
        assert contract["confirmation_time"]
        assert contract["signal_available_time"]
        assert contract["can_repaint"] is False
        assert contract["structural_level"]
        assert card["verification_status"]["quarantined"] is True
        assert card["agent_use"]["eligible_for_strategy_generation"] is False


def test_manual_quarantined_card_survives_knowledge_layer_rebuild(
    tmp_path: Path, monkeypatch
) -> None:
    active = tmp_path / "active"
    quarantined = tmp_path / "quarantined"
    active.mkdir()
    quarantined.mkdir()
    source = QUARANTINED_DIR / "chan_third_point_breakout_retest.json"
    shutil.copyfile(source, quarantined / source.name)
    monkeypatch.setattr(layer, "CARDS_DIR", active)
    monkeypatch.setattr(layer, "QUARANTINED_CARDS_DIR", quarantined)

    layer.build_cards([])

    rebuilt = json.loads((quarantined / source.name).read_text(encoding="utf-8"))
    assert rebuilt["id"] == "chan_third_point_breakout_retest"
    assert rebuilt["verification_status"]["quarantined"] is True
    assert rebuilt["agent_use"]["eligible_for_strategy_generation"] is False
