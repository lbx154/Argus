"""Planner prompt composition and size regression guards."""

from __future__ import annotations

import json
from types import SimpleNamespace

from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.life.memory import EventJournal
from argus.life.supervisor import LifeSupervisor
from argus.planner import Planner
from argus.roles.prompts.planner import _RESEARCH_PLAN_CONTRACT
from argus.skills.vertical_select import persist_vertical

# A little above what the staged argus_maintenance prompt costs today
# (2026-10-20, when math moved to argus-verticals and these guards moved off its
# prompt; raised by 100 on 2026-10-21 for the one line on offering an operator
# question's answers). Raise only for fixed policy that earns its space; compress first.
SCOPE_BUDGET = 9_550
MATURE_SCOPE_BUDGET = 15_350
RESEARCH_PLAN_DYNAMIC_BUDGET = 8_000


def _without_host_shell_guidance(prompt: str) -> str:
    """Drop native-Windows shell guidance, which is host-specific by design."""
    from argus.roles.prompts.engineer import _WINDOWS_LONG_EXPERIMENT_RULE
    from argus.roles.task_contract import (
        NATIVE_WINDOWS_SHELL_CONTRACT,
        NATIVE_WINDOWS_SHELL_SUMMARY,
    )

    for text in (NATIVE_WINDOWS_SHELL_CONTRACT, _WINDOWS_LONG_EXPERIMENT_RULE, NATIVE_WINDOWS_SHELL_SUMMARY):
        prompt = prompt.replace(text, "")
    return prompt


def _build_scope_prompt(
    tmp_path,
    monkeypatch,
    *,
    journal_tail: str = "(empty)",
) -> tuple[str, str]:
    persist_vertical(tmp_path, "argus_maintenance")
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    objective = (
        "Search current mathematical literature and primary sources to identify "
        "a genuinely unresolved conjecture for which a counterexample would be "
        "meaningful. Independently choose a tractable candidate only after "
        "verifying that it remains open, then conduct an honest, reproducible "
        "counterexample search with premise checks. Claim a counterexample only "
        "after every hypothesis and the current literature are independently "
        "reviewed. Preserve sources, code, raw outputs, and negative results. "
        "Continue autonomously with another unresolved conjecture when a route "
        "is exhausted."
    )
    runtime_context = (
        "## Manager intent boundary (authoritative)\n"
        "- intent_id: intent-test\n"
        "- source: daemon_boot\n"
        "- interpreted_vertical: argus_maintenance\n"
        "- kind: software\n"
        "- stages: inspect, change, verify\n"
        "- reason: manager completed daemon objective handoff\n\n"
        "Plan only work consistent with this Manager boundary."
    )
    prompt = Planner._build_planner_prompt(
        continuous_objective=objective,
        journal_tail=journal_tail,
        planning_cycle=0,
        runtime_change_summary=runtime_context,
        open_ended=True,
    )
    # Measured without the test's own directory, whose length varies by machine and worker.
    return prompt.replace(str(tmp_path), "<project>"), objective


def test_scope_prompt_is_compact_and_deduplicated(
    tmp_path,
    monkeypatch,
) -> None:
    prompt, objective = _build_scope_prompt(tmp_path, monkeypatch)

    assert len(_without_host_shell_guidance(prompt)) < SCOPE_BUDGET, (
        f"scope Planner prompt is {len(_without_host_shell_guidance(prompt))} chars; keep fixed policy "
        "compact and move state-specific guidance behind structured triggers"
    )
    assert prompt.count(objective) == 1
    assert "## Manager mission brief (authoritative)\n" + objective in prompt
    assert "## Original operator request (immutable anchor)" not in prompt
    assert "Argus planner role skill:" not in prompt
    assert "waiting_contract" not in prompt
    assert prompt.count("PROJECT_DONE=false") == 1
    assert "TASK_KEY=k1" in prompt
    assert 'prerequisites, not directions' in prompt
    flat = " ".join(prompt.split())
    assert flat.count('Integrity/reproducibility are prerequisites, not directions') == 1
    assert 'Engineer implements and tests' in prompt
    assert "JSON matching the provided schema" not in prompt


def test_scope_prompt_excludes_unrelated_modules(
    tmp_path,
    monkeypatch,
) -> None:
    prompt, _objective = _build_scope_prompt(tmp_path, monkeypatch)

    assert '## Assigning work' in prompt
    assert "## Current workflow stage" in prompt
    assert "current: `inspect`" in prompt
    assert "## Stage checklist (inspect)" in prompt
    assert "## Stage gate" not in prompt
    assert "## Parallel paper-drafting track" not in prompt
    assert "PAPER_INFRASTRUCTURE_REVIEW.json" not in prompt
    assert "RESULT_PLACEHOLDERS.md" not in prompt


