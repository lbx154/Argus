"""Reviewer prompt size budget — regression guard against prose re-bloat.

The reviewer prompt built by ``_build_prompt`` is assembled and sent every
review round. Its fixed instruction prose had grown to restate the same ideas
3-6x with worked examples; an operator-requested compression cut the decision /
planner-report / checkpoint / step-back prose roughly in half while preserving
every consumed named verdict field and every anti-cheat guardrail.

This test pins a CHARACTER BUDGET on the FIXED POLICY PROSE so it cannot
silently regrow. It used to pin the whole assembled prompt, which measured the
wrong thing: on the representative build below the research stage checklist is
3.8k of the 8.6k total, so 45% of a budget meant for role/routing prose was
actually being spent by a vertical's checklist. Either side could then exhaust
the other's headroom, and the cheapest way out of a red build was to raise the
cap — which is how a guard against growth becomes a record of it.

Task-specific content (stage checklist, matched Skills, Wiki, research target)
is subtracted out. Those blocks are owned by a vertical and are meant to vary;
what must stay compact is the role/routing and named-verdict prose this file
was written to protect. The objective, the Planner's guidance, and the operator
text need no subtraction: they ride in the round delta so the static preamble
stays byte-identical — and its fingerprint valid — across missions.
"""

from __future__ import annotations

import json

from argus.reviewer import Reviewer


def _build(monkeypatch) -> tuple[str, Reviewer]:
    r = Reviewer(runner=None, skill_store=None)
    prompt = r._build_prompt(
        objective="minimize cand_ms on the kernel",
        operator_messages=["make the kernel faster"],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="HANDOFF: tried X. RESULT correct=true cand_ms=0.5",
        main_error=None,
        prior_checkpoint={},
    )
    return prompt, r


def _prompt(monkeypatch) -> str:
    return _build(monkeypatch)[0]


def _persist_research_stage(project_root, stage: str) -> None:
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(project_root, "research")
    state_path = project_root / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["current_stage"] = stage
    state_path.write_text(json.dumps(payload), encoding="utf-8")


def test_ceremonial_frontier_footer_fields_are_not_requested(monkeypatch):
    prompt = _prompt(monkeypatch=monkeypatch)

    for field in (
        "FRONTIER_CHANGE",
        "FRONTIER_SUMMARY",
        "FRONTIER_OBLIGATIONS",
        "FRONTIER_EVIDENCE",
        "NEXT_DECISION_POINT",
        "REGRESSION_ENVELOPE",
        "SESSION_SIGNAL",
    ):
        assert field not in prompt

    # The handoff line routes the round.
    assert "forward_progress" not in prompt
    assert "`to=planner`" in prompt


def test_reviewer_records_prompt_block_token_estimates(monkeypatch):
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="audit the current research result",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="RESULT: evidence exists",
        main_error=None,
        prior_checkpoint={},
    )

    stats = reviewer.last_prompt_block_stats
    assert stats["static_total"]["chars"] > 0
    assert stats["delta_total"]["chars"] > 0
    assert stats["main_summary"]["chars"] == len("RESULT: evidence exists")
    assert stats["static_total"]["estimated_tokens"] > 0
    assert stats["static_total"]["chars"] + stats["delta_total"]["chars"] == len(prompt)


def test_bounded_review_stage_checklist_stays_compact(
    tmp_path,
    monkeypatch,
) -> None:
    _persist_research_stage(tmp_path, "review")
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="bounded submission package repair",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="done",
        main_error=None,
        prior_checkpoint={},
        working_dir=str(tmp_path),
        scope="bounded",
    )

    stats = reviewer.last_prompt_block_stats["stage_checklist"]
    assert stats["chars"] < 10_000
    assert stats["estimated_tokens"] < 2_500
    assert "## Stage checklist (review)" in prompt
    assert "Full pipeline checklist" not in prompt
    assert "reviewing one task within a larger project" in prompt
    assert "only the checklist items materially touched by this task" in prompt
    assert "review.scope" in prompt
    assert "review.visual" in prompt


def test_reviewer_does_not_duplicate_identical_objective(monkeypatch):
    reviewer = Reviewer(runner=None, skill_store=None)
    objective = "repair the exact benchmark evidence " * 120

    prompt = reviewer._build_prompt(
        objective=objective,
        original_objective=objective,
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="done",
        main_error=None,
        prior_checkpoint={},
    )

    assert prompt.count(objective.strip()) == 1
    assert "Task objective:" in prompt
    assert "Original operator request:" not in prompt
    assert reviewer.last_prompt_block_stats["objective_context"]["chars"] < (len(objective) + 64)


def test_reviewer_keeps_distinct_original_and_mission_objectives(monkeypatch):
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="repair the benchmark",
        original_objective="produce a publishable paper",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="done",
        main_error=None,
        prior_checkpoint={},
    )

    assert "Original operator request:\nproduce a publishable paper" in prompt
    assert "Current mission objective:\nrepair the benchmark" in prompt


