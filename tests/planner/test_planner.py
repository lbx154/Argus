"""Unit tests for the Planner sub-agent: parsing + plan_next dispatch.

The Planner is stateless; we exercise the JSON parser end-to-end and the
plan_next dispatch path that the supervisor relies on. The historical
"critic" surface (per-iteration polish loop with Improvement records)
has been removed; if you came here looking for those tests, they were
deleted along with the dead code.
"""
from __future__ import annotations

import json

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
    )


def test_waiting_contract_positional_api_remains_backward_compatible() -> None:
    contract = WaitingContract("blocker", "condition", "token", True, 600)

    assert contract.allow_verification_probe is True
    assert contract.recheck_after_seconds == 600
    assert contract.stage_reconciliation_required is False


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


def test_decision_frontier_prevents_speculative_downstream_dag_nodes() -> None:
    text = load_builtin_skill_text("argus-planner-role.md")
    compact = " ".join(text.split())

    assert "Decision-frontier rule" in text
    assert "enqueue ONLY that decision node" in compact
    assert "Do not speculatively enqueue training" in compact
    assert "Re-plan from the reviewed outcome" in compact
    assert "waiting=true" in text and "waiting_contract" in text


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
            "scope": "bounded",
            "objective": "o",
            "key": None,
            "deps": None,
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
