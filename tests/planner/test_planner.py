"""Unit tests for the Planner sub-agent: parsing + plan_next dispatch.

The Planner is stateless; we exercise the JSON parser end-to-end and the
plan_next dispatch path that the supervisor relies on. The historical
"critic" surface (per-iteration polish loop with Improvement records)
has been removed; if you came here looking for those tests, they were
deleted along with the dead code.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.planner import (
    Planner,
    PlannerConfig,
    TaskSpec,
    WaitingContract,
    parse_planner_text,
)
from argus_skill.skills.role_context import load_builtin_skill_text


class _FakeRunner:
    def __init__(self, *agent_messages: str, reasoning_output_tokens: int = 0) -> None:
        self._agent_messages = list(agent_messages)
        self._reasoning_output_tokens = reasoning_output_tokens
        self.calls: list[tuple[str, object]] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append((prompt, options))
        return RunnerResult(
            exit_code=0,
            agent_messages=list(self._agent_messages),
            stdout_lines=[],
            stderr_lines=[],
            thread_id=None,
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_output_tokens=self._reasoning_output_tokens,
        )


class _SequencedRunner:
    def __init__(self, *results: RunnerResult) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append({
            "prompt": prompt,
            "options": options,
            "run_label": run_label,
            "resume_thread_id": resume_thread_id,
        })
        return self.results.pop(0)


# ---------------------------------------------------------------------------
# parse_planner_text
# ---------------------------------------------------------------------------


def test_parse_planner_text_project_done_clears_tasks() -> None:
    txt = json.dumps({"project_done": True, "reason": "ok", "new_tasks": []})
    v = parse_planner_text(txt)
    assert v.project_done is True
    assert v.new_tasks == []


def test_parse_planner_text_emits_task_specs() -> None:
    txt = json.dumps({
        "project_done": False,
        "reason": "more work",
        "new_tasks": [{
            "title": "fix the loader",
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "loader crashes on empty input",
            "scope": "bounded",
            "stage_closing": True,
            "objective": "patch code/loader.py to handle empty input and add a regression test",
        }],
    })
    v = parse_planner_text(txt)
    assert v.project_done is False
    assert len(v.new_tasks) == 1
    spec = v.new_tasks[0]
    assert isinstance(spec, TaskSpec)
    assert spec.title == "fix the loader"
    assert spec.impact_score == 5
    assert spec.scope == "bounded"
    assert spec.stage_closing is True
    # Legacy planner rows remain readable; evidence becomes the fallback check.
    assert spec.acceptance_check == "loader crashes on empty input"


def test_parse_planner_text_preserves_context_packet_fields() -> None:
    txt = json.dumps({
        "project_done": False,
        "reason": "screen one candidate",
        "new_tasks": [{
            "title": "Screen candidate access",
            "impact_score": 5,
            "impact_area": "discovery",
            "evidence": "The selected candidate has not been access-checked.",
            "acceptance_check": "research/access_screen.json records pass or fail",
            "non_goals": ["do not preregister", "do not execute inference"],
            "context_refs": [{
                "kind": "artifact",
                "ref": "research/IDEA_CANDIDATES.md",
                "why": "selected candidate",
                "content_hash": "abc",
            }],
            "scope": "bounded",
            "stage_closing": False,
            "objective": "Verify public code, data, model, and evaluator access.",
            "key": "access",
            "deps": [],
        }],
    })

    spec = parse_planner_text(txt).new_tasks[0]
    assert spec.acceptance_check.endswith("pass or fail")
    assert spec.non_goals == ["do not preregister", "do not execute inference"]
    assert spec.context_refs[0]["ref"] == "research/IDEA_CANDIDATES.md"


def test_plan_next_rejects_whole_stage_research_monolith() -> None:
    broad = json.dumps({
        "project_done": False,
        "reason": "close research in one mission",
        "new_tasks": [{
            "title": "Close research with a new thesis",
            "impact_score": 5,
            "impact_area": "discovery",
            "evidence": "The stage is open.",
            "acceptance_check": "independent review closes research",
            "non_goals": [],
            "context_refs": [{
                "kind": "artifact",
                "ref": "research/PIPELINE_STATE.json",
                "why": "stage state",
                "content_hash": "",
            }],
            "scope": "bounded",
            "stage_closing": True,
            "objective": (
                "Survey primary literature and select candidate ideas; verify "
                "access and environment preflight; freeze a preregistration and "
                "run the GPU experiment; analyze results and write the paper claim."
            ),
            "key": "all-research",
            "deps": [],
        }],
    })

    verdict = Planner(_FakeRunner(broad)).plan_next(
        continuous_objective="Develop a strong paper.",
    )

    assert verdict.new_tasks == []
    assert "granularity" in verdict.error
    assert "one fresh Engineer session" in verdict.reason


def test_plan_next_rejects_large_multi_artifact_package() -> None:
    task = {
        "title": "Rewrite the full plan package",
        "impact_score": 5,
        "impact_area": "reliability",
        "evidence": "Six planning surfaces are stale.",
        "acceptance_check": (
            "EXPERIMENT_PLAN.md, BASELINE_PLAN.md, CODE_REUSE_PLAN.md, "
            "INFRA_CHOICE.md, BENCHMARK_PROVENANCE.json, and RUN_CONTRACT.json "
            "are all rewritten"
        ),
        "non_goals": ["do not execute experiments"],
        "context_refs": [
            {"kind": "artifact", "ref": f"research/input-{i}.json", "why": "input", "content_hash": ""}
            for i in range(6)
        ],
        "scope": "bounded",
        "stage_closing": False,
        "objective": (
            "Rewrite EXPERIMENT_PLAN.md, BASELINE_PLAN.md, CODE_REUSE_PLAN.md, "
            "INFRA_CHOICE.md, BENCHMARK_PROVENANCE.json, and RUN_CONTRACT.json."
        ),
        "key": "all-plan",
        "deps": [],
    }
    verdict = Planner(_FakeRunner(json.dumps({
        "project_done": False,
        "reason": "repair all plan files",
        "new_tasks": [task],
    }))).plan_next(continuous_objective="Develop a strong paper.")

    assert verdict.new_tasks == []
    assert "artifact boundary" in verdict.error


def test_parse_planner_text_uses_latest_json_verdict() -> None:
    placeholder = json.dumps({
        "project_done": False,
        "reason": "inspecting before routing",
        "restart_daemon": False,
        "waiting": False,
        "new_tasks": [],
    })
    final = json.dumps({
        "project_done": False,
        "reason": "route the no-go pivot",
        "restart_daemon": False,
        "waiting": False,
        "new_tasks": [{
            "title": "Rollback and pivot the failed positive method plan",
            "impact_score": 5,
            "impact_area": "requirement_gap",
            "evidence": "research/CLAIM_REPAIR_NO_GO.md blocks the positive claim",
            "scope": "bounded",
            "objective": (
                "Roll back to plan, inspect the no-go evidence, and produce "
                "the next pivot plan."
            ),
        }],
    })

    v = parse_planner_text(placeholder + "\n" + final)

    assert not v.error
    assert len(v.new_tasks) == 1
    assert v.reason == "route the no-go pivot"
    assert v.new_tasks[0].title == "Rollback and pivot the failed positive method plan"


def test_parse_planner_text_returns_error_verdict_on_garbage() -> None:
    v = parse_planner_text("not json at all")
    assert v.project_done is False
    assert v.error or v.new_tasks == []


def test_plan_next_repairs_malformed_json_once_in_same_session() -> None:
    malformed = "I will schedule the loader repair, but this is not JSON."
    repaired = json.dumps({
        "project_done": False,
        "reason": "repair the loader",
        "restart_daemon": False,
        "restart_reason": "",
        "waiting": False,
        "waiting_reason": "",
        "waiting_contract": None,
        "new_tasks": [{
            "title": "Repair loader",
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "loader test fails",
            "scope": "bounded",
            "objective": "Fix the loader and add a regression test.",
            "key": None,
            "deps": [],
        }],
        "meta_decision": None,
        "checklist_ops": [],
    })
    runner = _SequencedRunner(
        RunnerResult(
            exit_code=0,
            agent_messages=[malformed],
            thread_id="planner-thread",
            input_tokens=10,
            output_tokens=3,
        ),
        RunnerResult(
            exit_code=0,
            agent_messages=[repaired],
            thread_id="planner-thread",
            input_tokens=4,
            cached_input_tokens=2,
            output_tokens=5,
        ),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="keep the loader correct",
        planning_cycle=7,
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == ["Repair loader"]
    assert len(runner.calls) == 2
    assert runner.calls[0]["resume_thread_id"] is None
    assert runner.calls[1]["resume_thread_id"] == "planner-thread"
    assert runner.calls[1]["run_label"] == "planner.cycle7.schema-repair"
    assert runner.calls[1]["options"].sandbox_mode == "read-only"
    assert verdict.schema_repair_attempted is True
    assert verdict.schema_repair_succeeded is True
    assert verdict.schema_repair_original_sha256 == hashlib.sha256(
        malformed.encode("utf-8")
    ).hexdigest()
    assert verdict.input_tokens == 14
    assert verdict.cached_input_tokens == 2
    assert verdict.output_tokens == 8
    assert verdict.schema_repair_input_tokens == 4
    assert verdict.schema_repair_event_payload() == {
        "schema_repair_attempted": True,
        "schema_repair_succeeded": True,
        "schema_repair_original_sha256": hashlib.sha256(
            malformed.encode("utf-8")
        ).hexdigest(),
        "schema_repair_error": "",
        "schema_repair_input_tokens": 4,
        "schema_repair_cached_input_tokens": 2,
        "schema_repair_output_tokens": 5,
        "schema_repair_reasoning_output_tokens": 0,
        "schema_repair_premium_requests": 0.0,
    }


def test_plan_next_does_not_repair_without_resumable_thread() -> None:
    runner = _SequencedRunner(RunnerResult(
        exit_code=0,
        agent_messages=["not json"],
        thread_id=None,
    ))

    verdict = Planner(runner).plan_next(continuous_objective="keep working")

    assert verdict.error == "unparseable planner output"
    assert verdict.schema_repair_attempted is False
    assert len(runner.calls) == 1


def test_plan_next_failed_schema_repair_remains_retryable() -> None:
    runner = _SequencedRunner(
        RunnerResult(
            exit_code=0,
            agent_messages=["not json"],
            thread_id="planner-thread",
        ),
        RunnerResult(
            exit_code=0,
            agent_messages=["still not json"],
            thread_id="planner-thread",
        ),
    )

    verdict = Planner(runner).plan_next(continuous_objective="keep working")

    assert verdict.error == "unparseable planner output"
    assert verdict.schema_repair_attempted is True
    assert verdict.schema_repair_succeeded is False
    assert verdict.schema_repair_error == "unparseable planner output"
    assert len(runner.calls) == 2


@pytest.mark.parametrize(
    ("reason", "waiting_reason", "expected_fragment"),
    [
        (
            "training run still in progress; nothing higher-impact to do",
            "CV-GRPO run 2b510 at step 40/200, not terminal",
            "CV-GRPO",
        ),
        (
            "image route remains a provider-side blocker",
            (
                "paper/figures/IMAGE2_OPERATOR_ACTION_REQUIRED.md documents the "
                "non-local image generation unknown_model blocker; all local draft "
                "work is exhausted"
            ),
            "IMAGE2_OPERATOR_ACTION_REQUIRED.md",
        ),
    ],
    ids=["running-experiment", "external-capability"],
)
def test_parse_planner_text_waiting_is_not_error(
    reason: str,
    waiting_reason: str,
    expected_fragment: str,
) -> None:
    # First-class await-external: planner intentionally idles with no tasks
    # and no done. This must NOT be reported as an error (which would spin /
    # make-work); it is a clean waiting outcome.
    txt = json.dumps({
        "project_done": False,
        "reason": reason,
        "new_tasks": [],
        "waiting": True,
        "waiting_reason": waiting_reason,
        "waiting_contract": {
            "blocker_fingerprint": "test:external-dependency",
            "recheck_condition": "the external dependency changes state",
            "recheck_token": "unchanged-v1",
            "stage_reconciliation_required": False,
            "allow_verification_probe": False,
            "recheck_after_seconds": 0,
            "wait_mode": "event",
            "wake_on": ["authorization"],
            "watched_paths": [],
            "expires_at": 0,
        },
    })
    v = parse_planner_text(txt)
    assert v.waiting is True
    assert v.project_done is False
    assert v.new_tasks == []
    assert not v.error
    assert expected_fragment in v.waiting_reason


def test_parse_planner_text_preserves_agent_authored_waiting_contract() -> None:
    txt = json.dumps({
        "project_done": False,
        "reason": "source remains unavailable",
        "new_tasks": [],
        "waiting": True,
        "waiting_reason": "operator must provide the licensed source",
        "waiting_contract": {
            "blocker_fingerprint": "source:chen-2003",
            "recheck_condition": "a licensed full-text path appears",
            "recheck_token": "no-source-v1",
            "stage_reconciliation_required": False,
            "allow_verification_probe": False,
            "recheck_after_seconds": 0,
            "wait_mode": "event",
            "wake_on": ["authorization", "artifact_revision"],
            "watched_paths": ["research/LICENSED_SOURCE.md"],
            "expires_at": 0,
        },
    })

    verdict = parse_planner_text(txt)

    assert verdict.waiting is True
    assert verdict.waiting_contract == WaitingContract(
        blocker_fingerprint="source:chen-2003",
        recheck_condition="a licensed full-text path appears",
        recheck_token="no-source-v1",
        stage_reconciliation_required=False,
        allow_verification_probe=False,
        recheck_after_seconds=0,
        wait_mode="event",
        wake_on=("authorization", "artifact_revision"),
        watched_paths=("research/LICENSED_SOURCE.md",),
        expires_at=0.0,
        operator_action_required=True,
    )


def test_waiting_contract_positional_api_remains_backward_compatible() -> None:
    contract = WaitingContract("blocker", "condition", "token", True, 600)

    assert contract.allow_verification_probe is True
    assert contract.recheck_after_seconds == 600
    assert contract.stage_reconciliation_required is False
    assert contract.wait_mode == "poll"
    assert contract.operator_action_required is False


def test_waiting_contract_rejects_unsafe_watched_paths() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "await an artifact",
        "new_tasks": [],
        "waiting": True,
        "waiting_reason": "artifact has not changed",
        "waiting_contract": {
            "blocker_fingerprint": "artifact:report",
            "recheck_condition": "report revision changes",
            "recheck_token": "report-v1",
            "stage_reconciliation_required": False,
            "allow_verification_probe": False,
            "recheck_after_seconds": 0,
            "wait_mode": "event",
            "wake_on": ["artifact_revision", "unknown"],
            "watched_paths": ["../secret", "/etc/passwd", "research/report.json"],
            "expires_at": 0,
        },
    }))

    assert verdict.waiting_contract is not None
    assert verdict.waiting_contract.wake_on == ("artifact_revision",)
    assert verdict.waiting_contract.watched_paths == ("research/report.json",)


def test_waiting_contract_does_not_infer_operator_hold_from_failed_theses() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "all authorized theses are exhausted",
        "new_tasks": [],
        "waiting": True,
        "waiting_reason": "await explicit authorization for more research",
        "waiting_contract": {
            "blocker_fingerprint": "research-stage-no-viable-thesis-after-six-no-gos",
            "recheck_condition": (
                "Manager explicitly authorizes a materially distinct thesis"
            ),
            "recheck_token": "six-no-gos-v1",
            "stage_reconciliation_required": True,
            "operator_action_required": False,
            "allow_verification_probe": False,
            "recheck_after_seconds": 0,
        },
    }))

    assert verdict.waiting
    assert verdict.waiting_contract is not None
    assert verdict.waiting_contract.operator_action_required is False


def test_waiting_contract_preserves_explicit_operator_only_scope_expansion() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "operator must choose whether to expand beyond the objective",
        "new_tasks": [],
        "waiting": True,
        "waiting_reason": "operator decision required",
        "waiting_contract": {
            "blocker_fingerprint": "operator:scope-expansion",
            "recheck_condition": "operator explicitly expands the objective",
            "recheck_token": "scope-v1",
            "stage_reconciliation_required": False,
            "operator_action_required": True,
            "allow_verification_probe": False,
            "recheck_after_seconds": 0,
        },
    }))
    assert verdict.waiting_contract is not None
    assert verdict.waiting_contract.operator_action_required is True


def test_planner_role_treats_no_go_as_autonomous_pivot() -> None:
    text = Path(
        "argus_skill/builtin_skills/planner/argus-planner-role.md"
    ).read_text()
    assert "NO-GO" in text
    assert "NOT an operator-only blocker" in text


def test_parse_planner_text_no_tasks_without_waiting_is_error() -> None:
    # No tasks, not done, and waiting NOT set → still treated as a degenerate
    # planner output (error), preserving the original safety net.
    txt = json.dumps({
        "project_done": False,
        "reason": "hmm",
        "new_tasks": [],
    })
    v = parse_planner_text(txt)
    assert v.waiting is False
    assert v.error


def test_parse_planner_text_rejects_waiting_without_contract() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "external dependency",
        "new_tasks": [],
        "waiting": True,
        "waiting_reason": "still blocked",
        "waiting_contract": None,
    }))

    assert verdict.waiting is False
    assert verdict.error == "waiting verdict requires waiting_contract"


# ---------------------------------------------------------------------------
# Planner.plan_next dispatch
# ---------------------------------------------------------------------------


def test_plan_next_returns_done_when_runner_says_so() -> None:
    runner = _FakeRunner(json.dumps({
        "project_done": True,
        "reason": "everything is done",
        "new_tasks": [],
    }))
    verdict = Planner(runner).plan_next(
        continuous_objective="keep going",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
    )
    assert verdict.project_done is True
    assert verdict.new_tasks == []
    assert len(runner.calls) == 1


def test_plan_next_preserves_reasoning_output_tokens() -> None:
    runner = _FakeRunner(
        json.dumps({"project_done": True, "reason": "everything is done", "new_tasks": []}),
        reasoning_output_tokens=321,
    )
    verdict = Planner(runner).plan_next(
        continuous_objective="keep going",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
    )
    assert verdict.reasoning_output_tokens == 321


def test_plan_next_passes_planner_config_to_runner() -> None:
    runner = _FakeRunner(json.dumps({"project_done": True, "reason": "x", "new_tasks": []}))
    cfg = PlannerConfig(
        model="gpt-test",
        reasoning_effort="high",
        working_dir="/tmp/planner",
        skip_git_repo_check=True,
        full_auto=False,
        dangerous_yolo=True,
    )
    Planner(runner).plan_next(
        continuous_objective="goal",
        journal_tail="recent work",
        planning_cycle=2,
        runtime_change_summary="Runtime source changed since daemon start.",
        config=cfg,
    )
    sent_prompt, opts = runner.calls[0]
    assert opts.model == "gpt-test"
    assert opts.reasoning_effort == "high"
    assert opts.working_dir == "/tmp/planner"
    assert opts.dangerous_yolo is True
    # Structural prompt assertions only — verify the planner prompt
    # carries the runtime context + the current stage checklist headline.
    assert "Runtime source changed since daemon start." in sent_prompt


def test_plan_next_defaults_to_xhigh_reasoning_effort() -> None:
    runner = _FakeRunner(json.dumps({"project_done": True, "reason": "x", "new_tasks": []}))

    Planner(runner).plan_next(
        continuous_objective="goal",
        journal_tail="recent work",
        planning_cycle=0,
        runtime_change_summary="",
    )

    sent_prompt, opts = runner.calls[0]
    assert opts.reasoning_effort == "xhigh"
    assert "## Stage checklist" in sent_prompt


def test_plan_next_rejects_nonproof_task_for_hard_theorem_objective(
    monkeypatch,
) -> None:
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "calibrate one more finite route",
        "new_tasks": [{
            "title": "Validate partitioned order-22 certificate route",
            "objective": (
                "Run bounded geng shards and, if no witness appears, classify the "
                "result as feasibility evidence only."
            ),
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "Replayable finite shard manifests.",
            "scope": "bounded",
            "stage_closing": False,
            "key": None,
            "deps": [],
        }],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(harness_overlay, "resolve_project_root", lambda: Path("."))
    monkeypatch.setattr(stage_checklists, "current_stage", lambda _root: "solve")

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is at least one nontrivial theorem with "
            "a complete self-contained proof accepted by an independent Reviewer. "
            "Finite enumeration and feasibility evidence do not count."
        ),
    )

    assert verdict.new_tasks == []
    assert "hard objective contract violation" in verdict.error


def test_plan_next_accepts_proof_task_for_hard_theorem_objective(
    monkeypatch,
) -> None:
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "prove a structural special case",
        "new_tasks": [{
            "title": "State and prove a structural cycle theorem",
            "objective": (
                "State a precisely quantified nontrivial lemma, give a complete "
                "self-contained rigorous proof, update the lemma graph and claim "
                "ledger, and require independent Reviewer acceptance."
            ),
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "The theorem statement, proof, ledger, graph, and review.",
            "scope": "bounded",
            "stage_closing": False,
            "key": None,
            "deps": [],
        }],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is at least one nontrivial theorem with "
            "a complete self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == [
        "State and prove a structural cycle theorem"
    ]


def test_theorem_contract_allows_honest_prior_no_theorem_context(
    monkeypatch,
) -> None:
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "replace the prior finite route with proof work",
        "new_tasks": [{
            "title": "Prove structural no-C4/C8/C16 cubic lemma",
            "objective": (
                "State a precisely quantified nontrivial lemma and give a complete "
                "self-contained rigorous proof. Do not accept the mission as a "
                "successful finite-search-only report. Update the lemma graph and "
                "claim ledger and require independent Reviewer acceptance."
            ),
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": (
                "Prior work has no theorem and only a bounded non-exhaustive prefix."
            ),
            "scope": "bounded",
            "stage_closing": True,
            "key": "structural-lemma",
            "deps": [],
        }],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is at least one nontrivial theorem with "
            "a complete self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == [
        "Prove structural no-C4/C8/C16 cubic lemma"
    ]


def test_theorem_contract_allows_review_stage_closure(monkeypatch) -> None:
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "close the accepted theorem's review delta",
        "new_tasks": [{
            "title": "Close review-stage venue profile after round 12",
            "objective": (
                "Record the already accepted theorem's review-stage venue delta "
                "without changing its proof or novelty classification."
            ),
            "impact_score": 4,
            "impact_area": "requirement_gap",
            "evidence": "Reviewer-certified theorem and current venue artifacts.",
            "scope": "bounded",
            "stage_closing": True,
            "key": "review-delta",
            "deps": [],
        }],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(harness_overlay, "resolve_project_root", lambda: Path("."))
    monkeypatch.setattr(stage_checklists, "current_stage", lambda _root: "review")

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is at least one nontrivial theorem with "
            "a complete self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == [
        "Close review-stage venue profile after round 12"
    ]


def test_theorem_contract_relies_on_runtime_reviewer_not_task_wording(
    monkeypatch, tmp_path,
) -> None:
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "prove the next structural lemma",
        "new_tasks": [{
            "title": "Prove a rooted path lemma",
            "objective": (
                "State a precisely quantified theorem and give a complete "
                "self-contained rigorous proof. Update the claim ledger and "
                "lemma graph; finite checks alone cannot complete the task."
            ),
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "Current solve boundary and exact proof artifacts.",
            "scope": "bounded",
            "stage_closing": False,
            "key": "rooted-lemma",
            "deps": [],
        }],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(harness_overlay, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(stage_checklists, "current_stage", lambda _root: "solve")

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is a theorem with a complete "
            "self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == [
        "Prove a rooted path lemma"
    ]


def test_theorem_contract_rejects_nonadvancing_theorem_after_baseline(
    monkeypatch, tmp_path,
) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "CLAIM_LEDGER.md").write_text(
        "C22 | complete bounded theorem with self-contained proof\n",
        encoding="utf-8",
    )
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "prove another easy fact",
        "new_tasks": [{
            "title": "Re-derive a standard degree lemma",
            "objective": (
                "Read research/CLAIM_LEDGER.md and research/LEMMA_GRAPH.md. "
                "State a theorem and give a complete self-contained proof."
            ),
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "The existing theorem ledger and dependency graph.",
            "scope": "bounded",
            "stage_closing": False,
            "key": "easy-lemma",
            "deps": [],
        }],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(harness_overlay, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(stage_checklists, "current_stage", lambda _root: "solve")

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is a theorem with a complete "
            "self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert verdict.new_tasks == []
    assert "strict improvement" in verdict.error


def test_theorem_contract_accepts_strict_bound_improvement_after_baseline(
    monkeypatch, tmp_path,
) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "CLAIM_LEDGER.md").write_text(
        "C22 | complete bounded theorem with self-contained proof\n",
        encoding="utf-8",
    )
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "strictly improve the current blocker bound",
        "new_tasks": [{
            "title": "Prove a sharper rooted blocker bound",
            "objective": (
                "Read research/CLAIM_LEDGER.md and research/LEMMA_GRAPH.md. "
                "Prove a precisely quantified theorem with a complete "
                "self-contained proof that strictly strengthens Theorem 14.1 "
                "by replacing constant 27 with an explicit K < 27."
            ),
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "C22/P21 record blocker-or-27 as the current boundary.",
            "scope": "bounded",
            "stage_closing": False,
            "key": "sharper-bound",
            "deps": [],
        }],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(harness_overlay, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(stage_checklists, "current_stage", lambda _root: "solve")

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is a theorem with a complete "
            "self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == [
        "Prove a sharper rooted blocker bound"
    ]


def test_theorem_contract_allows_guarded_overlap_node_after_strict_theorem(
    monkeypatch, tmp_path,
) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "CLAIM_LEDGER.md").write_text(
        "C22 | complete bounded theorem with self-contained proof\n",
        encoding="utf-8",
    )
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "improve the theorem, then audit its mechanism",
        "new_tasks": [
            {
                "title": "Prove a sharper rooted blocker bound",
                "objective": (
                    "Read research/CLAIM_LEDGER.md and research/LEMMA_GRAPH.md. "
                    "Prove a theorem with a complete self-contained proof that "
                    "strictly strengthens Theorem 14.1 by obtaining K < 27."
                ),
                "impact_score": 5,
                "impact_area": "correctness",
                "evidence": "C22/P21 record the current blocker-or-27 bound.",
                "scope": "bounded",
                "stage_closing": False,
                "key": "r15-proof",
                "deps": [],
            },
            {
                "title": "Audit and close the round-15 solve package",
                "objective": (
                    "Read the new theorem plus research/CLAIM_LEDGER.md and "
                    "research/LEMMA_GRAPH.md. Complete a mechanism-overlap audit. "
                    "This node may close only if the package retains a theorem with "
                    "a complete self-contained proof; if the proof artifact is "
                    "absent or flawed, repair it or fail."
                ),
                "impact_score": 4,
                "impact_area": "requirement_gap",
                "evidence": "The strict theorem node creates novelty-audit debt.",
                "scope": "bounded",
                "stage_closing": True,
                "key": "r15-overlap",
                "deps": ["r15-proof"],
            },
        ],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(harness_overlay, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(stage_checklists, "current_stage", lambda _root: "solve")

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is a theorem with a complete "
            "self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == [
        "Prove a sharper rooted blocker bound",
        "Audit and close the round-15 solve package",
    ]


def test_theorem_contract_allows_round16_audit_to_inherit_proof_dependency(
    monkeypatch, tmp_path,
) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "CLAIM_LEDGER.md").write_text(
        "C23 | complete bounded theorem with self-contained proof\n",
        encoding="utf-8",
    )
    runner = _FakeRunner(json.dumps({
        "project_done": False,
        "reason": "strict theorem advancement followed by overlap audit",
        "new_tasks": [
            {
                "title": "Prove a graph-specific blocker advance",
                "objective": (
                    "Read research/CLAIM_LEDGER.md and research/LEMMA_GRAPH.md. "
                    "Prove a precisely quantified theorem with a complete "
                    "self-contained proof that strictly advances C23 by an "
                    "improved bound K < 25."
                ),
                "impact_score": 5,
                "impact_area": "correctness",
                "evidence": "C23/P22/T15 record blocker-or-25.",
                "scope": "bounded",
                "stage_closing": False,
                "key": "round16-proof",
                "deps": [],
            },
            {
                "title": "Audit overlap for new blocker mechanism",
                "objective": (
                    "After the successful predecessor theorem, read its artifact "
                    "plus research/CLAIM_LEDGER.md and research/LEMMA_GRAPH.md. "
                    "Run a mechanism-level overlap audit with exact queries, "
                    "primary sources, citation checks, overlap mapping, and "
                    "novelty limitations."
                ),
                "impact_score": 4,
                "impact_area": "correctness",
                "evidence": "A refined theorem triggers a separate overlap audit.",
                "scope": "bounded",
                "stage_closing": True,
                "key": "round16-overlap",
                "deps": ["round16-proof"],
            },
        ],
    }))
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **_kwargs: "prompt"),
    )
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(harness_overlay, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(stage_checklists, "current_stage", lambda _root: "solve")

    verdict = Planner(runner).plan_next(
        continuous_objective=(
            "The hard success criterion is a theorem with a complete "
            "self-contained proof accepted by an independent Reviewer."
        ),
    )

    assert not verdict.error
    assert [task.title for task in verdict.new_tasks] == [
        "Prove a graph-specific blocker advance",
        "Audit overlap for new blocker mechanism",
    ]


def test_theorem_first_prompt_makes_nonproof_fallback_illegal(
    monkeypatch, tmp_path,
) -> None:
    from argus_skill.skills import harness_overlay, stage_checklists
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "math")
    monkeypatch.setattr(stage_checklists, "current_stage", lambda *_a, **_k: "solve")
    monkeypatch.setattr(
        stage_checklists,
        "format_stage_checklist",
        lambda s, **_k: f"<<CHECKLIST:{s}>>",
    )
    monkeypatch.setattr(
        harness_overlay, "resolve_project_root", lambda *_a, **_k: tmp_path
    )
    prompt = Planner._build_planner_prompt(
        continuous_objective=(
            "The hard success criterion is to produce a theorem with a complete "
            "self-contained proof. Finite computation does not count."
        ),
        journal_tail="",
        planning_cycle=0,
    )

    assert "Active hard theorem-proof contract" in prompt
    assert "MUST NOT be written as an alternative successful fallback" in prompt


def test_plan_next_returns_error_verdict_on_runner_exception() -> None:
    class _BrokenRunner:
        def run_exec(self, **_):
            raise RuntimeError("backend exit 127")

    verdict = Planner(_BrokenRunner()).plan_next(
        continuous_objective="goal",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
    )
    assert verdict.project_done is False
    assert verdict.error


# ---------------------------------------------------------------------------
# Parallel paper-drafting track (run/analysis only) — _build_planner_prompt
# ---------------------------------------------------------------------------


def _prompt_for_stage(monkeypatch, tmp_path, stage: str) -> str:
    """Build the planner prompt with current_stage pinned to ``stage``."""
    from argus_skill.skills import harness_overlay, stage_checklists
    from argus_skill.skills.vertical_select import persist_vertical

    # In a real mission the Manager decides + persists the vertical before the
    # planner builds a prompt; resolve_vertical is now fail-hard, so seed it.
    persist_vertical(tmp_path, "research")
    monkeypatch.setattr(stage_checklists, "current_stage", lambda *_a, **_k: stage)
    monkeypatch.setattr(
        stage_checklists,
        "format_stage_checklist",
        lambda s, **_k: f"<<CHECKLIST:{s}>>",
    )
    monkeypatch.setattr(
        harness_overlay, "resolve_project_root", lambda *_a, **_k: tmp_path
    )
    return Planner._build_planner_prompt(
        continuous_objective="write the EMNLP paper",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
    )


def test_prompt_has_concise_dynamic_host_policy(monkeypatch, tmp_path) -> None:
    from argus_skill.planner.planner import MIN_PLANNER_IMPACT_SCORE

    prompt = _prompt_for_stage(monkeypatch, tmp_path, "research")
    assert "## Dynamic host policy" in prompt
    assert f"`impact_score >= {MIN_PLANNER_IMPACT_SCORE}`" in prompt
    assert "provided planner schema" in prompt
    assert "JSON only" in prompt
    assert "Output a JSON object with this exact shape" not in prompt


def test_parallel_drafting_block_present_at_run(monkeypatch, tmp_path) -> None:
    prompt = _prompt_for_stage(monkeypatch, tmp_path, "run")
    assert "## Parallel paper-drafting track" in prompt
    # Draft-stage checklist is surfaced for scoping.
    assert "<<CHECKLIST:draft>>" in prompt
    # Integrity + non-advancement guardrails are present.
    assert "PIPELINE_STATE.json" in prompt
    assert "RESULT_PLACEHOLDERS.md" in prompt
    assert "TBD" in prompt
    # Reviewer framing names the current stage as background-only.
    assert "BACKGROUND context only" in prompt


def test_parallel_drafting_block_present_at_analysis(monkeypatch, tmp_path) -> None:
    prompt = _prompt_for_stage(monkeypatch, tmp_path, "analysis")
    assert "## Parallel paper-drafting track" in prompt
    assert "<<CHECKLIST:draft>>" in prompt
    # analysis-stage gets the evidence_chain structural caveat.
    assert "evidence_chain" in prompt


def test_parallel_drafting_block_absent_outside_run_analysis(
    monkeypatch, tmp_path
) -> None:
    for stage in ("research", "plan", "benchmark", "draft", "review", "submission"):
        prompt = _prompt_for_stage(monkeypatch, tmp_path, stage)
        assert "## Parallel paper-drafting track (run/analysis only)" not in prompt, stage
        assert "RESULT_PLACEHOLDERS.md" not in prompt, stage


def test_parallel_drafting_exception_documented_in_role() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    assert "Parallel paper drafting is an overlap exception" in text
    assert "does not complete or advance any stage" in text


def test_waiting_external_capability_documented_in_role() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    assert "external capability blocker" in text
    assert "written action artifact" in text
    assert "operator action" in text
    assert "no independent high-impact work remains" in text
    assert "Reversible project-local housekeeping is not such a blocker" in text
    assert "choose the safe archive instead" in text


def test_prompt_routes_reversible_local_archive_as_engineer_work(
    monkeypatch, tmp_path
) -> None:
    prompt = _prompt_for_stage(monkeypatch, tmp_path, "run")
    compact = " ".join(prompt.split())
    assert "reversible project-local archive/quarantine" in compact
    assert "ordinary Engineer work, not an external operator dependency" in compact
    assert "queue the safe archive" in compact


def test_decision_frontier_prevents_speculative_downstream_dag_nodes() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    compact = " ".join(text.split())

    assert "Decision-frontier rule" in text
    assert "enqueue ONLY that decision node" in compact
    assert "Do not speculatively enqueue training" in compact
    assert "Re-plan from the reviewed outcome" in compact
    assert "waiting=true" in text and "waiting_contract" in text
    assert "Manager owns stage transitions" in compact
    assert "can never create credentials" in compact
    assert "operator_action_required=true" in compact
    assert "resolve a stale wait" in compact


def test_planner_does_not_repeat_skill_loading_in_task_objectives() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    compact = " ".join(text.split())

    assert "Do not tell the Engineer to export, open, or read built-in skill files" in compact
    assert "SkillLoop already matches and task-adapts" in compact
    assert "Name at most one exact skill" in compact


def test_stage_ordering_rule_in_role() -> None:
    """The role must carry a GENERAL stage-ordering rule (all verticals):
    finish the current stage before any downstream work; no skipping."""
    text = load_builtin_skill_text("argus-planner-role.md")
    # Must advance stages in order and complete the current stage first.
    assert "STRICTLY IN ORDER" in text
    assert "current-stage" in text and "checklist" in text
    # Downstream optimization is explicitly named as blocked.
    assert "downstream optimization" in text
    assert "Manager alone advances or rolls back `current_stage`" in text
    # Phrased generally — not nanochat / setup / GROUND_TRUTH specific.
    lower = text.lower()
    assert "nanochat" not in lower
    assert "ground_truth" not in lower


def test_stage_gate_block_surfaces_current_stage(monkeypatch, tmp_path) -> None:
    """The built prompt must surface a concrete stage gate that names the
    current stage, its checklist, and the in-order ordering rule — for any
    stage, including non-paper stages like ``setup``/``optimize``."""
    for stage in ("setup", "optimize", "research", "run"):
        prompt = _prompt_for_stage(monkeypatch, tmp_path, stage)
        assert "## Stage gate" in prompt, stage
        # Names the actual current stage.
        assert f"`current_stage` (from research/PIPELINE_STATE.json) is `{stage}`" in prompt, stage
        # The current-stage checklist is surfaced right above the gate.
        assert f"<<CHECKLIST:{stage}>>" in prompt, stage
        # The hard ordering rule is present and references the stage.
        assert "STRICTLY IN ORDER" in prompt, stage
        assert "COMPLETES THE CURRENT STAGE" in prompt, stage
        assert "FORBIDDEN" in prompt, stage
        assert "Downstream stages" in prompt, stage


# ---------------------------------------------------------------------------
# DAG new_tasks: key/deps parsing + prompt teaching + schema
# ---------------------------------------------------------------------------


def test_parse_planner_text_reads_key_and_deps() -> None:
    """A DAG batch (parallel a/b + summary c) parses key/deps onto TaskSpec."""
    txt = json.dumps({
        "project_done": False,
        "reason": "fan out then summarize",
        "new_tasks": [
            {
                "key": "a",
                "deps": [],
                "title": "run seed 0",
                "impact_score": 5,
                "impact_area": "reliability",
                "evidence": "need multi-seed variance",
                "scope": "bounded",
                "objective": "train seed=0; write experiments/run-a/summary.tsv",
            },
            {
                "key": "b",
                "deps": [],
                "title": "run seed 1",
                "impact_score": 5,
                "impact_area": "reliability",
                "evidence": "need multi-seed variance",
                "scope": "bounded",
                "objective": "train seed=1; write experiments/run-b/summary.tsv",
            },
            {
                "key": "c",
                "deps": ["a", "b"],
                "title": "analyze",
                "impact_score": 5,
                "impact_area": "reliability",
                "evidence": "fan-in summary",
                "scope": "bounded",
                "objective": "read run-a/run-b summaries; write analysis/RESULTS.md",
            },
        ],
    })
    v = parse_planner_text(txt)
    assert not v.error
    assert [t.key for t in v.new_tasks] == ["a", "b", "c"]
    assert v.new_tasks[0].deps == []
    assert v.new_tasks[1].deps == []
    assert v.new_tasks[2].deps == ["a", "b"]


def test_parse_planner_text_flat_task_has_empty_key_deps() -> None:
    """Back-compat: a task with no key/deps parses to key='' / deps=[]."""
    txt = json.dumps({
        "project_done": False,
        "reason": "one flat task",
        "new_tasks": [{
            "title": "fix the loader",
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "loader crashes on empty input",
            "scope": "bounded",
            "objective": "patch code/loader.py and add a regression test",
        }],
    })
    v = parse_planner_text(txt)
    assert len(v.new_tasks) == 1
    assert v.new_tasks[0].key == ""
    assert v.new_tasks[0].deps == []


def test_parse_planner_text_accepts_up_to_six_tasks() -> None:
    """maxItems is now 6 (fan-out + fan-in); the parser must not cap at 3."""
    tasks = [
        {
            "key": f"k{i}",
            "deps": [],
            "title": f"task {i}",
            "impact_score": 5,
            "impact_area": "reliability",
            "evidence": "needed",
            "scope": "bounded",
            "objective": f"do work {i} and write out/{i}.txt",
        }
        for i in range(6)
    ]
    txt = json.dumps({"project_done": False, "reason": "six", "new_tasks": tasks})
    v = parse_planner_text(txt)
    assert len(v.new_tasks) == 6


def test_dag_teaching_section_in_role() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    assert "DAG" in text
    assert "`key`" in text and "`deps`" in text
    assert "Prefer a small DAG" in text
    assert "flat task only when" in text
    assert "short-horizon Engineer mission" in text
    assert "discovery, implementation, independent verification, and" in text
    assert "synthesis" in text
    # Self-contained objective requirement is taught.
    assert "self-contained" in text
    assert "exact artifacts" in text
    assert "explicitly read the artifacts" in text
    # Cap rule was raised to 6.
    assert "exceeds six nodes" in text


def test_planner_role_defines_coherent_short_node_boundaries() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    assert "one clear outcome" in text
    assert "one decisive acceptance check" in text
    assert "natural artifact" in text
    assert "Avoid both monoliths and meaningless microtasks" in text
    # Mission sizing is semantic, not an arbitrary wall-clock/token cutoff.
    lower = text.lower()
    assert "minutes per node" not in lower
    assert "tokens per node" not in lower


def test_planner_schema_accepts_dag_and_flat_tasks() -> None:
    """The structured-output schema validates both a key/deps DAG batch and a
    plain flat batch (key/deps optional)."""
    import jsonschema

    from argus_skill.planner.planner import (
        MIN_PLANNER_IMPACT_SCORE,
        PLANNER_SCHEMA_PATH,
    )

    with open(PLANNER_SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)

    base = {
        "project_done": False,
        "reason": "x",
        "restart_daemon": False,
        "restart_reason": "",
        "waiting": False,
        "waiting_reason": "",
        "waiting_contract": None,
        "meta_decision": None,
        "checklist_ops": None,
    }

    def _task(**over):
        t = {
            "title": "t",
            "impact_score": 5,
            "impact_area": "reliability",
            "evidence": "e",
            "acceptance_check": "pytest -q",
            "non_goals": ["do not edit unrelated files"],
            "context_refs": [{
                "kind": "artifact",
                "ref": "research/STATE.json",
                "why": "current state",
                "content_hash": "",
            }],
            "scope": "bounded",
            "stage_closing": False,
            "objective": "o",
            "key": None,
            "deps": None,
            "authorization_id": None,
            "authorization_action": None,
        }
        t.update(over)
        return t

    # DAG batch with key/deps validates.
    dag = dict(base, new_tasks=[
        _task(key="a", deps=[]),
        _task(key="b", deps=[]),
        _task(key="c", deps=["a", "b"]),
    ])
    jsonschema.validate(dag, schema)

    # Flat batch (no key/deps) still validates.
    flat = dict(base, new_tasks=[_task()])
    jsonschema.validate(flat, schema)

    authorized = dict(base, new_tasks=[_task(
        authorization_id="auth-123",
        authorization_action="validator_repair",
    )])
    jsonschema.validate(authorized, schema)

    waiting = dict(
        base,
        waiting=True,
        waiting_reason="operator must provide the licensed source",
        waiting_contract={
            "blocker_fingerprint": "source:chen-2003",
            "recheck_condition": "a licensed full-text path appears",
            "recheck_token": "source-missing-v1",
            "stage_reconciliation_required": False,
            "allow_verification_probe": False,
            "recheck_after_seconds": 0,
                "wait_mode": "event",
                "wake_on": ["authorization"],
                "watched_paths": [],
                "expires_at": 0,
        },
        new_tasks=[],
    )
    jsonschema.validate(waiting, schema)
    malformed_waiting = dict(
        waiting,
        waiting_contract={
            "blocker_fingerprint": "source:chen-2003",
            "recheck_condition": "a licensed full-text path appears",
        },
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(malformed_waiting, schema)
    # Codex Structured Outputs does not support conditional schema keywords.
    # The parser below enforces waiting=true => object contract fail-closed.
    jsonschema.validate(dict(waiting, waiting_contract=None), schema)

    # Six tasks validate (maxItems raised to 6); seven must fail.
    six = dict(base, new_tasks=[_task(key=f"k{i}") for i in range(6)])
    jsonschema.validate(six, schema)
    seven = dict(base, new_tasks=[_task(key=f"k{i}") for i in range(7)])
    with __import__("pytest").raises(jsonschema.ValidationError):
        jsonschema.validate(seven, schema)

    # Structured output must enforce the same minimum as the host parser.
    impact_schema = schema["properties"]["new_tasks"]["items"]["properties"][
        "impact_score"
    ]
    assert impact_schema["minimum"] == MIN_PLANNER_IMPACT_SCORE
    too_low = dict(
        base,
        new_tasks=[_task(impact_score=MIN_PLANNER_IMPACT_SCORE - 1)],
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(too_low, schema)
