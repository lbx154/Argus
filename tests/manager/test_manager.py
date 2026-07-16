"""Tests for the Manager division layer — decide_vertical / stage split / commit.

The Manager decides the vertical via ONE grounded agent call; these tests use a
fake runner returning the decision JSON (no real LLM).
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from argus_skill.manager import Division, Manager
from argus_skill.manager.domain_author import VerticalDecision, parse_vertical_decision
from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals.research.stages import STAGE_ORDER as RESEARCH_STAGES


class _DecisionResult:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"


class _DecisionRunner:
    """Fake runner: returns a fixed vertical-decision JSON for every call."""

    def __init__(self, decision: dict) -> None:
        self._decision = decision
        self.last_options = None

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.last_options = options
        return _DecisionResult(json.dumps(self._decision))


def _existing(vertical: str) -> _DecisionRunner:
    decision = {
        "choice": "existing",
        "vertical": vertical,
        "execution_task": "perform the requested task",
    }
    if vertical == "math":
        decision["research_target_level"] = "exploratory"
    return _DecisionRunner(decision)


def test_triage_existing_research():
    vertical, kind, regular = Manager(runner=_existing("research")).triage(
        "write a paper on retrieval for EMNLP and prepare the submission"
    )
    assert vertical == "research"
    assert kind == "research"
    assert regular is True


def test_triage_existing_nanochat_is_optimize():
    vertical, kind, regular = Manager(runner=_existing("nanochat")).triage(
        "minimize val_bpb on the nanochat train.py"
    )
    assert vertical == "nanochat"
    assert kind == "optimize"
    assert regular is True


@pytest.mark.parametrize("vertical", VERTICALS)
def test_explicit_builtin_vertical_preserves_execution_task(
    tmp_path,
    monkeypatch,
    vertical: str,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", vertical)

    runner = _existing("math") if vertical == "math" else None
    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "  execute this task  "
    )

    assert decision.choice == "existing"
    assert decision.vertical == vertical
    assert decision.execution_task == "execute this task"
    if vertical == "math":
        assert decision.research_target_level == "exploratory"


def test_explicit_math_without_backend_uses_fail_closed_target(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")

    decision = Manager(project_root=tmp_path).decide_vertical("prove the lemma")

    assert decision.execution_task == "prove the lemma"
    assert decision.research_target_level == "doctoral"


def test_explicit_math_target_env_override_needs_no_backend(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    monkeypatch.setenv(
        "ARGUS_SKILL_MATH_RESEARCH_TARGET_LEVEL",
        "exploratory",
    )

    decision = Manager(project_root=tmp_path).decide_vertical("prove the lemma")

    assert decision.research_target_level == "exploratory"


def test_plan_stages_research_is_the_8_stage_pipeline():
    stages = Manager().plan_stages("research")
    assert stages == list(RESEARCH_STAGES)
    assert stages[0] == "research" and stages[-1] == "submission"
    assert len(stages) == 8


def test_plan_stages_propagates_vertical_load_failure(monkeypatch):
    """A vertical that fails to resolve/import must PROPAGATE, not silently
    substitute the canonical/paper stage list — matches divide()'s and
    LifeSupervisor._resolve_vertical_once's documented FAIL-HARD contract.
    Silently degrading here would turn e.g. a kernelbench mission into the
    paper pipeline with no visible error."""
    from argus_skill.verticals import _base

    def _boom(name, project_root=None):
        raise RuntimeError("simulated broken vertical import")

    monkeypatch.setattr(_base, "load_vertical", _boom)
    with pytest.raises(RuntimeError, match="simulated broken vertical import"):
        Manager().plan_stages("kernelbench")


def test_plan_stages_defaults_when_vertical_has_no_stage_order(monkeypatch):
    """A vertical module that loads successfully but simply does not define
    STAGE_ORDER (an optional hook, not a failure) still gets the canonical
    template — this is NOT the guessing anti-pattern, it's the documented
    optional-hook default used throughout verticals/_base.py."""
    from argus_skill.skills.stage_checklists import CANONICAL_STAGE_ORDER
    from argus_skill.verticals import _base

    class _BareModule:
        pass

    monkeypatch.setattr(_base, "load_vertical", lambda name, project_root=None: _BareModule())
    stages = Manager().plan_stages("some-vertical")
    assert stages == list(CANONICAL_STAGE_ORDER)


def test_divide_commits_vertical_so_supervisor_trusts_it(tmp_path):
    mgr = Manager(project_root=tmp_path, runner=_existing("nanochat"))
    d = mgr.divide("minimize val_bpb on nanochat train.py")
    assert isinstance(d, Division)
    assert d.vertical == "nanochat" and d.kind == "optimize"
    # persisted into PIPELINE_STATE.json — the supervisor reads & trusts this
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "nanochat"


def test_math_divide_persists_manager_owned_research_target(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    manager = Manager(project_root=tmp_path, runner=_existing("math"))

    division = manager.divide("verify this bounded lemma")

    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text()
    )
    assert division.vertical == "math"
    assert state["vertical"] == "math"
    assert state["research_target_level"] == "exploratory"
    assert state["research_target_set_at"] > 0


def test_target_capable_vertical_parsing_is_not_math_specific() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "physics",
            "execution_task": "derive the requested result",
            "research_target_level": "doctoral",
        }),
        known_verticals=("physics",),
        research_target_verticals=("physics",),
    )

    assert decision is not None
    assert decision.vertical == "physics"
    assert decision.research_target_level == "doctoral"


def test_vertical_commit_persists_generic_research_target_contract(
    tmp_path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from argus_skill.verticals import _base

    monkeypatch.setattr(
        _base,
        "load_vertical",
        lambda name, project_root=None: SimpleNamespace(
            STAGE_ORDER=("scope", "review"),
            RESEARCH_TARGET_LEVELS=("exploratory", "publishable", "doctoral"),
        ),
    )
    manager = Manager(project_root=tmp_path)
    decision = VerticalDecision(
        choice="existing",
        vertical="physics",
        execution_task="derive the requested result",
        research_target_level="doctoral",
    )

    division = manager.commit_vertical_decision(
        "derive the requested result",
        decision,
    )

    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text()
    )
    assert division.vertical == "physics"
    assert state["research_target_level"] == "doctoral"
    assert state["research_target_set_at"] > 0


def test_vertical_decision_can_be_committed_after_external_revision_check(tmp_path):
    mgr = Manager(project_root=tmp_path, runner=_existing("research"))

    decision = mgr.decide_vertical("draft the paper")

    assert not (tmp_path / "research" / "PIPELINE_STATE.json").exists()
    division = mgr.commit_vertical_decision("draft the paper", decision)
    assert division.execution_task == "perform the requested task"
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"


def test_failed_vertical_commit_restores_pipeline_state(tmp_path, monkeypatch):
    manager = Manager(project_root=tmp_path, runner=_existing("research"))
    manager.divide("seed the research pipeline")
    pipeline_state = tmp_path / "research" / "PIPELINE_STATE.json"
    before = pipeline_state.read_bytes()
    decision = VerticalDecision(
        choice="existing",
        vertical="nanochat",
        execution_task="run nanochat",
    )
    monkeypatch.setattr(
        "argus_skill.manager._core.vertical_select.reset_stage_for_new_intent",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reset failed")),
    )

    with pytest.raises(RuntimeError, match="reset failed"):
        manager.commit_vertical_decision("run nanochat", decision)

    assert pipeline_state.read_bytes() == before


def test_divide_research_persists_and_lists_8_stages(tmp_path):
    d = Manager(project_root=tmp_path, runner=_existing("research")).divide(
        "draft a paper for EMNLP submission"
    )
    assert d.vertical == "research"
    assert d.stages == list(RESEARCH_STAGES)
    assert "regular" in d.headline()
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"


def test_root_task_id_scopes_manager_vertical_call(tmp_path):
    transitions: list[tuple[str, str]] = []

    @contextmanager
    def usage_context(root_task_id: str):
        transitions.append(("enter", root_task_id))
        try:
            yield
        finally:
            transitions.append(("exit", root_task_id))

    manager = Manager(
        project_root=tmp_path,
        runner=_existing("research"),
        usage_context=usage_context,
    )

    manager.divide("write a paper", root_task_id="root-task-1")

    assert transitions == [
        ("enter", "root-task-1"),
        ("exit", "root-task-1"),
    ]


def test_root_task_id_scopes_manager_front_door_call(tmp_path):
    transitions: list[tuple[str, str]] = []

    @contextmanager
    def usage_context(root_task_id: str):
        transitions.append(("enter", root_task_id))
        try:
            yield
        finally:
            transitions.append(("exit", root_task_id))

    manager = Manager(
        project_root=tmp_path,
        runner=_DecisionRunner({}),
        usage_context=usage_context,
    )

    manager.classify_front_door("build it", root_task_id="root-task-2")

    assert transitions == [
        ("enter", "root-task-2"),
        ("exit", "root-task-2"),
    ]


def test_root_task_id_scopes_manager_stage_call(tmp_path):
    from argus_skill.core.models import ReviewDecision

    transitions: list[tuple[str, str]] = []

    @contextmanager
    def usage_context(root_task_id: str):
        transitions.append(("enter", root_task_id))
        try:
            yield
        finally:
            transitions.append(("exit", root_task_id))

    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "current_stage": "research"}),
        encoding="utf-8",
    )
    review = ReviewDecision(
        status="continue",
        reason="more evidence needed",
        next_action="continue",
        checklist=[],
        planner_report={"headline": "continue", "forward_progress": True},
    )
    manager = Manager(
        project_root=tmp_path,
        usage_context=usage_context,
    )

    manager.decide_stage_transition(
        review=review,
        project_root=tmp_path,
        run_exec=lambda prompt: _DecisionResult(json.dumps({
            "action": "hold",
            "target_stage": "research",
            "reason": "continue",
        })),
        root_task_id="root-task-3",
    )

    assert transitions == [
        ("enter", "root-task-3"),
        ("exit", "root-task-3"),
    ]


def test_vertical_decision_persists_manager_live_view(tmp_path):
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "research",
        "execution_task": "Write the substantive manuscript.",
        "live_view": {
            "title": "Live manuscript",
            "reason": "The operator should see the paper evolve.",
            "paths": [".argus/live/current.md"],
        },
        "presentations": [{
            "path": ".argus/live/current.md",
            "content": "# Current manuscript status\n",
        }],
    })

    division = Manager(project_root=tmp_path, runner=runner).divide("write the paper")

    payload = json.loads(
        (tmp_path / ".argus" / "live-view.json").read_text(encoding="utf-8")
    )
    assert payload["title"] == "Live manuscript"
    assert payload["paths"] == [".argus/live/current.md"]
    assert (tmp_path / ".argus" / "live" / "current.md").exists()
    assert division.execution_task == "Write the substantive manuscript."
    assert runner.last_options.sandbox_mode == "read-only"
    assert runner.last_options.dangerous_yolo is False


def test_execution_task_parser_is_string_only_and_lossless() -> None:
    malformed = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "research",
            "execution_task": {"bad": True},
        }),
        known_verticals=["research"],
    )
    assert malformed is None

    long_task = "x" * 9000
    parsed = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "research",
            "execution_task": long_task,
        }),
        known_verticals=["research"],
    )
    assert parsed is not None
    assert parsed.execution_task == long_task


def test_divide_resets_stage_when_new_intent_supersedes_finished_prior_vertical(tmp_path):
    """End-to-end regression for the vertical-resolution false-stage-advance
    bug: an OLD custom vertical (``ops_continuity_runbook``) already reached
    ITS OWN terminal stage ("review") with status="done". A brand-new,
    operator-issued Task now gets divided into the "research" vertical, whose
    8-stage order ALSO contains a stage literally named "review". Before the
    fix, ``Manager.divide`` would persist "research" but leave
    ``current_stage="review"`` untouched (``persist_vertical`` is seed-only),
    and since "review" is a valid member of research's own order,
    ``current_stage()`` would silently accept it as real progress on the
    brand-new project. After the fix, ``divide`` must reset ``current_stage``
    to research's FIRST stage.
    """
    from argus_skill.skills.stage_checklists import current_stage
    from argus_skill.verticals._data_domain import write_data_domain

    old_stage_order = ("investigate", "configure", "dry_run", "document", "review")
    write_data_domain(
        tmp_path, "ops_continuity_runbook",
        stages=list(old_stage_order), checklist_stage_order=list(old_stage_order),
        created_by="manager",
    )
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": "ops_continuity_runbook",
            "current_stage": "review",
            "stages": {s: {"status": "done"} for s in old_stage_order},
        }),
        encoding="utf-8",
    )
    assert current_stage(tmp_path) == "review"  # old, unrelated project: done

    mgr = Manager(project_root=tmp_path, runner=_existing("research"))
    d = mgr.divide("write a brand new paper — totally unrelated to the old runbook")

    assert d.vertical == "research"
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"
    assert state["current_stage"] == "research"  # reset to the NEW vertical's first stage
    assert current_stage(tmp_path) == "research"


def test_divide_reopens_finished_pipeline_for_new_same_vertical_task(tmp_path):
    """Regression: a second research task must not immediately become planner done."""
    from argus_skill.skills.vertical_select import vertical_reached_own_terminal_stage

    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": "submission",
            "stages": {stage: {"status": "done"} for stage in RESEARCH_STAGES},
        }),
        encoding="utf-8",
    )
    assert vertical_reached_own_terminal_stage(tmp_path, "research") is True

    division = Manager(
        project_root=tmp_path,
        runner=_existing("research"),
    ).divide("start a separate second research task")

    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert division.vertical == "research"
    assert state["current_stage"] == "research"
    assert vertical_reached_own_terminal_stage(tmp_path, "research") is False


class _FakeResult:
    """Minimal RunnerResult shape the router classifier reads."""
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.exit_code = 0


def test_manager_no_runner_treats_free_text_as_task():
    # No backend → can't chat-classify → safe default is TASK (never drop work).
    assert Manager(runner=None).is_conversational("hi") is False


def test_manager_owns_chat_vs_task_decision():
    mgr = Manager()
    assert mgr.is_conversational(
        "hello there", run_exec=lambda p: _FakeResult("CHAT")
    ) is True
    assert mgr.is_conversational(
        "minimize val_bpb on train.py", run_exec=lambda p: _FakeResult("TASK")
    ) is False


# ---- F6: pure classification must NOT fire the skill matcher ----------------

class _CountingMission:
    """Stand-in ManagerMission that counts matcher calls (the LLM burn F6 cuts)."""
    def __init__(self) -> None:
        self.calls = 0

    def match(self, objective: str):
        self.calls += 1

        class _M:
            block = ""
        return _M()


def _mgr_with_store(tmp_path):
    mgr = Manager(project_root=tmp_path, runner=None, skill_store=object())
    mgr.mission = _CountingMission()  # type: ignore[assignment]
    return mgr


def test_role_skill_block_match_false_still_injects_fixed_role(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    block = mgr._role_skill_block("optimize a CUDA kernel", match=False)
    assert block.strip()                       # fixed role identity still injected
    assert "manager" in block.lower()
    assert mgr.mission.calls == 0              # matcher NEVER called


def test_role_skill_block_match_true_fires_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    mgr._role_skill_block("optimize a CUDA kernel", match=True)
    assert mgr.mission.calls == 1             # default path still matches


def test_route_does_not_fire_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    out = mgr.route("hello", run_exec=lambda p: _FakeResult("TEAM"))
    assert mgr.mission.calls == 0
    assert out in ("simple", "complex")


def test_is_conversational_does_not_fire_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    out = mgr.is_conversational("hi there", run_exec=lambda p: _FakeResult("CHAT"))
    assert mgr.mission.calls == 0
    assert out is True


# ---- needs_persistence: BOUNDED vs STANDING (auto continuous-mode arming) ---

def test_manager_no_runner_treats_free_text_as_bounded():
    # No backend → can't classify → safe default is BOUNDED (never silently
    # force an expensive 7x24 campaign onto a task that did not ask for one).
    assert Manager(runner=None).needs_persistence("optimize everything forever") is False


def test_manager_owns_bounded_vs_standing_decision():
    mgr = Manager()
    assert mgr.needs_persistence(
        "optimize as many kernels as possible", run_exec=lambda p: _FakeResult("STANDING")
    ) is True
    assert mgr.needs_persistence(
        "fix the flaky test in test_foo.py", run_exec=lambda p: _FakeResult("BOUNDED")
    ) is False


def test_needs_persistence_does_not_fire_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    out = mgr.needs_persistence(
        "keep improving this indefinitely", run_exec=lambda p: _FakeResult("STANDING")
    )
    assert mgr.mission.calls == 0
    assert out is True
