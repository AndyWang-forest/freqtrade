from __future__ import annotations

import sys
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "strategy_research"
sys.path.insert(0, str(MODULE_DIR))

from family_exit_risk_contract import canonical_family_id, family_exit_contract  # noqa: E402
from family_risk_gate import family_role_names  # noqa: E402
from plan_memory_guided_hypotheses import build_hypotheses  # noqa: E402
from regime_window_builder import FAMILY_HOME_LABELS  # noqa: E402
from strategy_taxonomy import family_contract, infer_family_from_card  # noqa: E402


E_FAMILY = "volatility_compression_directional_expansion"
LEGACY_E_FAMILY = "volatility_compression_breakout"


def test_legacy_e_family_normalizes_to_directional_expansion() -> None:
    assert canonical_family_id(LEGACY_E_FAMILY) == E_FAMILY
    assert family_contract(LEGACY_E_FAMILY)["strategy_family"] == E_FAMILY
    assert family_exit_contract(LEGACY_E_FAMILY)["strategy_family"] == E_FAMILY


def test_knowledge_card_explicit_e_family_is_preserved() -> None:
    card = {"freqtrade_translation": {"strategy_family": LEGACY_E_FAMILY}}
    assert infer_family_from_card(card) == E_FAMILY


def test_memory_hypothesis_prefers_lineage_family_over_name_guess() -> None:
    strategy = "E1SolXrpQ20ExpansionAtr10Short"
    memory = {
        "next_focus": [
            {
                "strategy": strategy,
                "blocker": "unknown_blocker",
                "objective": "Close promotion blocks before dry-run review.",
            }
        ],
        "avoid_patterns": [],
    }
    lineage = {
        "nodes": [
            {
                "name": strategy,
                "root": strategy,
                "family": E_FAMILY,
                "recommended_state": "research_candidate",
                "failure_attribution": {},
            }
        ]
    }

    hypotheses = build_hypotheses(memory, lineage, {})

    assert hypotheses[0]["strategy_family"] == E_FAMILY
    assert hypotheses[0]["strategy_family_code"] == "E"


def test_e_family_gate_uses_high_vol_for_new_and_legacy_ids() -> None:
    manifest = {
        "windows": [
            {"name": "high_vol_active", "label": "high_vol", "status": "active"},
            {"name": "range_active", "label": "range", "status": "active"},
        ],
        "family_window_roles": {
            E_FAMILY: {
                "home": ["high_vol_active"],
                "hostile": ["range_active"],
            }
        },
    }

    assert FAMILY_HOME_LABELS[E_FAMILY] == {"high_vol"}
    assert family_role_names(manifest, E_FAMILY, "home") == {"high_vol_active"}
    assert family_role_names(manifest, LEGACY_E_FAMILY, "home") == {"high_vol_active"}
