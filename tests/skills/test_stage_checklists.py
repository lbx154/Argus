"""Tests for the stage-aware reviewer checklist module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_machine import (
    current_stage,
    format_full_pipeline_checklist,
    format_stage_checklist,
)
from argus_skill.verticals.research.stages import (
    CANONICAL_STAGE_ORDER,
    STAGE_CHECKLISTS,
    get_stage_checklist,
    list_stages,
)


@pytest.fixture(autouse=True)
def _isolate_project_vertical_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "research")
    monkeypatch.setenv("ARGUS_SKILL_VENUE", "EMNLP")
    monkeypatch.chdir(tmp_path)


def test_canonical_stage_order_covers_eight_stages() -> None:
    assert CANONICAL_STAGE_ORDER == (
        "research",
        "plan",
        "benchmark",
        "run",
        "analysis",
        "draft",
        "review",
        "submission",
    )
    assert list_stages() == CANONICAL_STAGE_ORDER


def test_every_canonical_stage_has_a_checklist() -> None:
    """No stage may ship without at least one checklist item; otherwise the
    reviewer prompt collapses to an empty body for that stage.
    """

    for stage in CANONICAL_STAGE_ORDER:
        items = get_stage_checklist(stage)
        assert items, f"stage {stage!r} has no checklist items"
        for item in items:
            assert item.id.startswith(f"{stage}.")
            assert item.statement
            assert item.evidence_hint


def test_format_stage_checklist_engineer_framing() -> None:
    text = format_stage_checklist("research", role="engineer")
    assert "## Stage checklist (research)" in text
    assert "L2 reviewer will tick these items" in text
    assert "research.literature" in text
    # No retired CLI command leaks into the prompt.
    assert "validate-full-emnlp" not in text
    assert "validate-grounding" not in text


def test_format_stage_checklist_reviewer_framing() -> None:
    text = format_stage_checklist("draft", role="reviewer")
    assert "## Stage checklist (draft)" in text
    assert "You are the L2 reviewer" in text
    assert "Do not run any `validate-*` shell command" in text


def test_bounded_reviewer_only_gates_on_mission_relevant_items() -> None:
    text = format_stage_checklist("research", role="reviewer", scope="bounded")

    assert "bounded mission" in text
    assert "only the checklist items materially touched by this mission" in text
    assert "do not use them to keep this mission running" in text
    assert "Manager separately keeps the project stage on HOLD" in text
    assert "reply `done` only when every item is satisfied" not in text.lower()
    assert "research.literature" in text


def test_plan_benchmark_checklist_supports_clinical_mechanism_projects() -> None:
    text = format_stage_checklist("plan", role="reviewer")
    assert "Clinical or mechanism projects" in text
    assert "real public data source, comparator/control, and planned cohort" in text
    assert "license/access conditions" in text
    assert "planned with task_count=0" in text
    assert "must never be relabeled as benchmark tasks" in text


def test_benchmark_provenance_has_one_canonical_source() -> None:
    text = format_stage_checklist("benchmark", role="reviewer")

    assert "BENCHMARK_PROVENANCE.json (canonical)" in text
    assert "duplicate prose is not a separate gate" in text
    assert "BENCHMARK_PROVENANCE.md and .json" not in text


def test_format_stage_checklist_unknown_stage_returns_safe_block() -> None:
    text = format_stage_checklist("nonexistent_stage", role="engineer")
    # Should not crash and must fail closed for an undeclared required checklist.
    assert "Stage checklist (nonexistent_stage)" in text
    assert "Configuration error" in text
    assert "required checklist is not loaded" in text


def test_format_full_pipeline_checklist_concatenates_every_stage() -> None:
    text = format_full_pipeline_checklist(role="reviewer")
    assert "Full pipeline checklist (final submission gate)" in text
    for stage in CANONICAL_STAGE_ORDER:
        # Section header should appear for every stage.
        assert f"### {stage}" in text, f"final-gate prompt missing stage section {stage!r}"
    # No retired CLI command leaks anywhere in the final-gate block.
    for retired in (
        "validate-full-emnlp",
        "validate-grounding",
        "validate-paper-contract",
        "refresh-manifest",
    ):
        assert retired not in text, f"final-gate prompt still mentions retired tool {retired!r}"


def test_current_stage_defaults_to_research_when_state_missing(tmp_path: Path) -> None:
    assert current_stage(tmp_path) == "research"


def test_current_stage_reads_pipeline_state(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "benchmark"}),
        encoding="utf-8",
    )
    assert current_stage(tmp_path) == "benchmark"


def test_current_stage_clamps_unknown_stage_to_research(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "made_up_stage"}),
        encoding="utf-8",
    )
    assert current_stage(tmp_path) == "research"


def test_current_stage_tolerates_malformed_json(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text("{not json", encoding="utf-8")
    assert current_stage(tmp_path) == "research"


def test_stage_checklist_completeness() -> None:
    """Each stage's checklist should cover the irreducible quality bars
    that historically were enforced by Python validators. This test acts
    as a regression guard: if someone deletes an item the reviewer would
    no longer notice the missing artifact.
    """

    research_ids = {item.id for item in STAGE_CHECKLISTS["research"]}
    assert "research.literature" in research_ids
    assert "research.idea_portfolio" in research_ids
    assert "research.adversarial_selection" in research_ids
    assert "research.thesis" in research_ids

    run_ids = {item.id for item in STAGE_CHECKLISTS["run"]}
    assert "run.matrix" in run_ids
    assert "run.scale" in run_ids
    assert "run.method_diagnosis_recall" in run_ids

    analysis_ids = {item.id for item in STAGE_CHECKLISTS["analysis"]}
    assert "analysis.claims" in analysis_ids
    assert "analysis.figure1" in analysis_ids
    assert "analysis.publication_scale" in analysis_ids
    assert "analysis.thesis" in analysis_ids

    plan_ids = {item.id for item in STAGE_CHECKLISTS["plan"]}
    assert "plan.argument_organization" in plan_ids
    assert "plan.publication_scale" in plan_ids

    draft_ids = {item.id for item in STAGE_CHECKLISTS["draft"]}
    assert "draft.pdf" in draft_ids
    assert "draft.bibliography" in draft_ids
    assert "draft.research.method_faithfulness" in draft_ids

    review_ids = {item.id for item in STAGE_CHECKLISTS["review"]}
    assert "review.infrastructure" in review_ids
    assert "review.publication_value" in review_ids
    assert "review.research.method_faithfulness" not in review_ids

    submission_ids = {item.id for item in STAGE_CHECKLISTS["submission"]}
    assert "submission.upstream" in submission_ids
    assert "submission.anonymous" in submission_ids
    assert "submission.research.method_faithfulness" not in submission_ids


def test_research_late_stages_accept_manager_selected_survey_shape() -> None:
    by_stage = {
        stage: {item.id: item for item in STAGE_CHECKLISTS[stage]}
        for stage in ("draft", "review", "submission")
    }

    draft = by_stage["draft"]["draft.tex"].statement
    assert "literature review" in draft
    assert "must not invent a method or experiment section" in draft

    language = by_stage["review"]["review.language"].statement
    assert "For a literature review" in language
    assert "taxonomy" in language and "source selection" in language

    value = by_stage["review"]["review.publication_value"].statement
    assert "literature review must deliver valuable coverage" in value

    upstream = by_stage["submission"]["submission.upstream"]
    assert "explicitly skipped by a recorded Manager decision" in upstream.statement
    assert "status=skipped" in upstream.evidence_hint


# --- stage rollback ---------------------------------------------------------


def test_rollback_stage_moves_state_machine_backward(tmp_path: Path) -> None:
    from argus_skill.skills.stage_machine import rollback_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "run",
        "stages": {
            "research": {"status": "done"},
            "plan": {"status": "done"},
            "benchmark": {"status": "done"},
            "run": {"status": "in_progress"},
            "analysis": {"status": "missing"},
        },
    }), encoding="utf-8")

    rollback_stage(
        tmp_path,
        target_stage="benchmark",
        reason="benchmark evaluator returns constant 1.0; not a real scorer",
    )

    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert payload["current_stage"] == "benchmark"
    # `run` was in_progress; rollback must demote it back to pending
    assert payload["stages"]["run"]["status"] == "pending"
    # LIVENESS INVARIANT: the stage we land on must be actionable. benchmark was
    # `done`; landing on a `done` stage is the deadlock (Planner can't dispatch a
    # done stage, only the Manager advances) — so the target is reopened to
    # `in_progress`.
    assert payload["stages"]["benchmark"]["status"] == "in_progress"
    assert len(payload["rollback_history"]) == 1
    entry = payload["rollback_history"][0]
    assert entry["at"].endswith("Z")
    assert "+00:00" not in entry["at"]
    assert entry["from_stage"] == "run"
    assert entry["to_stage"] == "benchmark"
    assert "constant 1.0" in entry["reason"]


def test_rollback_stage_rejects_forward_or_same_target(tmp_path: Path) -> None:
    import pytest as _pytest

    from argus_skill.skills.stage_machine import rollback_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "plan",
    }), encoding="utf-8")

    with _pytest.raises(ValueError):
        rollback_stage(tmp_path, target_stage="plan", reason="self-rollback")
    with _pytest.raises(ValueError):
        rollback_stage(tmp_path, target_stage="benchmark", reason="forward")
    with _pytest.raises(ValueError):
        rollback_stage(tmp_path, target_stage="nonsense", reason="bad name")


def test_rollback_stage_appends_history_across_calls(tmp_path: Path) -> None:
    from argus_skill.skills.stage_machine import rollback_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "draft",
        "stages": {s: {"status": "done"} for s in (
            "research", "plan", "benchmark", "run", "analysis", "draft",
        )},
    }), encoding="utf-8")

    rollback_stage(tmp_path, target_stage="plan", reason="infra choice missing")
    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert payload["current_stage"] == "plan"
    # benchmark/run/analysis/draft demoted back to pending
    for downgraded in ("benchmark", "run", "analysis", "draft"):
        assert payload["stages"][downgraded]["status"] == "pending"

    # A subsequent rollback (e.g. after re-advancing to benchmark and
    # discovering another upstream gap) must accumulate, not overwrite.
    payload["current_stage"] = "benchmark"
    payload["stages"]["benchmark"] = {"status": "in_progress"}
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps(payload), encoding="utf-8")
    rollback_stage(tmp_path, target_stage="research", reason="literature gap")
    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert len(payload["rollback_history"]) == 2
    assert payload["current_stage"] == "research"


def test_rollback_onto_completed_stage_reopens_it_no_deadlock(tmp_path: Path) -> None:
    """LIVENESS INVARIANT regression: rolling back onto an already-``done`` stage
    must reopen it, never land the machine on a ``done`` ``current_stage``.

    Reproduces the open-ended reconcile deadlock: every pipeline stage is
    ``done`` and the Manager rolls the terminal stage back to keep working. If
    the target stays ``done``, ``current_stage`` has no dispatchable work and
    only the Manager can advance -> the Planner spins forever on
    ``planner_waiting``. The harness must guarantee the landing stage is
    actionable.
    """
    from argus_skill.skills.stage_machine import rollback_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "draft",
        "stages": {s: {"status": "done"} for s in (
            "research", "plan", "benchmark", "run", "analysis", "draft",
        )},
    }), encoding="utf-8")

    rollback_stage(tmp_path, target_stage="plan", reason="keep iterating")

    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    cur = payload["current_stage"]
    assert cur == "plan"
    # The landing stage MUST be actionable — not the deadlocking "done".
    assert payload["stages"][cur]["status"] == "in_progress"


# --- stage advance (forward) ------------------------------------------------


def test_advance_stage_moves_forward_and_marks_previous_done(tmp_path: Path) -> None:
    from argus_skill.skills.stage_machine import advance_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "benchmark",
        "stages": {"benchmark": {"status": "in_progress"}},
    }), encoding="utf-8")
    (tmp_path / "STATUS.md").write_text(
        "# Status\n\nCurrent stage: stale-review.\n\nKeep this detail.\n",
        encoding="utf-8",
    )

    advance_stage(tmp_path, target_stage="run", reason="benchmark checklist satisfied")

    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert payload["current_stage"] == "run"
    # the stage just completed is stamped done
    assert payload["stages"]["benchmark"]["status"] == "done"
    # unified transition log records the advance
    assert len(payload["stage_history"]) == 1
    entry = payload["stage_history"][0]
    assert entry["at"].endswith("Z")
    assert "+00:00" not in entry["at"]
    assert entry["direction"] == "advance"
    assert entry["from_stage"] == "benchmark"
    assert entry["to_stage"] == "run"
    assert entry["by"] == "manager"
    # advance never touches the legacy rollback log
    assert "rollback_history" not in payload
    assert (tmp_path / "STATUS.md").read_text(encoding="utf-8") == (
        "# Status\n\nCurrent stage: run.\n\nKeep this detail.\n"
    )


def test_stage_transition_leaves_noncanonical_status_file_untouched(tmp_path: Path) -> None:
    from argus_skill.skills.stage_machine import advance_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "benchmark"}), encoding="utf-8"
    )
    status = tmp_path / "STATUS.md"
    status.write_text("# Status\n\nStage is described in prose only.\n", encoding="utf-8")

    advance_stage(tmp_path, target_stage="run", reason="benchmark complete")

    assert status.read_text(encoding="utf-8") == (
        "# Status\n\nStage is described in prose only.\n"
    )


def test_advance_stage_rejects_backward_but_records_skips(tmp_path: Path) -> None:
    import pytest as _pytest

    from argus_skill.skills.stage_machine import advance_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "benchmark",
    }), encoding="utf-8")

    with _pytest.raises(ValueError):
        advance_stage(tmp_path, target_stage="research", reason="backward")  # earlier
    advance_stage(tmp_path, target_stage="analysis", reason="run is irrelevant")
    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text())
    assert payload["current_stage"] == "analysis"
    assert payload["stages"]["benchmark"]["status"] == "done"
    assert payload["stages"]["run"] == {
        "status": "skipped",
        "skip_reason": "run is irrelevant",
        "skipped_by": "manager",
    }
    assert payload["stage_history"][-1]["skipped_stages"] == ["run"]
    with _pytest.raises(ValueError):
        advance_stage(tmp_path, target_stage="nonsense", reason="bad name")


def test_advance_stage_is_vertical_aware_speedrun(tmp_path: Path, monkeypatch) -> None:
    import pytest as _pytest

    from argus_skill.skills.stage_machine import advance_stage

    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    # speedrun order is setup -> optimize -> measure -> report
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "vertical": "speedrun",
        "current_stage": "setup",
    }), encoding="utf-8")

    advance_stage(tmp_path, target_stage="optimize", reason="setup done")
    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert payload["current_stage"] == "optimize"
    assert payload["stages"]["setup"]["status"] == "done"

    # a research stage name is not a valid speedrun stage
    with _pytest.raises(ValueError):
        advance_stage(tmp_path, target_stage="run", reason="wrong vertical")


def test_manager_early_completion_is_a_current_completion_certificate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Early completion is a real outcome — for a caller that has standing.

    This asserted the same three facts before run 13, minus the standing. It
    was, exactly, the shape that run's Engineer produced: ``complete`` at
    ``scope``, ``solve`` and ``review`` ``skipped``, certificate valid. The
    difference between that and a legitimate early close is entirely who is
    allowed to ask, so that is now what the test supplies — ``direct`` workflow
    mode, where stopping before the final stage is a real answer rather than an
    abandoned pipeline, plus the explicit argument at the primitive.

    The refusal side lives in ``tests/skills/test_stage_completion_authority.py``.
    """
    from argus_skill.skills import stage_machine
    from argus_skill.skills.vertical_select import (
        persist_vertical,
        vertical_has_current_completion_certificate,
        vertical_reached_own_terminal_stage,
    )

    persist_vertical(
        tmp_path,
        "math",
        research_target_level="exploratory",
    )
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["workflow_mode"] = "direct"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    monkeypatch.setattr(
        stage_machine,
        "_ensure_stage_completion",
        lambda *_args, **_kwargs: None,
    )

    stage_machine.complete_final_stage(
        tmp_path,
        reason="The scoped answer satisfies the operator objective.",
        allow_early_completion=True,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_stage"] == "scope"
    assert state["stages"]["scope"]["status"] == "done"
    assert state["stages"]["solve"]["status"] == "skipped"
    assert state["stages"]["review"]["status"] == "skipped"
    assert vertical_reached_own_terminal_stage(tmp_path, "math") is True
    assert vertical_has_current_completion_certificate(tmp_path, "math") is True


def test_rollback_stage_also_writes_unified_stage_history(tmp_path: Path) -> None:
    from argus_skill.skills.stage_machine import rollback_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "run",
        "stages": {"run": {"status": "in_progress"}},
    }), encoding="utf-8")

    rollback_stage(tmp_path, target_stage="benchmark", reason="stub evaluator")

    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    # legacy log preserved
    assert len(payload["rollback_history"]) == 1
    # unified log additionally records the rollback
    assert len(payload["stage_history"]) == 1
    assert payload["stage_history"][0]["direction"] == "rollback"
    assert payload["stage_history"][0]["by"] == "reviewer"


