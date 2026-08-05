"""Reviewer prompt size budget — regression guard against prose re-bloat.

The reviewer prompt built by ``_build_prompt`` is assembled and sent every
review round. Its fixed instruction prose had grown to restate the same ideas
3-6x with worked examples; an operator-requested compression cut the decision /
planner-report / checkpoint / step-back prose roughly in half while preserving
every consumed named verdict field and every anti-cheat guardrail.

This test pins a CHARACTER BUDGET on the built non-measured prompt so fixed
policy prose cannot silently regrow. Task-specific checklists remain allowed;
role/routing and named-verdict explanations must stay compact.
"""

from __future__ import annotations

from argus_skill.reviewer import Reviewer

# Repeated evidence-policy prose was removed; the representative prompt is now
# about 6.5k chars. Keep headroom for task checklists without permitting the
# global policy block to return.
NON_MEASURED_BUDGET = 9_000


def _build(measured: bool, monkeypatch) -> str:
    if measured:
        monkeypatch.setenv("ARGUS_SKILL_MEASURED_MODE", "1")
    else:
        monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective="minimize cand_ms on the kernel",
        operator_messages=["make the kernel faster"],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="HANDOFF: tried X. RESULT correct=true cand_ms=0.5",
        main_error=None,
        prior_checkpoint={},
    )


def test_non_measured_prompt_within_budget(monkeypatch):
    p = _build(measured=False, monkeypatch=monkeypatch)
    assert len(p) < NON_MEASURED_BUDGET, (
        f"reviewer non-measured prompt is {len(p)} chars, over the "
        f"{NON_MEASURED_BUDGET} budget. The fixed instruction prose has "
        "regrown — re-compress (delete repetition/examples) rather than raising "
        "this cap, unless a genuinely new block was deliberately added."
    )


def test_compression_removed_redundant_examples(monkeypatch):
    # Tie the guard to the actual compression, not just a byte count: these
    # verbose snippets were deleted and must not reappear (they are the
    # redundancy the cut targeted).
    p = _build(measured=False, monkeypatch=monkeypatch)
    assert "you are not a JSON robot" not in p
    assert "Anti-pattern: agent shows test_accuracy=0.98" not in p
    assert "expense_tracker/ package using unittest" not in p
    assert "## Evidence policy" not in p


def test_reviewer_records_prompt_block_token_estimates(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
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


def test_reviewer_does_not_duplicate_identical_objective(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
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
    from argus_skill.skills.vertical_select import persist_vertical

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
    assert stats["chars"] < 1_100
    assert stats["estimated_tokens"] < 300


def test_reviewer_prompt_uses_named_footer_without_schema_language(monkeypatch) -> None:
    prompt = _build(measured=False, monkeypatch=monkeypatch)

    assert "STATUS=done|continue|blocked|replan_requested" in prompt
    assert "NEXT_ACTION=<the Engineer instruction; empty for done>" in prompt
    assert "JSON Schema" not in prompt
    assert "OUTPUT CONTRACT (STRICT)" not in prompt