def test_direct_workflow_suppresses_stage_artifact_ceremony(
    tmp_path,
    monkeypatch,
) -> None:
    persist_vertical(
        tmp_path,
        "argus_maintenance",
        workflow_mode="direct",
    )
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))

    prompt = Planner._build_planner_prompt(
        continuous_objective="Directly optimize MiniMax H3 inference on M4 Pro.",
        journal_tail="(empty)",
        planning_cycle=0,
        open_ended=True,
    )

    assert "## Direct workflow — objective first" in prompt
    assert 'describes the kind of work, without requiring a separate set of files' in prompt
    assert "## Stage checklist (inspect)" not in prompt
    assert "## Stage gate" not in prompt


def test_planner_keeps_operator_actions_ahead_of_optional_hardening(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    prompt = Planner._build_planner_prompt(
        continuous_objective=(
            "Download the BF16 model, quantize it, and prove local inference works."
        ),
        journal_tail="An older 8-bit model already has a local manifest.",
        planning_cycle=0,
        open_ended=False,
    )

    assert 'Follow requested actions in order' in prompt
    assert 'outputs or alternatives' in prompt
    flat = " ".join(prompt.split())
    assert 'outputs or alternatives do not replace unmet actions' in flat
    assert 'Add no cleanup, docs, rechecks or optional hardening to completed finite work' in flat


def test_research_prompt_carries_no_final_submission_scope_machinery(
    tmp_path,
    monkeypatch,
) -> None:
    """The final-submission scope block was removed with the rest of the
    scripted verification machinery; the review stage judges the submission."""
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="publishable",
    )
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    write_pipeline_state(tmp_path, state)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))

    prompt = Planner._build_planner_prompt(
        continuous_objective=(
            "Obtain final independent certification for the research submission."
        ),
        journal_tail="(empty)",
        planning_cycle=0,
        project_root=tmp_path,
        state_root=tmp_path,
        open_ended=True,
    )

    # The scope marker itself survives (completion accounting reads it); the
    # prescriptive section that scripted the final-submission workflow is gone.
    assert "## Final-submission task scope" not in prompt


def test_mature_prompt_keeps_only_bounded_terminal_history(
    tmp_path,
    monkeypatch,
) -> None:
    journal = "\n".join(
        f"- [07-22 12:0{index}] mission_complete: result-{index} — " + ("evidence " * 210)
        for index in range(3)
    )

    prompt, _objective = _build_scope_prompt(
        tmp_path,
        monkeypatch,
        journal_tail=journal,
    )

    assert len(_without_host_shell_guidance(prompt)) < MATURE_SCOPE_BUDGET


def test_planner_journal_uses_latest_three_terminal_outcomes() -> None:
    entries = [
        SimpleNamespace(
            kind="mission_started",
            ts=0.0,
            title="start-noise",
            summary="",
            extra={},
        ),
        *[
            SimpleNamespace(
                kind="mission_complete",
                ts=float(index),
                title=f"terminal-{index}",
                summary="result " + ("x" * 3_000),
                extra={},
            )
            for index in range(4)
        ],
        SimpleNamespace(
            kind="research_pause",
            ts=8.0,
            title="paused-methods",
            summary="current approach exhausted",
            extra={},
        ),
        SimpleNamespace(
            kind="planner_cycle",
            ts=9.0,
            title="planner-noise",
            summary="",
            extra={},
        ),
    ]
    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.memory = SimpleNamespace(
        journal=SimpleNamespace(
            tail_kinds=lambda _count, *, kinds: [
                entry for entry in entries if entry.kind in kinds
            ][-_count:],
            tail_settlements=lambda _count, *, kinds=None: [
                entry for entry in entries
                if kinds is None or entry.kind in kinds
            ][-_count:],
        ),
    )

    rendered = supervisor._render_journal_for_planner()

    assert "start-noise" not in rendered
    assert "planner-noise" not in rendered
    assert "terminal-0" not in rendered
    assert "terminal-1" not in rendered
    assert all(f"terminal-{index}" in rendered for index in range(2, 4))
    assert "paused-methods" in rendered
    assert len(rendered) <= 3 * 1_800 + 2


def test_planner_journal_window_survives_waiting_heartbeat_floods(tmp_path) -> None:
    """Journal chatter after a settlement must not hide the settlement.

    The window used to be ``tail(64)`` post-filtered by kind, so 70 waiting
    heartbeats systematically evicted every terminal outcome the Planner was
    supposed to reason over. An independent ``life.budget.pause`` event (which
    has no settlement source) must also stay visible so the Planner can see
    why nothing is running.
    """
    path = tmp_path / "events.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.mission.completed",
            "item_id": "aaaa1111bbbb",
            "ts": 1.0,
            "success": True,
            "status": "done",
            "title": "settled mission",
            "summary": "landed the result",
        }) + "\n")
        for index in range(70):
            fh.write(json.dumps({
                "type": "life.planner.waiting",
                "ts": 2.0 + index,
                "reason": "waiting on external dependency",
            }) + "\n")
        fh.write(json.dumps({
            "type": "life.budget.pause",
            "ts": 100.0,
            "title": "budget pause",
            "reason": "daily cap reached",
        }) + "\n")

    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.memory = SimpleNamespace(journal=EventJournal(path))

    rendered = supervisor._render_journal_for_planner()

    assert "settled mission" in rendered
    assert "budget pause" in rendered
    assert "waiting on external dependency" not in rendered


