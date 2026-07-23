from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "strategy_research"
    / "start_manual_research.sh"
)
RESEARCH_ROOT = SCRIPT.parent


def test_read_only_preflight_and_regime_rebuild_run_before_state_bootstrap() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    preflight_only = text.index('if [[ "$mode" == "preflight_only" ]]')
    regime_rebuild = text.index('if [[ "$mode" == "regime_windows" ]]')
    postmortem = text.index("bootstrap E1-E62 research postmortem")

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


def test_repo_entrypoints_resolve_root_from_git_worktree() -> None:
    for name in (
        "start_manual_research.sh",
        "run_tonight_research_daemon.sh",
        "stage_e62_background_runtime.sh",
        "run_e62_background_collection.sh",
    ):
        text = (RESEARCH_ROOT / name).read_text(encoding="utf-8")
        assert "git -C \"$SCRIPT_DIR\" rev-parse --show-toplevel" in text
        assert 'SCRIPT_DIR/../../user_data/strategy_research' not in text


def test_research_reset_solidifies_allocator_memory_and_dashboard() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    helper = text[
        text.index("solidify_research_reset()") : text.index('case "$mode" in', text.index("solidify_research_reset()"))
    ]
    reset_case = text[text.index("  research_reset)") : text.index("  e62_background_once)")]

    assert "research_failure_funnel.py" in helper
    assert "build_research_memory.py" in helper
    assert "build_research_consolidation.py" in helper
    assert "refresh_dashboard_if_available" in helper
    assert "run_research_reflection" in reset_case
    assert "solidify_research_reset" in reset_case