def test_research_target_context_stays_compact(tmp_path, monkeypatch):
    """The block may hold the contract's vocabulary, and nothing more.

    The bound was 1_100 chars while the block listed the five research-result
    field *names* and none of their legal values. Testbed run 15
    (``s-f0dbba19``) emitted six ``RESEARCH_RESULT`` blocks under it and the
    contract rejected all six, every one for inventing vocabulary the prompt
    had never supplied. Enumerating the five value sets costs ~600 chars, and
    600 chars that make a hard gate answerable are worth more than a bound
    that made it unanswerable.

    So the guard moves rather than disappears, and what it now guards is
    prose: the value lists are rendered from ``RESULT_FIELD_CHOICES``, so they
    track the contract on their own and only added text can push this over.
    The margin above is sized for the longest verification-profile line, not
    for another paragraph.
    """
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(
        tmp_path,
        "research",
        research_target_level="publishable",
    )
    reviewer = Reviewer(runner=None, skill_store=None)
    reviewer._build_prompt(
        objective="repair the benchmark",
        original_objective="repair the benchmark",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="done",
        main_error=None,
        prior_checkpoint={},
        working_dir=str(tmp_path),
        scope="bounded",
    )

    stats = reviewer.last_prompt_block_stats["research_target"]
    assert stats["chars"] < 1_320
    assert stats["estimated_tokens"] < 340


def _change_prompt(snapshots, raw_evidence=""):
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective="evaluate the model",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="accuracy 0.93",
        main_error=None,
        prior_checkpoint={},
        raw_evidence=raw_evidence,
        snapshots=snapshots,
    )


def _task(tmp_path):
    from argus.core.workspace_snapshot import WorkspaceSnapshots

    workspace = tmp_path / "my project"
    workspace.mkdir()
    (workspace / "train.py").write_text("split = 'test'\n", encoding="utf-8")
    snapshots = WorkspaceSnapshots(tmp_path / "state" / "snapshots.git", workspace, "m1")
    assert snapshots.take("baseline")
    return workspace, snapshots


def test_reviewer_starts_from_the_diff_then_the_commands_then_the_account(tmp_path):
    workspace, snapshots = _task(tmp_path)
    (workspace / "train.py").write_text("split = 'train'\n", encoding="utf-8")
    (workspace / "results").mkdir()
    (workspace / "results" / "eval results.json").write_text('{"acc": 0.93}\n', encoding="utf-8")
    (workspace / ".argus").mkdir()
    (workspace / ".argus" / "state.json").write_text("{}", encoding="utf-8")
    assert snapshots.take("candidate")

    prompt = _change_prompt(snapshots, raw_evidence="$ pytest -q\n3 passed")

    block = prompt.split("## Workspace changes under review\n", 1)[1].split("Raw verification", 1)[0]
    assert "2 files changed, +2 -1:" in block
    assert "- results/: 1 file, +1 -0\n" in block  # run outputs are counted, never diffed
    assert "- train.py: +1 -1\n" in block
    assert "do not re-explore the workspace" in block
    assert "--- train.py\n@@ -1 +1 @@\n-split = 'test'\n+split = 'train'\n" in block
    assert ".argus" not in block.split("caches and nested repositories.", 1)[1]
    order = [
        prompt.index("## Workspace changes under review"),
        prompt.index("Raw verification evidence"),
        prompt.index("## Engineer's account of this round"),
    ]
    assert order == sorted(order)


def test_a_very_large_change_is_listed_by_counts_beyond_the_caps(tmp_path):
    from argus.roles.prompts import reviewer as prompts

    workspace, snapshots = _task(tmp_path)
    for index in range(150):
        lines = "".join(f"value_{index}_{line} = {'x' * 400}\n" for line in range(200))
        (workspace / f"module_{index:03d}.py").write_text(lines, encoding="utf-8")
    assert snapshots.take("candidate")

    prompt = _change_prompt(snapshots)

    block = prompt.split("## Workspace changes under review\n", 1)[1].split("Main agent fatal", 1)[0]
    excerpt = block.split("\nDiff excerpt:\n", 1)[1].splitlines()
    assert len(excerpt) <= prompts._DIFF_TOTAL_LINES + prompts._DIFF_TOTAL_LINES // prompts._DIFF_FILE_LINES
    assert max(len(line) for line in excerpt) <= prompts._DIFF_LINE_CHARS
    assert "- module_000.py: +200 -0, excerpt cut\n" in block
    assert "- module_079.py: +200 -0, not excerpted\n" in block
    assert "- and 70 more files: +14000 -0" in block
    assert "module_080.py" not in block.split("\nDiff excerpt:\n", 1)[0]
    assert "150 files changed, +30000 -0:" in block
    assert "Read a file marked cut or not excerpted yourself" in block
    assert len(block) < 90_000


def test_an_unchanged_workspace_says_so_and_no_snapshots_say_nothing(tmp_path):
    _, snapshots = _task(tmp_path)
    assert snapshots.take("candidate")

    assert "Nothing in it has changed." in _change_prompt(snapshots)
    assert "## Workspace changes under review" not in _change_prompt(None)