def test_rollback_reopens_a_previously_skipped_stage(tmp_path: Path) -> None:
    from argus_skill.skills.stage_machine import rollback_stage

    research_dir = tmp_path / ".argus"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "draft",
            "stages": {
                "plan": {"status": "skipped", "skip_reason": "not needed"},
                "benchmark": {"status": "skipped"},
                "run": {"status": "skipped"},
                "analysis": {"status": "skipped"},
                "draft": {"status": "in_progress"},
            },
        }),
        encoding="utf-8",
    )

    rollback_stage(tmp_path, target_stage="plan", reason="survey scope now needs planning")

    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text())
    assert payload["current_stage"] == "plan"
    assert payload["stages"]["plan"]["status"] == "in_progress"
    for stage in ("benchmark", "run", "analysis", "draft"):
        assert payload["stages"][stage]["status"] == "pending"


# --- new evaluator-authenticity items --------------------------------------


def test_benchmark_stage_checklist_demands_real_evaluator() -> None:
    items = STAGE_CHECKLISTS["benchmark"]
    by_id = {item.id: item for item in items}
    ids = set(by_id)
    assert "benchmark.evaluator_authentic" in ids
    statement = by_id["benchmark.evaluator_authentic"].statement
    assert "clinical or mechanism projects" in statement
    assert "prespecified observation-level outcome" in statement
    assert "Never invent an evaluator" in statement


