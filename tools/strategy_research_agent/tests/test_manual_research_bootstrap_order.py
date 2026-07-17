from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "strategy_research"
    / "start_manual_research.sh"
)


def test_read_only_preflight_and_regime_rebuild_run_before_state_bootstrap() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    preflight_only = text.index('if [[ "$mode" == "preflight_only" ]]')
    regime_rebuild = text.index('if [[ "$mode" == "regime_windows" ]]')
    postmortem = text.index("bootstrap E1-E41 research postmortem")

    assert preflight_only < postmortem
    assert regime_rebuild < postmortem
    assert "--allow-research-reflection-rebuild" in text


def test_factor_research_refreshes_current_strategy_plan_before_consolidation() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    block = text[text.index("  factor_research)") : text.index("  factor_to_strategy)")]

    event_gate = block.index("run_factor_candidate_event_study.py")
    factor_plan = block.index("factor_to_strategy_plan.py")
    consolidation = block.index("build_research_consolidation.py")

    assert event_gate < factor_plan < consolidation
