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

Task-specific content (stage checklist, matched Skills, Wiki, research target,
objective and operator text) is subtracted out. Those blocks are owned by a
vertical or by the round and are meant to vary; what must stay compact is the
role/routing and named-verdict prose this file was written to protect.
"""

from __future__ import annotations

from argus_skill.reviewer import Reviewer
from argus_skill.roles.prompts import reviewer as reviewer_prompt
from argus_skill.roles.task_contract import NATIVE_WINDOWS_SHELL_SUMMARY

#: Blocks that belong to a vertical or to the round, not to the fixed contract.
_TASK_OWNED_BLOCKS = (
    "stage_checklist",
    "matched_skill",
    "direct_memory",
    "wiki_curator",
    "research_target",
    "objective_context",
)

# Measured at 4_546 for the representative build. The margin is for one more
# deliberate contract block, not for a paragraph of restatement: the last
# addition was the plan-signal vocabulary (~580 chars), which names the two
# legal `plan_signal` values and the three fields a plan challenge carries.
# `reconsider` is the only token that opens the Reviewer -> Manager -> Planner
# channel and it had appeared in no prompt, so deleting that block closes the
# channel rather than tightening the prose. Re-compress before raising this.
FIXED_PROSE_BUDGET = 5_000


def _fixed_prose_chars(reviewer: Reviewer) -> int:
    """Size of the contract prose alone, after the built prompt is measured."""
    stats = reviewer.last_prompt_block_stats
    task_owned = sum(stats[name]["chars"] for name in _TASK_OWNED_BLOCKS)
    return stats["static_total"]["chars"] - task_owned


def _build(measured: bool, monkeypatch) -> tuple[str, Reviewer]:
    if measured:
        monkeypatch.setenv("ARGUS_SKILL_MEASURED_MODE", "1")
    else:
        monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
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


def _prompt(measured: bool, monkeypatch) -> str:
    return _build(measured, monkeypatch)[0]


def test_fixed_contract_prose_within_budget(monkeypatch):
    _prompt_text, reviewer = _build(measured=False, monkeypatch=monkeypatch)
    fixed = _fixed_prose_chars(reviewer)
    assert fixed < FIXED_PROSE_BUDGET, (
        f"reviewer fixed contract prose is {fixed} chars, over the "
        f"{FIXED_PROSE_BUDGET} budget. Re-compress (delete repetition and "
        "examples) rather than raising this cap, unless a genuinely new "
        "contract block was deliberately added — and say which, here."
    )


def test_windows_fixed_contract_prose_within_budget(monkeypatch):
    monkeypatch.setattr(
        reviewer_prompt,
        "native_shell_summary",
        lambda: NATIVE_WINDOWS_SHELL_SUMMARY,
    )
    _prompt_text, reviewer = _build(measured=False, monkeypatch=monkeypatch)

    assert _fixed_prose_chars(reviewer) < FIXED_PROSE_BUDGET


def test_reviewer_performs_live_product_acceptance_when_applicable(monkeypatch):
    prompt = _prompt(measured=False, monkeypatch=monkeypatch)

    assert "product-user acceptance" in prompt
    assert "isolated state, non-production port" in prompt
    assert "test-only credentials" in prompt
    assert "Never cause external or irreversible effects" in prompt
    assert "Unit tests alone do not prove that flow" in prompt
    assert "stop it" in prompt


def test_the_budget_ignores_content_a_vertical_owns(monkeypatch):
    """A longer stage checklist must not spend the contract's headroom.

    This is the failure the whole-prompt cap had: the research checklist is
    most of the assembled prompt, so a vertical adding one line pushed the
    role prose over a limit it had not moved.
    """
    _prompt_text, reviewer = _build(measured=False, monkeypatch=monkeypatch)
    before = _fixed_prose_chars(reviewer)
    stats = dict(reviewer.last_prompt_block_stats)
    grown = dict(stats["stage_checklist"])
    grown["chars"] += 4_000
    stats["stage_checklist"] = grown
    stats["static_total"] = {
        **stats["static_total"],
        "chars": stats["static_total"]["chars"] + 4_000,
    }
    reviewer._last_prompt_block_stats = stats

    assert _fixed_prose_chars(reviewer) == before


def test_compression_removed_redundant_examples(monkeypatch):
    # Tie the guard to the actual compression, not just a byte count: these
    # verbose snippets were deleted and must not reappear (they are the
    # redundancy the cut targeted).
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "you are not a JSON robot" not in p
    assert "Anti-pattern: agent shows test_accuracy=0.98" not in p
    assert "expense_tracker/ package using unittest" not in p
    assert "## Evidence policy" not in p


def test_the_verdict_vocabulary_is_stated_once(monkeypatch):
    # `done`/`continue`/`replan_requested`/`blocked` used to be defined twice —
    # once in the role block and again, at greater length, in the handoff
    # policy. Two definitions of the same four words is the redundancy this
    # budget exists to catch, and it cost more than the sentence it funded.
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert p.count("concrete in-scope material gap") == 1
    assert p.count("wrong target or real boundary change") == 1


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
    assert stats["chars"] < 1_320
    assert stats["estimated_tokens"] < 340


def test_reviewer_prompt_records_process_decision_without_final_footer(monkeypatch) -> None:
    prompt = _prompt(measured=False, monkeypatch=monkeypatch)

    assert "ARGUS_ROLE_DECISION=" in prompt
    assert '"role":"reviewer"' in prompt
    assert "Any later response is plain language and is not parsed." in prompt
    assert "STATUS=done|continue|blocked|replan_requested" not in prompt
    assert "JSON Schema" not in prompt
    assert "OUTPUT CONTRACT (STRICT)" not in prompt


def test_reviewer_accepts_implementation_grounding_proportionally(
    monkeypatch,
) -> None:
    prompt = _prompt(measured=False, monkeypatch=monkeypatch)

    assert "primary-source grounding" in prompt
    assert "community implementations may suffice for implementation details" in prompt
    assert "`replan_requested` rarely" in prompt
    assert "Do not demand extra research" in prompt