def test_run_stage_checklist_demands_score_variance() -> None:
    items = STAGE_CHECKLISTS["run"]
    ids = {item.id for item in items}
    assert "run.score_variance" in ids


def test_run_stage_checklist_has_generic_method_diagnosis_recall() -> None:
    """The run stage must carry a DOMAIN-AGNOSTIC recall item that makes the
    agent consult the matched method-specific diagnosis skill before killing an
    idea on a rejected attempt — RL specifics live in the evolvable skill, not here. This
    guards against the framework re-acquiring hardcoded RL-knob prose.
    """

    items = STAGE_CHECKLISTS["run"]
    by_id = {item.id: item for item in items}
    assert "run.method_diagnosis_recall" in by_id
    item = by_id["run.method_diagnosis_recall"]
    statement = item.statement
    # Domain-agnostic: it points at a method-diagnosis SKILL, not at hardcoded
    # RL hyperparameter knobs.
    assert "diagnosis" in statement.lower()
    assert "max_completion_length" not in statement
    assert "num_generations" not in statement
    assert "current evidence is discriminating enough" in statement
    assert "trusted reference" in statement
    assert "every conceivable repair" in statement


# --- RL plan-config sanity item --------------------------------------------


def test_universal_checklist_excludes_method_specific_rl_forms() -> None:
    plan_ids = {item.id for item in STAGE_CHECKLISTS["plan"]}
    run_ids = {item.id for item in STAGE_CHECKLISTS["run"]}

    assert "plan.rl_config" not in plan_ids
    assert "plan.run_contract" not in plan_ids
    assert "run.learning_validity" not in run_ids
    assert "run.curriculum_feasibility_packet" not in run_ids
