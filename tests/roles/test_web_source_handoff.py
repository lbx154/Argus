from argus.reviewer import Reviewer


def test_source_cache_handoff_is_workspace_scoped_and_keeps_static_prefix_unchanged(tmp_path):
    reviewer = Reviewer(runner=None, skill_store=None)

    def render():
        return reviewer._build_prompt(
            objective="Review the source-backed survey", operator_messages=[],
            planner_review_instruction="", round_index=1, session_id=None,
            main_summary="Read RESEARCH_NOTES.md for the cited source paths", main_error=None,
            working_dir=str(tmp_path),
        )

    without = render()
    assert "Web source cache:" not in without
    prefix_length = reviewer.last_prompt_block_stats["static_total"]["chars"]
    assert reviewer.last_prompt_block_stats["web_sources"]["chars"] == 0

    sources = tmp_path / ".argus" / "sources"
    sources.mkdir(parents=True)
    (sources / "paper.txt").write_text("Source text belongs in a tool read, not the prompt.")
    with_sources = render()
    assert without[:prefix_length] == with_sources[:prefix_length]
    assert reviewer.last_prompt_block_stats["static_total"]["chars"] == prefix_length
    assert with_sources.index("Web source cache:") >= prefix_length
    assert str(sources.resolve()) in with_sources
    assert "never hunt downloads in shared `/tmp`" in with_sources
    assert "Source text belongs in a tool read" not in with_sources
