"""Unit tests for the Manager's stage-transition authority.

The Manager is the SOLE post-bootstrap writer of ``current_stage``: it makes its
own LLM judgment from the reviewer's feedback and the current-stage checklist,
then advances / holds / rolls back. These tests drive ``decide_stage_transition``
with a stub runner returning canned JSON verdicts, plus the strict parser.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.manager import Manager, StageTransition
from argus_skill.manager.live_view import load_live_view_decision
from argus_skill.manager.stage_decider import (
    build_stage_decision_prompt,
    fallback_empty_stage_decision,
    final_stage_completion_decision,
    parse_stage_decision,
)
from argus_skill.skills.stage_machine import (
    ChecklistItem,
    ChecklistLoadState,
    StageChecklistContract,
    advance_stage,
    complete_final_stage,
    resolve_stage_checklist_contract,
)
from argus_skill.skills.vertical_select import persist_vertical


class _Result:
    """Minimal RunnerResult shape (last_agent_message + exit_code)."""

    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.exit_code = 0


class _StubRunner:
    """A runner whose run_exec returns a fixed JSON verdict."""

    def __init__(self, verdict: dict | str) -> None:
        self._text = verdict if isinstance(verdict, str) else json.dumps(verdict)
        self.calls = 0
        self.last_prompt = ""
        self.last_options = None

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        self.calls += 1
        self.last_prompt = prompt
        self.last_options = options
        return _Result(self._text)


class _BoomRunner:
    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        raise RuntimeError("backend down")


def _review(
    status: str = "done",
    *,
    checklist: list[dict] | None = None,
    forward_progress: bool | None = True,
    scope: str = "",
    scientific_decision: str = "",
):
    """A minimal ReviewDecision-shaped object the decider reads."""
    from argus_skill.core.models import ReviewDecision

    report = {"headline": "done", "blocker": ""}
    if forward_progress is not None:
        report["forward_progress"] = forward_progress
    return ReviewDecision(
        status=status,  # type: ignore[arg-type]
        reason="checklist satisfied",
        next_action="advance",
        checklist=(
            checklist
            if checklist is not None
            else [
                {
                    "item": "research.first_score_plan",
                    "satisfied": True,
                    "evidence": "X",
                }
            ]
        ),
        scope=scope,
        scientific_decision=scientific_decision,
        planner_report=report,
    )


def _project(tmp_path: Path, *, current: str) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": current}), encoding="utf-8"
    )
    return tmp_path


def _submission_project(tmp_path: Path) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps(
            {
                "current_stage": "submission",
                "stages": {"submission": {"status": "pending"}},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _read_stage(root: Path) -> str:
    return json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )["current_stage"]


def test_prompt_treats_engineer_waiver_as_manager_judgment_input() -> None:
    review = _review(checklist=[])
    review.review_source = "engineer_self_review"
    review.verification_summary = "pytest: 12 passed; artifact hashes verified"

    prompt = build_stage_decision_prompt(
        current_stage="research",
        next_stage="plan",
        earlier_stages=[],
        checklist_md="- [ ] research evidence is complete",
        review=review,
    )

    assert "source: engineer_self_review" in prompt
    assert "pytest: 12 passed; artifact hashes verified" not in prompt
    assert "inspect CHECKPOINT.md and the project artifacts" in prompt
    assert "empty Reviewer checklist is therefore expected" in prompt
    assert "MAY ADVANCE" in prompt


def test_prompt_routes_scope_change_through_manager_hold_or_rollback() -> None:
    review = _review(checklist=[])
    review.status = "replan_requested"
    review.next_action = "Authorize a scoped correctness-repair mission."
    review.planner_report = {
        "forward_progress": True,
        "plan_signal": "reconsider",
        "evidence_files": [],
    }
    review.harness_control = {
        "mission_scope_change_required": True,
        "stage_reconciliation_required": True,
        "reason": "Current mission non-goals forbid the repair.",
    }

    prompt = build_stage_decision_prompt(
        current_stage="baseline",
        next_stage="optimize",
        earlier_stages=("scope", "environment"),
        checklist_md="- [ ] baseline is complete",
        review=review,
    )

    assert "Mission-scope arbitration" in prompt
    assert "Reviewer advice is not authorization" in prompt
    assert "HOLD the current stage" in prompt
    assert "mission_scope_change_required: True" in prompt


def test_prompt_makes_value_not_integrity_the_stage_objective() -> None:
    prompt = build_stage_decision_prompt(
        current_stage="run",
        next_stage="analysis",
        earlier_stages=("research", "plan"),
        checklist_md="- [x] honest benchmark report",
        review=_review(scientific_decision="stop"),
    )

    assert "scientific_decision: stop" in prompt
    assert "honest reporting are hard constraints" in prompt
    assert "HOLD for a replacement plan" in prompt
    assert "weak proxy" in prompt


def test_empty_manager_output_cannot_advance_reviewer_stop() -> None:
    decision = fallback_empty_stage_decision(
        _review(scientific_decision="stop"),
        current_stage="run",
        stage_order=("research", "run", "analysis"),
    )

    assert decision.action == "hold"
    assert decision.diagnostic == "empty_output_scientific_stop"


def test_final_stage_no_go_cannot_complete() -> None:
    decision = final_stage_completion_decision(
        _review(
            scientific_decision="stop",
            checklist=[
                {"item": "review.required", "satisfied": True, "evidence": "checked"}
            ],
        ),
        current_stage="review",
        stage_order=("scope", "review"),
        checklist_contract=_checklist_contract(ChecklistLoadState.LOADED),
    )

    assert decision is None


def _read_stage_status(root: Path, stage: str) -> str:
    return json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )["stages"][stage]["status"]


def _checklist_contract(
    state: ChecklistLoadState,
    *,
    optional: bool = False,
) -> StageChecklistContract:
    items = (
        ChecklistItem("review.required", "Verify the result.", "REVIEW.md"),
    ) if state is ChecklistLoadState.LOADED else ()
    return StageChecklistContract(
        stage="review",
        state=state,
        checklist_optional=optional,
        items=items,
    )


@pytest.mark.parametrize(
    "state",
    [ChecklistLoadState.NOT_LOADED, ChecklistLoadState.EMPTY],
)
def test_required_checklist_cannot_complete_when_unloaded_or_empty(
    state: ChecklistLoadState,
) -> None:
    decision = final_stage_completion_decision(
        _review(checklist=[]),
        current_stage="review",
        stage_order=("scope", "review"),
        checklist_contract=_checklist_contract(state),
    )

    assert decision is None


def test_loaded_required_checklist_requires_every_declared_item() -> None:
    contract = _checklist_contract(ChecklistLoadState.LOADED)

    missing = final_stage_completion_decision(
        _review(checklist=[]),
        current_stage="review",
        stage_order=("scope", "review"),
        checklist_contract=contract,
    )
    complete = final_stage_completion_decision(
        _review(checklist=[{
            "item": "review.required",
            "satisfied": True,
            "evidence": "REVIEW.md line 4",
        }]),
        current_stage="review",
        stage_order=("scope", "review"),
        checklist_contract=contract,
    )

    assert missing is None
    assert complete is not None
    assert complete.action == "complete"


def test_explicit_optional_checklist_can_complete_without_items() -> None:
    decision = final_stage_completion_decision(
        _review(checklist=[]),
        current_stage="review",
        stage_order=("scope", "review"),
        checklist_contract=_checklist_contract(
            ChecklistLoadState.NOT_APPLICABLE,
            optional=True,
        ),
    )

    assert decision is not None
    assert decision.action == "complete"


def test_manager_holds_empty_required_checklist_before_stage_backend(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    state_path = tmp_path / "research" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "solve"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    # Tombstone every active Math solve seed so the effective list is truly
    # empty under seed-plus-override semantics (stage present + all seeds
    # disabled → store_items_for_stage returns () → state=EMPTY).
    from argus_skill.skills.checklist_store import seed_items_for

    solve_seed_ids = [item.id for item in seed_items_for(tmp_path, "solve")]
    (tmp_path / "research" / "CHECKLISTS.json").write_text(
        json.dumps({
            "revision": 1,
            "vertical": "math",
            "stages": {"solve": []},
            "disabled": {"solve": solve_seed_ids},
        }),
        encoding="utf-8",
    )
    backend = _StubRunner({
        "action": "advance",
        "target_stage": "review",
        "reason": "empty checklist looked complete",
    })

    transition = Manager(
        project_root=tmp_path,
        runner=backend,
    ).decide_stage_transition(
        review=_review(checklist=[]),
        project_root=tmp_path,
    )

    assert transition.action == "hold"
    assert transition.diagnostic == "required_checklist_empty"
    assert backend.calls == 0


# --- decide_stage_transition: writes -------------------------------------


def test_decide_advance_writes_state(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert isinstance(st, StageTransition)
    assert st.action == "advance"
    assert _read_stage(root) == "plan"
    # The self-reported confidence field is gone from the verdict dataclass.
    import dataclasses
    assert "confidence" not in [f.name for f in dataclasses.fields(StageTransition)]


def test_decide_advance_rejected_after_reviewer_stop(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "honest result"}
    ))

    transition = mgr.decide_stage_transition(
        review=_review(scientific_decision="stop"),
        project_root=root,
    )

    assert transition.action == "hold"
    assert transition.diagnostic == "scientific_stop_advance_rejected"
    assert _read_stage(root) == "research"


@pytest.mark.parametrize("target", ["`plan`", "plan stage"])
def test_decide_advance_accepts_harmless_target_formatting(
    tmp_path: Path, target: str
) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "target_stage": target, "reason": "done"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "advance"
    assert st.target_stage == "plan"
    assert st.diagnostic == "normalized_target_stage"
    assert _read_stage(root) == "plan"


def test_decide_advance_infers_unique_next_stage_when_target_omitted(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "reason": "done"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "advance"
    assert st.target_stage == "plan"
    assert st.diagnostic == "inferred_next_stage"
    assert _read_stage(root) == "plan"


def test_decide_hold_writes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "hold", "target_stage": "research", "reason": "more work"}
    ))
    st = mgr.decide_stage_transition(review=_review(status="continue"), project_root=root)
    assert st.action == "hold"
    assert st.diagnostic == "intentional_hold"
    assert _read_stage(root) == "research"  # untouched


def test_parse_wait_reconciliation_hold_can_request_replanning() -> None:
    decision = parse_stage_decision(
        json.dumps({
            "action": "hold",
            "target_stage": "research",
            "reason": "new mechanism work is now authorized",
            "resolves_wait": True,
        }),
        current_stage="research",
        stage_order=["research", "plan"],
    )

    assert decision.action == "hold"
    assert decision.target_stage == "research"
    assert decision.resolves_wait is True


def test_wait_reconciliation_prompt_explains_resolves_wait() -> None:
    planner_verdict = SimpleNamespace(
        waiting=True,
        waiting_reason="manager authorization required",
        reason="manager authorization required",
        waiting_contract=SimpleNamespace(
            recheck_condition="Manager explicitly authorizes a new mechanism",
            operator_action_required=True,
        ),
    )

    prompt = build_stage_decision_prompt(
        current_stage="research",
        next_stage="plan",
        earlier_stages=[],
        checklist_md="- [ ] research.signal",
        review=None,
        planner_verdict=planner_verdict,
    )

    assert "Planner-wait reconciliation" in prompt
    assert "resolves_wait=true" in prompt
    assert '"resolves_wait": true|false' in prompt
    assert "cannot create or expand operator authorization" in prompt
    assert "set `resolves_wait=false`" in prompt


def test_decide_stage_refreshes_manager_owned_live_view(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "gpt-5.5")
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "gpt-5.6-sol")
    root = _project(tmp_path, current="research")
    backend = _StubRunner({
        "action": "hold",
        "target_stage": "research",
        "reason": "more work",
        "live_view": {
            "title": "Manager view",
            "reason": "Polished intermediate result",
            "paths": [".argus/live/current.md"],
        },
        "presentations": [{
            "path": ".argus/live/current.md",
            "content": "# Current result\n\n## Current node\n- Solve bridge — `running`\n\n## Verified progress\n- Scope accepted\n\n## Current blocker\n- Uniform bridge remains open.\n\n## Next action\n- Test the bridge.\n",
        }],
    })
    mgr = Manager(project_root=root, runner=backend)

    st = mgr.decide_stage_transition(
        review=_review(status="continue"),
        project_root=root,
    )

    assert st.action == "hold"
    view = load_live_view_decision(root)
    assert view is not None
    assert view.paths == (".argus/live/current.md",)
    assert (root / ".argus" / "live" / "current.md").exists()
    assert backend.last_options.working_dir == str(root)
    assert backend.last_options.sandbox_mode == "read-only"
    assert backend.last_options.dangerous_yolo is False
    assert backend.last_options.model == "gpt-5.5"
    assert "MANAGER ownership" in backend.last_prompt
    assert "Do not assign" in backend.last_prompt


def test_decide_rollback_writes_state(tmp_path: Path) -> None:
    root = _project(tmp_path, current="run")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "rollback", "target_stage": "benchmark", "reason": "stub evaluator"}
    ))
    st = mgr.decide_stage_transition(review=_review(status="continue"), project_root=root)
    assert st.action == "rollback"
    assert _read_stage(root) == "benchmark"


def test_changed_ground_truth_can_still_support_real_rollback(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, current="research")
    ground_truth = root / "research" / "GROUND_TRUTH.md"
    ground_truth.write_text("certified evidence\n", encoding="utf-8")
    advance_stage(
        root,
        target_stage="plan",
        reason="research evidence certified",
        advanced_by="manager",
    )
    ground_truth.write_text("newly discovered missing evidence\n", encoding="utf-8")

    mgr = Manager(project_root=root, runner=_StubRunner({
        "action": "rollback",
        "target_stage": "research",
        "reason": "GROUND_TRUTH now records newly missing research evidence",
    }))
    transition = mgr.decide_stage_transition(
        review=_review(status="continue"),
        project_root=root,
    )

    assert transition.action == "rollback"
    assert _read_stage(root) == "research"


def test_unchanged_ground_truth_does_not_mask_independent_rollback_reason(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, current="research")
    ground_truth = root / "research" / "GROUND_TRUTH.md"
    ground_truth.write_text("Observed pipeline stage: research\n", encoding="utf-8")
    advance_stage(
        root,
        target_stage="plan",
        reason="research evidence certified",
    )

    mgr = Manager(project_root=root, runner=_StubRunner({
        "action": "rollback",
        "target_stage": "research",
        "reason": (
            "GROUND_TRUTH stage differs from PIPELINE_STATE, and the plan has an "
            "invalid contract that independently requires research repair"
        ),
    }))
    transition = mgr.decide_stage_transition(
        review=_review(status="continue"),
        project_root=root,
    )

    assert transition.action == "rollback"
    assert _read_stage(root) == "research"


@pytest.mark.parametrize("target", ["`benchmark`", "benchmark stage"])
def test_decide_rollback_accepts_harmless_target_formatting(
    tmp_path: Path, target: str
) -> None:
    root = _project(tmp_path, current="run")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "rollback", "target_stage": target, "reason": "stub evaluator"}
    ))
    st = mgr.decide_stage_transition(review=_review(status="continue"), project_root=root)
    assert st.action == "rollback"
    assert st.target_stage == "benchmark"
    assert st.diagnostic == "normalized_target_stage"
    assert _read_stage(root) == "benchmark"


# --- decide_stage_transition: fail-safe HOLDs ----------------------------


def test_decide_no_runner_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=None).decide_stage_transition(
        review=_review(), project_root=root
    )
    assert st.action == "hold"
    assert st.source == "no_runner_hold"
    assert _read_stage(root) == "research"


def test_decide_review_none_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=_StubRunner({"action": "advance", "target_stage": "plan", "reason": "x"})).decide_stage_transition(
        review=None, project_root=root
    )
    assert st.action == "hold"
    assert st.source == "no_review_hold"
    assert _read_stage(root) == "research"


def test_open_ended_terminal_reconciliation_can_rollback(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "review",
            "vertical": "math",
            "stages": {
                "scope": {"status": "done"},
                "solve": {"status": "done"},
                "review": {"status": "done"},
            },
        }),
        encoding="utf-8",
    )
    backend = _StubRunner({
        "action": "rollback",
        "target_stage": "solve",
        "reason": "objective remains unresolved",
    })
    planner_verdict = SimpleNamespace(
        project_done=False,
        reason="proof not found; another solve direction remains",
        new_tasks=[],
    )

    st = Manager(project_root=tmp_path, runner=backend).decide_stage_transition(
        review=None,
        planner_verdict=planner_verdict,
        project_root=tmp_path,
        open_ended=True,
        continuous_objective="Continue until a complete proof or counterexample.",
    )

    assert st.action == "rollback"
    assert st.target_stage == "solve"
    assert _read_stage(tmp_path) == "solve"
    assert "Open-ended campaign contract" in backend.last_prompt
    assert "Continue until a complete proof" in backend.last_prompt


def test_open_ended_nonterminal_planner_wait_can_rollback(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "nanochat")
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "measure",
            "vertical": "nanochat",
            "stages": {
                "setup": {"status": "done"},
                "optimize": {"status": "done"},
                "measure": {"status": "in_progress"},
                "report": {"status": "pending"},
            },
        }),
        encoding="utf-8",
    )
    backend = _StubRunner({
        "action": "rollback",
        "target_stage": "optimize",
        "reason": "the required profile belongs to optimize",
    })
    planner_verdict = SimpleNamespace(
        project_done=False,
        waiting=True,
        reason="measure cannot dispatch the prerequisite optimize profile",
        new_tasks=[],
    )

    st = Manager(project_root=tmp_path, runner=backend).decide_stage_transition(
        review=None,
        planner_verdict=planner_verdict,
        project_root=tmp_path,
        open_ended=True,
        continuous_objective="Keep improving nanochat.",
    )

    assert st.action == "rollback"
    assert st.target_stage == "optimize"
    assert _read_stage(tmp_path) == "optimize"
    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["stages"]["optimize"]["status"] == "in_progress"
    assert "Planner-wait reconciliation" in backend.last_prompt


def test_planner_wait_cannot_advance_without_reviewer_evidence(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "nanochat")
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "measure",
            "vertical": "nanochat",
            "stages": {
                "setup": {"status": "done"},
                "optimize": {"status": "done"},
                "measure": {"status": "in_progress"},
                "report": {"status": "pending"},
            },
        }),
        encoding="utf-8",
    )
    planner_verdict = SimpleNamespace(
        project_done=False,
        waiting=True,
        reason="external scorer is still unavailable",
        new_tasks=[],
    )
    backend = _StubRunner({
        "action": "advance",
        "target_stage": "report",
        "reason": "skip the blocked measurement",
    })

    st = Manager(project_root=tmp_path, runner=backend).decide_stage_transition(
        review=None,
        planner_verdict=planner_verdict,
        project_root=tmp_path,
        open_ended=True,
        continuous_objective="Keep improving nanochat.",
    )

    assert st.action == "hold"
    assert st.diagnostic == "planner_wait_advance_rejected"
    assert _read_stage(tmp_path) == "measure"


def test_empty_manager_output_during_planner_wait_holds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persist_vertical(tmp_path, "nanochat")
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "measure",
            "vertical": "nanochat",
            "stages": {
                "setup": {"status": "done"},
                "optimize": {"status": "done"},
                "measure": {"status": "in_progress"},
                "report": {"status": "pending"},
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("argus_skill.manager._stage_ops.time.sleep", lambda _seconds: None)
    planner_verdict = SimpleNamespace(
        project_done=False,
        waiting=True,
        reason="external scorer is still unavailable",
        new_tasks=[],
    )

    st = Manager(project_root=tmp_path, runner=_StubRunner("")).decide_stage_transition(
        review=None,
        planner_verdict=planner_verdict,
        project_root=tmp_path,
        open_ended=True,
        continuous_objective="Keep improving nanochat.",
    )

    assert st.action == "hold"
    assert _read_stage(tmp_path) == "measure"


def test_open_ended_final_review_is_not_forced_complete(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "review",
            "vertical": "math",
            "stages": {"review": {"status": "pending"}},
        }),
        encoding="utf-8",
    )
    mgr = Manager(project_root=tmp_path, runner=_StubRunner({
        "action": "rollback",
        "target_stage": "solve",
        "reason": "review found the original problem unresolved",
    }))

    st = mgr.decide_stage_transition(
        review=_review(),
        project_root=tmp_path,
        open_ended=True,
        continuous_objective="Keep solving the open problem.",
    )

    assert st.action == "rollback"
    assert _read_stage(tmp_path) == "solve"


def test_open_ended_final_review_hold_certifies_goal_checkpoint(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "review",
            "vertical": "math",
            "stages": {"review": {"status": "pending"}},
        }),
        encoding="utf-8",
    )
    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)
    review = _review(checklist=[
        {
            "item": item.id,
            "satisfied": True,
            "evidence": f"verified {item.evidence_hint}",
        }
        for item in contract.items
    ])
    mgr = Manager(project_root=tmp_path, runner=_StubRunner({
        "action": "hold",
        "target_stage": "review",
        "reason": "the requested proof is complete and independently verified",
    }))

    st = mgr.decide_stage_transition(
        review=review,
        project_root=tmp_path,
        open_ended=True,
        continuous_objective="Continue until the theorem is proved.",
    )

    assert st.action == "complete"
    from argus_skill.skills.vertical_select import (
        vertical_has_current_completion_certificate,
        vertical_reached_own_terminal_stage,
    )

    assert vertical_reached_own_terminal_stage(tmp_path, "math") is True
    assert vertical_has_current_completion_certificate(tmp_path, "math") is True

    from argus_skill.skills import checklist_store

    checklist_store.apply_checklist_ops(tmp_path, [{
        "op": "add",
        "stage": "review",
        "id": "review.new-material-requirement",
        "statement": "A newly discovered material requirement is satisfied.",
        "evidence_hint": "direct evidence for the new requirement",
    }])
    assert vertical_reached_own_terminal_stage(tmp_path, "math") is True
    assert vertical_has_current_completion_certificate(tmp_path, "math") is False


def test_math_completion_fails_closed_when_fingerprint_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persist_vertical(tmp_path, "math")
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "current_stage": "review",
            "vertical": "math",
            "stages": {"review": {"status": "pending"}},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "argus_skill.skills.stage_machine.completion_contract_fingerprint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fingerprint failed")
        ),
    )

    with pytest.raises(ValueError, match="fingerprint unavailable"):
        complete_final_stage(tmp_path, reason="should not complete")

    assert _read_stage_status(tmp_path, "review") == "pending"


def test_open_ended_prompt_explains_terminal_checkpoint_semantics() -> None:
    text = build_stage_decision_prompt(
        current_stage="review",
        next_stage="",
        earlier_stages=("scope", "solve"),
        checklist_md="done",
        review=_review(),
        open_ended=True,
        continuous_objective="Keep solving.",
    )

    assert "final-stage checkpoint does not complete" in text
    assert "ROLL BACK" in text


def test_decide_llm_error_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=_BoomRunner()).decide_stage_transition(
        review=_review(), project_root=root
    )
    assert st.action == "hold"
    assert st.source == "failsafe_hold"
    assert _read_stage(root) == "research"


def test_decide_illegal_skip_target_holds(tmp_path: Path) -> None:
    # advance to a non-immediate stage → parser fails closed to HOLD, no write.
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "target_stage": "run", "reason": "skip"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "hold"
    assert st.diagnostic == "illegal_advance_target"
    assert _read_stage(root) == "research"


def test_decide_garbage_output_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner("not json at all"))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "hold"
    assert st.diagnostic == "no_json_object"
    assert _read_stage(root) == "research"


def test_decide_persistent_empty_done_satisfied_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("argus_skill.manager._stage_ops.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _StubRunner("")
    mgr = Manager(project_root=root, runner=backend)
    contract = resolve_stage_checklist_contract("research", project_root=root)
    checklist = [
        {"item": item.id, "satisfied": True, "evidence": item.evidence_hint}
        for item in contract.items
    ]
    st = mgr.decide_stage_transition(
        review=_review(checklist=checklist),
        project_root=root,
    )
    assert backend.calls == 3
    assert st.action == "advance"
    assert st.target_stage == "plan"
    assert st.diagnostic == "empty_output_certified_advance"
    assert _read_stage(root) == "plan"


def test_decide_persistent_empty_done_satisfied_completes_final_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("argus_skill.manager._stage_ops.time.sleep", lambda *_a, **_k: None)
    root = _submission_project(tmp_path)
    backend = _StubRunner("")
    mgr = Manager(project_root=root, runner=backend)
    from argus_skill.skills.stage_machine import (
        resolve_stage_checklist_contract,
    )

    contract = resolve_stage_checklist_contract(
        "submission",
        project_root=root,
    )
    review = _review(checklist=[
        {
            "item": item.id,
            "satisfied": True,
            "evidence": f"verified {item.evidence_hint}",
        }
        for item in contract.items
    ])

    st = mgr.decide_stage_transition(review=review, project_root=root)

    assert backend.calls == 3
    assert st.action == "complete"
    assert st.target_stage == "submission"
    assert st.diagnostic == "empty_output_no_next_stage"
    assert _read_stage_status(root, "submission") == "done"


def test_bounded_final_stage_completion_allows_empty_checklist() -> None:
    decision = final_stage_completion_decision(
        _review(checklist=[]),
        current_stage="review",
        stage_order=("scope", "review"),
        mission_scope="bounded",
    )

    assert decision is not None
    assert decision.action == "complete"


def test_final_submission_completion_requires_checklist() -> None:
    decision = final_stage_completion_decision(
        _review(checklist=[]),
        current_stage="submission",
        stage_order=("review", "submission"),
        mission_scope="final_submission",
    )

    assert decision is None


def test_decide_persistent_empty_unsatisfied_checklist_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("argus_skill.manager._stage_ops.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _StubRunner("")
    review = _review(
        checklist=[
            {"item": "research.first_score_plan", "satisfied": False, "evidence": ""}
        ]
    )
    st = Manager(project_root=root, runner=backend).decide_stage_transition(
        review=review, project_root=root
    )
    assert st.action == "hold"
    assert st.diagnostic == "empty_output_unsatisfied_checklist"
    assert _read_stage(root) == "research"


@pytest.mark.parametrize("status", ["continue", "blocked"])
def test_decide_persistent_empty_reviewer_not_done_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr("argus_skill.manager._stage_ops.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=_StubRunner("")).decide_stage_transition(
        review=_review(status=status), project_root=root
    )
    assert st.action == "hold"
    assert st.diagnostic == "empty_output_review_not_done"
    assert _read_stage(root) == "research"


# --- parse_stage_decision (pure, fail-closed) ----------------------------

ORDER = ("research", "plan", "benchmark", "run", "analysis", "draft", "review", "submission")


def test_parse_advance_immediate_ok() -> None:
    d = parse_stage_decision(
        '{"action":"advance","target_stage":"plan","reason":"ok"}',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance" and d.target_stage == "plan"
    assert d.diagnostic == "valid_target"
    import dataclasses
    assert "confidence" not in [f.name for f in dataclasses.fields(d)]


@pytest.mark.parametrize("target", ["`plan`", "plan stage"])
def test_parse_advance_harmless_target_formatting_ok(target: str) -> None:
    d = parse_stage_decision(
        json.dumps({"action": "advance", "target_stage": target, "reason": "ok"}),
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance"
    assert d.target_stage == "plan"
    assert d.diagnostic == "normalized_target_stage"


def test_parse_advance_missing_target_infers_unique_next_stage() -> None:
    d = parse_stage_decision(
        '{"action":"advance","reason":"ok"}',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance"
    assert d.target_stage == "plan"
    assert d.diagnostic == "inferred_next_stage"


def test_parse_advance_skip_holds() -> None:
    d = parse_stage_decision(
        '{"action":"advance","target_stage":"benchmark","reason":"skip"}',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "illegal_advance_target"


def test_parse_rollback_must_be_earlier() -> None:
    ok = parse_stage_decision(
        '{"action":"rollback","target_stage":"plan"}',
        current_stage="run", stage_order=ORDER,
    )
    assert ok.action == "rollback" and ok.target_stage == "plan"
    bad = parse_stage_decision(
        '{"action":"rollback","target_stage":"draft"}',  # later than run
        current_stage="run", stage_order=ORDER,
    )
    assert bad.action == "hold"
    assert bad.diagnostic == "illegal_rollback_target"


@pytest.mark.parametrize("target", ["`benchmark`", "benchmark stage"])
def test_parse_rollback_harmless_target_formatting_ok(target: str) -> None:
    d = parse_stage_decision(
        json.dumps({"action": "rollback", "target_stage": target, "reason": "ok"}),
        current_stage="run",
        stage_order=("research", "plan", "benchmark", "run"),
    )
    assert d.action == "rollback"
    assert d.target_stage == "benchmark"
    assert d.diagnostic == "normalized_target_stage"


@pytest.mark.parametrize(
    ("target", "diagnostic"),
    [
        ("", "missing_rollback_target"),
        ("run", "illegal_rollback_target"),
        ("analysis", "illegal_rollback_target"),
        ("unknown", "illegal_rollback_target"),
    ],
)
def test_parse_rollback_illegal_targets_hold(target: str, diagnostic: str) -> None:
    d = parse_stage_decision(
        json.dumps({"action": "rollback", "target_stage": target, "reason": "bad"}),
        current_stage="run",
        stage_order=("research", "plan", "benchmark", "run", "analysis"),
    )
    assert d.action == "hold"
    assert d.target_stage == "run"
    assert d.diagnostic == diagnostic


def test_parse_rollback_missing_target_holds() -> None:
    d = parse_stage_decision(
        '{"action":"rollback","reason":"unclear"}',
        current_stage="run", stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "missing_rollback_target"


def test_parse_json_in_code_fence() -> None:
    d = parse_stage_decision(
        '```json\n{"action":"advance","target_stage":"plan"}\n```',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance"


def test_parse_unknown_action_holds() -> None:
    d = parse_stage_decision('{"action":"yolo"}', current_stage="research", stage_order=ORDER)
    assert d.action == "hold"
    assert d.diagnostic == "unknown_action"


def test_fallback_empty_stage_decision_missing_evidence_holds() -> None:
    d = fallback_empty_stage_decision(
        _review(checklist=[{"item": "x", "satisfied": True, "evidence": " "}]),
        current_stage="research",
        stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "empty_output_missing_checklist_evidence"


def test_fallback_empty_stage_decision_unknown_current_holds() -> None:
    d = fallback_empty_stage_decision(
        _review(),
        current_stage="unknown",
        stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "empty_output_unknown_current_stage"


def test_fallback_empty_stage_decision_requires_every_contract_item() -> None:
    contract = StageChecklistContract(
        stage="research",
        state=ChecklistLoadState.LOADED,
        checklist_optional=False,
        items=(
            ChecklistItem("research.brief", "brief", "brief.md"),
            ChecklistItem("research.literature", "literature", "ledger.json"),
        ),
    )
    d = fallback_empty_stage_decision(
        _review(
            checklist=[
                {
                    "item": "research.brief",
                    "satisfied": True,
                    "evidence": "brief.md",
                }
            ]
        ),
        current_stage="research",
        stage_order=ORDER,
        checklist_contract=contract,
    )

    assert d.action == "hold"
    assert d.diagnostic == "empty_output_missing_required_checklist_items"


# --- F3: the manager-stage codex turn is metered ----------------------------


class _MeteredResult:
    """A RunnerResult shape that also carries token usage."""

    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.exit_code = 0
        self.input_tokens = 4_000
        self.cached_input_tokens = 1_000
        self.output_tokens = 300


class _MeteredRunner:
    def __init__(self, verdict: dict) -> None:
        self._text = json.dumps(verdict)

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        return _MeteredResult(self._text)


def test_decide_stage_transition_emits_codex_util_cost(tmp_path: Path) -> None:
    """The manager-stage codex turn was invisible to the cost sink; it now emits
    a ``codex.util.completed`` event carrying its per-turn token usage so the
    per-mission number and the daily cap account for it (F3 PART B)."""
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_MeteredRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done"}
    ))
    events: list[dict] = []
    st = mgr.decide_stage_transition(
        review=_review(), project_root=root, on_event=events.append,
    )
    assert st.action == "advance"  # the decision itself is unaffected

    util = [e for e in events if e.get("type") == "codex.util.completed"]
    assert len(util) == 1
    ev = util[0]
    assert ev["agent_layer"] == "manager"
    assert ev["run_label"] == "manager-stage"
    assert ev["input_tokens"] == 4_000
    assert ev["cached_input_tokens"] == 1_000
    assert ev["output_tokens"] == 300
    assert ev["usage_scope"] == "delta"


def test_decide_stage_transition_metering_is_failsoft_without_on_event(
    tmp_path: Path,
) -> None:
    """No ``on_event`` → no metering, but the decision still proceeds normally."""
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_MeteredRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "advance"
    assert _read_stage(root) == "plan"