def _research_plan(*, experiment_fill: str = "") -> str:
    return (
        "# Research plan\n"
        "Objective: establish a decisive publishable result.\n\n"
        "## Central hypotheses\n"
        "1. [untested] H1 — evidence: journal mission-1\n\n"
        "## Experiment program\n"
        "- Run the discriminating experiment. " + experiment_fill + "\n\n"
        "## Established results\n"
        "- Baseline reproduced — evidence: results/base.json\n\n"
        "## Dead ends\n"
        "- Old route — abandoned after null result in journal mission-0\n\n"
        "## Next milestone\n"
        "A cross-dataset effect with an independently checked mechanism."
    )


def test_research_planner_prompt_drops_frozen_plan_block(
    tmp_path,
) -> None:
    """The research vertical no longer plans through a frozen RESEARCH_PLAN.md:
    the experimental design lives in the experiment stage and is revised in
    contact with evidence, so the planner prompt carries no plan block even
    when a plan file exists."""
    from argus.life.research_plan import render_research_plan_for_planner
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    plan = _research_plan()
    (tmp_path / "RESEARCH_PLAN.md").write_text(plan, encoding="utf-8")
    rendered = render_research_plan_for_planner(tmp_path)
    prompt = Planner._build_planner_prompt(
        continuous_objective="Establish the result.",
        journal_tail="(empty)",
        research_plan=rendered,
        planning_cycle=2,
        project_root=tmp_path,
        state_root=tmp_path,
    )

    assert '## Research plan' not in prompt
    assert "PLAN_UPDATE" not in prompt


def test_planner_prompt_marks_absent_or_corrupt_plan_for_creation(tmp_path) -> None:
    from argus.life.research_plan import render_research_plan_for_planner

    absent = render_research_plan_for_planner(tmp_path)
    (tmp_path / "RESEARCH_PLAN.md").write_text("not the contract", encoding="utf-8")
    corrupt = render_research_plan_for_planner(tmp_path)

    assert "no plan yet" in absent
    assert "create RESEARCH_PLAN.md" in absent
    assert corrupt == absent


def test_research_plan_contract_avoids_hard_result_gates() -> None:
    assert 'no fixed numeric pass/fail thresholds' in _RESEARCH_PLAN_CONTRACT
    assert 'scientific gain toward a scoped paper' in _RESEARCH_PLAN_CONTRACT


def test_oversize_research_plan_keeps_head_and_next_milestone_with_hard_cap(
    tmp_path,
) -> None:
    from argus.life.research_plan import render_research_plan_for_planner

    (tmp_path / "RESEARCH_PLAN.md").write_text(
        _research_plan(experiment_fill="x" * 12_000),
        encoding="utf-8",
    )
    rendered = render_research_plan_for_planner(tmp_path)

    assert len(rendered) <= RESEARCH_PLAN_DYNAMIC_BUDGET
    assert rendered.startswith("# Research plan")
    assert "living plan truncated" in rendered
    assert "## Next milestone" in rendered
    assert "independently checked mechanism" in rendered


def test_planner_prompt_asks_for_fanout_of_independent_work(
    tmp_path,
    monkeypatch,
) -> None:
    """The fan-out judgement ground must reach every route, not only staged
    research: independent work that differs only in inputs and writes
    disjoint outputs is emitted together up to the free slots, after any
    shared code change it depends on."""
    prompt, _ = _build_scope_prompt(tmp_path, monkeypatch)
    flat = " ".join(prompt.split())

    assert "differ only in inputs or config" in flat
    assert "free slots" in flat
    assert "shared code change goes first" in flat


def test_planner_prompt_asks_time_fit_and_concurrency_questions(
    tmp_path,
    monkeypatch,
) -> None:
    """A plan sized against no clock overran a one-week window sixfold; a
    Planner that is never asked what else can start emits one task a cycle.
    Both are judgement questions, not gates."""
    prompt, _ = _build_scope_prompt(tmp_path, monkeypatch)
    flat = " ".join(prompt.split())

    assert "measured throughput" in flat
    assert "first held-out number" in flat
    assert "smaller end-to-end version" in flat
    assert "independent work can start this cycle" in flat
