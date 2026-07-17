"""Tests for the stage-aware reviewer checklist module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_checklists import (
    CANONICAL_STAGE_ORDER,
    STAGE_CHECKLISTS,
    current_stage,
    format_full_pipeline_checklist,
    format_stage_checklist,
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
    assert "research.go_no_go" in research_ids

    run_ids = {item.id for item in STAGE_CHECKLISTS["run"]}
    assert "run.matrix" in run_ids
    assert "run.scale" in run_ids
    assert "run.method_diagnosis_recall" in run_ids

    analysis_ids = {item.id for item in STAGE_CHECKLISTS["analysis"]}
    assert "analysis.claims" in analysis_ids

    draft_ids = {item.id for item in STAGE_CHECKLISTS["draft"]}
    assert "draft.pdf" in draft_ids
    assert "draft.bibliography" in draft_ids

    review_ids = {item.id for item in STAGE_CHECKLISTS["review"]}
    assert "review.infrastructure" in review_ids
    assert "review.placeholders" in review_ids

    submission_ids = {item.id for item in STAGE_CHECKLISTS["submission"]}
    assert "submission.upstream" in submission_ids
    assert "submission.anonymous" in submission_ids


# --- stage rollback ---------------------------------------------------------


def test_rollback_stage_moves_state_machine_backward(tmp_path: Path) -> None:
    from argus_skill.skills.stage_checklists import rollback_stage

    research_dir = tmp_path / "research"
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
    assert entry["from_stage"] == "run"
    assert entry["to_stage"] == "benchmark"
    assert "constant 1.0" in entry["reason"]


def test_rollback_stage_rejects_forward_or_same_target(tmp_path: Path) -> None:
    import pytest as _pytest

    from argus_skill.skills.stage_checklists import rollback_stage

    research_dir = tmp_path / "research"
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
    from argus_skill.skills.stage_checklists import rollback_stage

    research_dir = tmp_path / "research"
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
    from argus_skill.skills.stage_checklists import rollback_stage

    research_dir = tmp_path / "research"
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
    from argus_skill.skills.stage_checklists import advance_stage

    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "benchmark",
        "stages": {"benchmark": {"status": "in_progress"}},
    }), encoding="utf-8")

    advance_stage(tmp_path, target_stage="run", reason="benchmark checklist satisfied")

    payload = json.loads((research_dir / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert payload["current_stage"] == "run"
    # the stage just completed is stamped done
    assert payload["stages"]["benchmark"]["status"] == "done"
    # unified transition log records the advance
    assert len(payload["stage_history"]) == 1
    entry = payload["stage_history"][0]
    assert entry["direction"] == "advance"
    assert entry["from_stage"] == "benchmark"
    assert entry["to_stage"] == "run"
    assert entry["by"] == "manager"
    # advance never touches the legacy rollback log
    assert "rollback_history" not in payload


def test_advance_stage_rejects_backward_or_skip(tmp_path: Path) -> None:
    import pytest as _pytest

    from argus_skill.skills.stage_checklists import advance_stage

    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "benchmark",
    }), encoding="utf-8")

    with _pytest.raises(ValueError):
        advance_stage(tmp_path, target_stage="research", reason="backward")  # earlier
    with _pytest.raises(ValueError):
        advance_stage(tmp_path, target_stage="analysis", reason="skip over run")  # skip
    with _pytest.raises(ValueError):
        advance_stage(tmp_path, target_stage="nonsense", reason="bad name")


def test_advance_stage_is_vertical_aware_speedrun(tmp_path: Path, monkeypatch) -> None:
    import pytest as _pytest

    from argus_skill.skills.stage_checklists import advance_stage

    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    research_dir = tmp_path / "research"
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


def test_rollback_stage_also_writes_unified_stage_history(tmp_path: Path) -> None:
    from argus_skill.skills.stage_checklists import rollback_stage

    research_dir = tmp_path / "research"
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
    idea on a no-go — RL specifics live in the evolvable skill, not here. This
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
    # Still bounds the loop with the three attribution labels.
    for verdict in ("misconfigured_run", "method_failure", "infeasible_under_budget"):
        assert verdict in statement
    # Conditional so it never blocks a run with no method-specific skill.
    assert "N/A" in statement


# --- RL plan-config sanity item --------------------------------------------


def test_plan_stage_checklist_has_conditional_rl_config_item() -> None:
    """The plan stage must let the L2 reviewer tick an RL-config sanity item so
    structurally-unlearnable RL configs (e.g. group size 1, zero-variance
    reward) are caught before any GPU is spent.
    """

    items = STAGE_CHECKLISTS["plan"]
    by_id = {item.id: item for item in items}
    assert "plan.rl_config" in by_id
    item = by_id["plan.rl_config"]
    # Conditional so it never blocks a non-RL plan.
    assert "only if" in item.statement.lower()
    assert "N/A for non-RL" in item.statement
    # Names the at-a-glance failure modes.
    assert "num_generations" in item.statement
    assert "max_completion_length" in item.statement


def test_run_stage_checklist_has_learning_validity_mirror_item() -> None:
    """The run stage must carry the MIRROR of method_diagnosis_recall: a
    domain-agnostic item that forces the reviewer to justify the reward->learning
    inference (rule out memorisation / saturation / reward-hacking) before
    accepting a run as healthy. This closes the looks-healthy blind spot without
    hardcoding numeric research thresholds.
    """

    items = STAGE_CHECKLISTS["run"]
    by_id = {item.id: item for item in items}
    assert "run.learning_validity" in by_id
    statement = by_id["run.learning_validity"].statement
    # Guards the positive (looks-healthy) direction, not the dead direction.
    assert "memori" in statement.lower()
    assert "distinct" in statement.lower()
    # Treats advisory signals as facts to address, not as automatic verdicts.
    assert "low_task_diversity" in statement
    assert "variance_metric_masks_saturation" in statement
    # Domain-agnostic: points at a diagnosis SKILL, no hardcoded RL knobs / numbers.
    assert "diagnosis" in statement.lower()
    assert "num_generations" not in statement
    assert "max_completion_length" not in statement
    # Has an escape hatch so legitimately-easy/converged runs are not blanket-vetoed.
    assert "N/A" in statement


def test_plan_rl_config_item_requires_training_set_diversity() -> None:
    """The RL-config sanity item must also name training-set / distinct-task
    diversity as a learnability precondition, conditioned on the claimed
    objective so it does not blanket-fail intentional smoke / memorisation runs.
    """

    items = STAGE_CHECKLISTS["plan"]
    by_id = {item.id: item for item in items}
    statement = by_id["plan.rl_config"].statement
    assert "diversity" in statement.lower() or "distinct-task" in statement.lower()
    assert "memorisation" in statement.lower()
    # Objective-conditioned escape: tiny/smoke/memorisation runs still allowed.
    assert "smoke" in statement.lower()
