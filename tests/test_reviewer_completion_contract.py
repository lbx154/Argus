"""Tests for the reviewer-driven final-submission completion contract.

This contract replaces the retired hardcoded EMNLP validator gate
(``validate_full_paper_readiness`` and friends). Whole-project completion
is now certified by the L2 reviewer's full-pipeline checklist verdict:

* ``ReviewDecision.final_submission_certified`` is True only for a ``done``
  verdict scoped to ``final_submission`` whose checklist is non-empty and
  every item is satisfied with concrete evidence (fail-closed).
* The reviewer JSON parser must parse ``scope`` / ``checklist`` fail-closed.
* ``LifeSupervisor._journal_has_full_paper_gate_success`` reads the event
  timeline for a ``life.mission.completed`` event stamped ``final_submission_certified``,
  never a validator call.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.mission_view import load_mission_view
from argus_skill.core.models import ReviewDecision
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from argus_skill.life.supervisor._constants import PLAN_RETRY
from argus_skill.planner import PlannerVerdict, TaskSpec, WaitingContract
from argus_skill.reviewer import _find_decision_in_messages
from argus_skill.skills.vertical_select import persist_vertical

# ---------------------------------------------------------------------------
# ReviewDecision.final_submission_certified
# ---------------------------------------------------------------------------

def _decision(**kw) -> ReviewDecision:
    base = dict(
        status="done",
        reason="ok",
        next_action="",
        round_summary_markdown="# x",
        completion_summary_markdown="done",
    )
    base.update(kw)
    return ReviewDecision(**base)


def test_certified_when_all_items_satisfied_with_evidence() -> None:
    d = _decision(
        scope="final_submission",
        checklist=[
            {"item": "experiments", "satisfied": True, "evidence": "pytest 12 passed"},
            {"item": "paper", "satisfied": True, "evidence": "main.pdf 8 pages"},
        ],
    )
    assert d.final_submission_certified is True


def test_not_certified_when_scope_missing() -> None:
    d = _decision(
        scope="",
        checklist=[{"item": "a", "satisfied": True, "evidence": "e"}],
    )
    assert d.final_submission_certified is False


def test_not_certified_when_checklist_empty() -> None:
    d = _decision(scope="final_submission", checklist=[])
    assert d.final_submission_certified is False


def test_not_certified_when_item_unsatisfied() -> None:
    d = _decision(
        scope="final_submission",
        checklist=[
            {"item": "a", "satisfied": True, "evidence": "e"},
            {"item": "b", "satisfied": False, "evidence": ""},
        ],
    )
    assert d.final_submission_certified is False


def test_not_certified_when_evidence_blank() -> None:
    d = _decision(
        scope="final_submission",
        checklist=[{"item": "a", "satisfied": True, "evidence": "   "}],
    )
    assert d.final_submission_certified is False


def test_not_certified_when_status_continue() -> None:
    d = _decision(
        status="continue",
        scope="final_submission",
        checklist=[{"item": "a", "satisfied": True, "evidence": "e"}],
    )
    assert d.final_submission_certified is False


# ---------------------------------------------------------------------------
# Reviewer JSON parser: scope / checklist (fail-closed)
# ---------------------------------------------------------------------------

def _parse(payload: dict) -> ReviewDecision | None:
    return _find_decision_in_messages([json.dumps(payload)])


def test_parser_reads_scope_and_checklist() -> None:
    decision = _parse({
        "status": "done",
        "reason": "all items verified",
        "next_action": "",
        "round_summary_markdown": "# Review\n- ok",
        "completion_summary_markdown": "complete",
        "scope": "final_submission",
        "checklist": [
            {"item": "run", "satisfied": True, "evidence": "stdout shows acc=0.9"},
        ],
    })
    assert decision is not None
    assert decision.scope == "final_submission"
    assert decision.checklist == [
        {"item": "run", "satisfied": True, "evidence": "stdout shows acc=0.9"}
    ]
    assert decision.final_submission_certified is True


def test_parser_defaults_when_scope_checklist_absent() -> None:
    decision = _parse({
        "status": "done",
        "reason": "bounded task done",
        "next_action": "",
        "round_summary_markdown": "# Review\n- ok",
        "completion_summary_markdown": "done",
    })
    assert decision is not None
    assert decision.scope == ""
    assert decision.checklist == []
    assert decision.final_submission_certified is False


def test_parser_accepts_machine_certification_payload() -> None:
    payload = {
        "status": "done",
        "certified_claim_id": "C15/RUN-5",
        "exact_command_exit_zero": True,
        "byte_reproduction": True,
        "evidence_sha256": "960784a1e6c6d36d88307fdb10703852d4984640e672311bdef0dceea91f6a8e",
        "verifier_sha256": "e86260443b4e205ff44a27e15f12293c201aea8152c20dcf52b201f37af75426",
        "blockers": [],
        "checklist_counts": {"supported": 8, "not_applicable": 2},
        "checklist": {
            "solve.checkable-evidence": "PASS",
            "solve.lean-compiled": "NOT_APPLICABLE",
        },
        "scope_exclusions": ["global_theorem"],
    }

    decision = _parse(payload)

    assert decision is not None
    assert decision.status == "done"
    assert decision.next_action == ""
    assert decision.certification_payload == payload
    assert decision.checklist == [
        {
            "item": "solve.checkable-evidence",
            "satisfied": True,
            "evidence": "machine certification checklist status: PASS",
        },
        {
            "item": "solve.lean-compiled",
            "satisfied": True,
            "evidence": "machine certification checklist status: NOT_APPLICABLE",
        },
    ]


def test_parser_drops_malformed_scope() -> None:
    decision = _parse({
        "status": "done",
        "reason": "x",
        "next_action": "",
        "round_summary_markdown": "# Review",
        "completion_summary_markdown": "done",
        "scope": "garbage",
        "checklist": "not-a-list",
    })
    assert decision is not None
    assert decision.scope == ""
    assert decision.checklist == []


# ---------------------------------------------------------------------------
# LifeSupervisor journal gate: reviewer certification, not validators
# ---------------------------------------------------------------------------

def _make_supervisor(tmp_path: Path) -> LifeSupervisor:
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(budget=LifeBudget(), poll_interval_seconds=0.01)

    class _Sink:
        def handle_event(self, event: dict) -> None:  # noqa: D401
            pass

    class _Runner:
        pass

    sink = JsonlEventSink(_Sink(), life_dir=mem.root, verbosity="full")
    sup = LifeSupervisor(memory=mem, runner=_Runner(), sink=sink, config=cfg)
    _seed_research_vertical(sup)
    return sup


def _make_supervisor_cfg(tmp_path: Path, **cfg_kwargs) -> LifeSupervisor:
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(), poll_interval_seconds=0.01, **cfg_kwargs
    )

    class _Sink:
        def handle_event(self, event: dict) -> None:  # noqa: D401
            pass

    class _Runner:
        pass

    sink = JsonlEventSink(_Sink(), life_dir=mem.root, verbosity="full")
    sup = LifeSupervisor(memory=mem, runner=_Runner(), sink=sink, config=cfg)
    _seed_research_vertical(sup)
    return sup


def _seed_research_vertical(sup: LifeSupervisor) -> None:
    # A real mission decides + persists the vertical at run() bootstrap before any
    # gate read; resolve_vertical is fail-hard, so these unit tests (which invoke
    # gate methods directly, not run()) seed the research vertical explicitly.
    from argus_skill.skills.vertical_select import persist_vertical

    vertical = "research" if sup.config.full_paper_gate else "software"
    persist_vertical(sup._artifact_root(), vertical)
    sup._vertical_resolved = True


def _append_event(sup: LifeSupervisor, event: dict) -> None:
    path = Path(sup.memory.root) / "events.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _events(sup: LifeSupervisor) -> list[dict]:
    path = Path(sup.memory.root) / "events.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


class _ExplodingPlannerRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_exec(
        self, *, prompt, options, run_label, resume_thread_id=None  # noqa: ANN001
    ):
        self.calls += 1
        raise AssertionError("planner should not run for a bounded terminal project")


def _write_pipeline_state(root: Path, payload: dict) -> None:
    path = root / "research" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_backlog_metadata_paper_guidance_driven_by_explicit_flag(tmp_path: Path) -> None:
    """Paper-vs-bounded guidance keys on config.paper_mission, not objective text."""
    # An objective whose prose reads exactly like a paper task...
    item = BacklogItem.new(
        title="benchmark stage",
        objective="Work the EMNLP paper benchmark stage and resolve blockers.",
        tags=["scope:bounded"],
    )
    # ...is treated as a plain bounded task when paper_mission is OFF.
    sup_off = _make_supervisor_cfg(tmp_path / "off", paper_mission=False)
    out_off = sup_off._render_backlog_item_metadata(item)
    assert "bounded_task" in out_off
    assert "paper_optimization_task" not in out_off

    # ...and gets the long-horizon paper guidance only when paper_mission is ON,
    # even for a non-papery-looking objective.
    plain = BacklogItem.new(
        title="tune loader", objective="optimize the data loader", tags=["scope:bounded"]
    )
    sup_on = _make_supervisor_cfg(tmp_path / "on", paper_mission=True)
    out_on = sup_on._render_backlog_item_metadata(plain)
    assert "paper_optimization_task" in out_on
    assert "bounded_task" not in out_on


def test_open_ended_is_explicit_flag_not_objective_keywords(tmp_path: Path) -> None:
    """The post-project_done 'continue forever' behavior keys on config.open_ended.

    Previously the supervisor sniffed the objective text for markers like
    '7×24'/'ongoing'/'perpetual'. That keyword classifier is gone: an objective
    full of perpetual-sounding words must NOT implicitly enable open-ended mode,
    and a terse objective must be able to enable it via the flag.
    """
    from argus_skill.life import supervisor as sup_mod

    assert not hasattr(sup_mod, "_objective_is_open_ended")

    perpetual = _make_supervisor_cfg(
        tmp_path / "kw",
        continuous=True,
        continuous_objective="ongoing 7×24 perpetual never-ending self-improvement",
    )
    assert perpetual.config.open_ended is False

    terse = _make_supervisor_cfg(
        tmp_path / "flag",
        continuous=True,
        continuous_objective="ship it",
        open_ended=True,
    )
    assert terse.config.open_ended is True


def test_journal_gate_true_only_with_certified_entry(tmp_path: Path) -> None:
    sup = _make_supervisor(tmp_path)
    # No certified entry yet.
    assert sup._journal_has_full_paper_gate_success() is False

    # A completed mission that was NOT certified must not pass the gate.
    _append_event(
        sup,
        {
            "type": "life.mission.completed",
            "title": "bounded task",
            "success": True,
            "final_submission_certified": False,
        },
    )
    assert sup._journal_has_full_paper_gate_success() is False

    # A certified final-submission event passes the gate.
    _append_event(
        sup,
        {
            "type": "life.mission.completed",
            "title": "final submission",
            "success": True,
            "final_submission_certified": True,
        },
    )
    assert sup._journal_has_full_paper_gate_success() is True


def test_journal_gate_ignores_stale_validator_text(tmp_path: Path) -> None:
    """Legacy prose mentioning the old gate must NOT certify."""
    sup = _make_supervisor(tmp_path)
    _append_event(
        sup,
        {
            "type": "life.mission.completed",
            "title": "legacy",
            "success": True,
            "completion_summary": "validate-full-emnlp exited 0",
        },
    )
    assert sup._journal_has_full_paper_gate_success() is False


def _write_operator_external_lock(root: Path, *, target_exists: bool = False) -> None:
    lock_dir = root / "diagnosis"
    lock_dir.mkdir(parents=True, exist_ok=True)
    target = "reports/external/score.jsonl"
    if target_exists:
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_text("{}\n", encoding="utf-8")
    (lock_dir / "operator_only_external_blocker_lock_20260605.json").write_text(
        json.dumps(
            {
                "local_engineer_action_required_before_mount": False,
                "canonical_viability_verdict": "blocked_plan_stage_benchmark_package_viability",
                "next_owner": "operator_data_owner",
                "required_external_targets": [target],
            }
        ),
        encoding="utf-8",
    )


def test_project_done_becomes_waiting_for_operator_only_external_blocker(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_operator_external_lock(project)
    sup = _make_supervisor_cfg(tmp_path / "life", project_worktree=project, full_paper_gate=True)
    verdict = PlannerVerdict(project_done=True, reason="local work complete")

    converted = sup._defer_project_done_for_operator_external_blocker(verdict)

    assert converted.project_done is False
    assert converted.waiting is True
    assert converted.new_tasks == []
    assert "operator-only external benchmark blocker" in converted.waiting_reason


def test_project_done_still_requires_final_submission_guard_without_external_lock(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(tmp_path / "life", project_worktree=project, full_paper_gate=True)
    verdict = PlannerVerdict(project_done=True, reason="paper ready")

    converted = sup._defer_project_done_for_operator_external_blocker(verdict)

    assert converted.project_done is True
    assert converted.waiting is False


def test_project_done_still_requires_final_submission_guard_after_reentry(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_operator_external_lock(project, target_exists=True)
    sup = _make_supervisor_cfg(tmp_path / "life", project_worktree=project, full_paper_gate=True)
    verdict = PlannerVerdict(project_done=True, reason="paper ready")

    converted = sup._defer_project_done_for_operator_external_blocker(verdict)

    assert converted.project_done is True
    assert converted.waiting is False


def test_open_ended_project_done_idles_when_state_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "state.txt").write_text("stable\n", encoding="utf-8")
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
    )
    sup._vertical_resolved = True
    sup._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]
    sup.planner_runner = object()

    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(project_done=True, reason="verified terminal")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)

    assert sup._plan_next_work() == "planner_retry"
    assert sup._plan_next_work() == "planner_terminal_idle"
    assert calls == 1

    events = _events(sup)
    assert sum(
        1
        for event in events
        if event.get("type") == "life.planner.verdict"
        and event.get("project_done") is True
        and event.get("open_ended_objective") is True
    ) == 1
    verdict = next(
        event for event in events if event.get("type") == "life.planner.verdict"
    )
    assert verdict["status"] == "completed"
    assert "event_validation" not in verdict
    assert sum(
        1 for event in events if event.get("type") == "life.planner.terminal_idle"
    ) == 1


def test_production_topology_emits_one_terminal_verdict_and_calls_planner_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        project,
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
        artifact_root=project,
    )
    (project / "REVIEW.md").write_text("stable completion\n", encoding="utf-8")
    sup._vertical_resolved = True
    sup._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]
    sup.planner_runner = object()

    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(project_done=True, reason="verified terminal")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)

    assert sup._plan_next_work() == "planner_retry"
    assert sup._plan_next_work() == "planner_terminal_idle"
    assert calls == 1

    verdicts = [
        event
        for event in _events(sup)
        if event.get("type") == "life.planner.verdict"
    ]
    assert len(verdicts) == 1
    assert verdicts[0]["status"] == "completed"
    assert "event_validation" not in verdicts[0]
    mission_view = load_mission_view(project / "life")
    assert sum(
        item.get("title") == "Project reviewed"
        for item in mission_view["timeline"]
    ) == 1


def test_terminal_fingerprint_ignores_nonsemantic_timestamps(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        project,
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
        artifact_root=project,
    )
    state_path = project / "research" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updated_at"] = 1.0
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (project / "REVIEW.md").write_text("stable completion\n", encoding="utf-8")
    first = sup._open_ended_terminal_idle_signature()

    state["updated_at"] = 2.0
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    (project / "mission-view.json").write_text(
        json.dumps({"updated_at": 99.0, "event_sequence": 42}),
        encoding="utf-8",
    )

    assert sup._open_ended_terminal_idle_signature() == first


def test_terminal_fingerprint_changes_when_review_changes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        project,
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
        artifact_root=project,
    )
    review = project / "REVIEW.md"
    review.write_text("first completion\n", encoding="utf-8")
    first = sup._open_ended_terminal_idle_signature()

    review.write_text("completion invalidated by new evidence\n", encoding="utf-8")

    assert sup._open_ended_terminal_idle_signature() != first


def test_failed_terminal_verdict_delivery_retries_after_supervisor_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    memory = LifeMemory.open(project / "life")
    config = LifeSupervisorConfig(
        budget=LifeBudget(),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
        artifact_root=project,
    )

    class _Runner:
        pass

    class _FlakySink(JsonlEventSink):
        def __init__(self) -> None:
            super().__init__(None, life_dir=memory.root, verbosity="full")
            self.failed = False

        def handle_event(self, event: dict) -> bool:
            if event.get("type") == "life.planner.verdict" and not self.failed:
                self.failed = True
                return False
            return super().handle_event(event)

    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(project_done=True, reason="verified terminal")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    persist_vertical(project, "software")
    first = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=_FlakySink(),
        config=config,
        planner_runner=object(),
    )
    first._vertical_resolved = True
    first._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]

    assert first._plan_next_work() == "planner_retry"
    assert calls == 1
    assert not [
        event
        for event in _events(first)
        if event.get("type") == "life.planner.verdict"
    ]

    restarted = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=config,
        planner_runner=object(),
    )
    restarted._vertical_resolved = True
    restarted._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]

    assert restarted._plan_next_work() == "planner_retry"
    assert restarted._plan_next_work() == "planner_terminal_idle"
    assert calls == 1
    verdicts = [
        event
        for event in _events(restarted)
        if event.get("type") == "life.planner.verdict"
    ]
    assert len(verdicts) == 1
    assert verdicts[0]["status"] == "completed"


def test_non_open_ended_project_done_stops_normally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="bounded finish",
        open_ended=False,
        full_paper_gate=False,
        project_worktree=project,
    )
    sup._vertical_resolved = True
    sup._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]
    sup.planner_runner = object()

    def _plan_next(_planner, **_kwargs):
        return PlannerVerdict(project_done=True, reason="bounded done")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)

    assert sup._plan_next_work() is False
    events = _events(sup)
    assert any(
        event.get("type") == "life.planner.verdict"
        and event.get("project_done") is True
        for event in events
    )
    assert not any(event.get("type") == "life.planner.terminal_idle" for event in events)


def test_bounded_non_paper_terminal_stage_stops_without_planner(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="bounded speedrun",
        open_ended=False,
        paper_mission=False,
        full_paper_gate=False,
        project_worktree=project,
        artifact_root=project,
    )
    _write_pipeline_state(
        project,
        {
            "vertical": "speedrun",
            "current_stage": "report",
            "stages": {"report": {"status": "done"}},
        },
    )
    planner_runner = _ExplodingPlannerRunner()
    sup.planner_runner = planner_runner

    assert sup._plan_next_work() is False
    assert planner_runner.calls == 0

    verdict = next(
        event
        for event in _events(sup)
        if event.get("type") == "life.planner.verdict"
    )
    assert verdict["project_done"] is True
    assert verdict["status"] == "completed"
    assert "event_validation" not in verdict
    assert verdict["enqueued_tasks"] == 0
    assert verdict["input_tokens"] == 0
    assert verdict["cached_input_tokens"] == 0
    assert verdict["output_tokens"] == 0
    assert verdict["cost_usd"] == 0.0
    assert any(
        event.get("type") == "life.status"
        and "bounded speedrun vertical reached terminal stage" in event.get("text", "")
        for event in _events(sup)
    )


def test_full_paper_terminal_stage_still_requires_certification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="finish the research paper",
        open_ended=False,
        paper_mission=True,
        full_paper_gate=True,
        project_worktree=project,
        artifact_root=project,
    )
    _write_pipeline_state(
        project,
        {
            "vertical": "research",
            "current_stage": "submission",
            "stages": {"submission": {"status": "done"}},
        },
    )
    sup.planner_runner = object()
    planner_calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return PlannerVerdict(project_done=True, reason="paper ready")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)

    assert sup._plan_next_work() is True
    assert planner_calls == 1
    assert any(
        "scope:final_submission" in (item.tags or [])
        for item in sup.memory.backlog.all()
    )


def test_done_but_uncertified_final_submission_can_be_retried(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="finish the research paper",
        open_ended=False,
        paper_mission=True,
        full_paper_gate=True,
        project_worktree=project,
        artifact_root=project,
    )
    _write_pipeline_state(
        project,
        {
            "vertical": "research",
            "current_stage": "submission",
            "stages": {"submission": {"status": "done"}},
        },
    )
    sup.planner_runner = object()
    planner_calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return PlannerVerdict(project_done=True, reason="paper ready")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)

    assert sup._plan_next_work() is True
    [final_item] = sup.memory.backlog.all()
    assert "scope:final_submission" in (final_item.tags or [])
    sup.memory.backlog.mark_done(final_item.id)

    assert sup._plan_next_work() is True
    assert planner_calls == 2
    items = sup.memory.backlog.all()
    assert len(items) == 2
    assert {item.status for item in items} == {"done", "pending"}


def test_open_ended_project_change_replans_and_enqueues_tasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    marker = project / "state.txt"
    marker.write_text("before\n", encoding="utf-8")
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
    )
    sup._vertical_resolved = True
    sup._current_pipeline_stage = lambda: "done"  # type: ignore[method-assign]
    sup.planner_runner = object()
    sup._last_open_ended_project_done_signature = (
        sup._open_ended_terminal_idle_signature()
    )
    marker.write_text("after\n", encoding="utf-8")

    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(
            project_done=False,
            reason="new work appeared",
            new_tasks=[
                TaskSpec(
                    title="Handle changed state",
                    objective="Inspect the changed state and act on it.",
                    impact_score=5,
                    impact_area="fresh_state",
                    evidence="project file changed after terminal idle",
                )
            ],
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)

    assert sup._plan_next_work() is True
    assert calls == 1
    assert any(item.title == "Handle changed state" for item in sup.memory.backlog.all())


def test_item_is_final_submission_prefers_structured_tag() -> None:
    from argus_skill.life.memory import BacklogItem
    from argus_skill.life.supervisor import (
        LifeSupervisor,
        _legacy_final_submission_marker,
    )

    # Structured scope tag is the primary signal — objective prose is
    # irrelevant when the tag is present.
    tagged = BacklogItem.new(
        title="Prove final submission readiness",
        objective="anything at all",
        tags=["planner", "scope:final_submission"],
    )
    assert LifeSupervisor._item_is_final_submission(tagged) is True

    bounded = BacklogItem.new(
        title="t",
        objective="add a unit test for the parser",
        tags=["planner", "scope:bounded"],
    )
    assert LifeSupervisor._item_is_final_submission(bounded) is False

    # Legacy items (persisted before scope tagging) fall back to the
    # objective-prose marker so resumed daemons don't regress.
    legacy = BacklogItem.new(
        title="t",
        objective="Project-final task. Scope: final_submission. Complete the pipeline.",
        tags=[],
    )
    assert LifeSupervisor._item_is_final_submission(legacy) is True

    # The legacy recognizer keys on the marker, not arbitrary prose.
    assert _legacy_final_submission_marker(
        "## Backlog item metadata\n- planner_scope: final_submission"
    ) is True
    assert _legacy_final_submission_marker(
        "Bounded task: add a unit test for the parser."
    ) is False


# ---------------------------------------------------------------------------
# Planner livelock fix: memory echo-chamber de-poison (A), verification-probe
# stall escalation (C), and the current-reality staleness note (B').
# ---------------------------------------------------------------------------


def _waiting_supervisor(tmp_path: Path, monkeypatch, *, reason: str = "gpu busy") -> LifeSupervisor:
    """A continuous, non-open-ended supervisor whose planner always returns a
    ``waiting`` verdict — the livelock shape we are hardening against."""
    project = tmp_path / "project"
    project.mkdir()
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="keep optimizing",
        open_ended=False,
        full_paper_gate=False,
        project_worktree=project,
    )
    sup._vertical_resolved = True
    sup.planner_runner = object()

    def _plan_next(_planner, **_kwargs):
        return PlannerVerdict(
            project_done=False, reason=reason, waiting=True, waiting_reason=reason
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    return sup


def test_should_journal_idle_repeat_heartbeat_gate(tmp_path: Path) -> None:
    """The append-gate is keyed on KIND alone (the reason text varies every
    cycle), so in-window repeats of a kind are suppressed while a different
    kind still admits an entry; the per-cycle event/status are unaffected."""
    sup = _make_supervisor(tmp_path)
    assert sup._should_journal_idle_repeat("planner_waiting") is True
    assert sup._should_journal_idle_repeat("planner_waiting") is False
    assert sup._should_journal_idle_repeat("planner_idle") is True
    assert sup._should_journal_idle_repeat("planner_idle") is False


def test_repeated_planner_waiting_emits_structured_events(
    tmp_path: Path, monkeypatch
) -> None:
    """Waiting cycles are structured events, not journal prose."""
    sup = _waiting_supervisor(tmp_path, monkeypatch)
    statuses: list[str] = []
    sup._emit_status = statuses.append  # type: ignore[method-assign]

    calls = {"n": 0}

    def _vary(_planner, **_kwargs):
        calls["n"] += 1
        reason = f"awaiting external job; fresh audit #{calls['n']}"
        return PlannerVerdict(
            project_done=False, reason=reason, waiting=True, waiting_reason=reason
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _vary)

    for _ in range(3):  # below K, so no probe yet
        assert sup._plan_next_work() == "awaiting_external"

    waiting = [
        event for event in _events(sup) if event.get("type") == "life.planner.waiting"
    ]
    assert len(waiting) == 3
    assert len(statuses) == 3  # status still fires every cycle


def test_k_idle_cycles_dispatch_one_verification_probe(tmp_path: Path, monkeypatch) -> None:
    """Change C: after K consecutive idle cycles a single verification-probe
    mission is enqueued, the idle counter resets, and the dispatch is audited."""
    from argus_skill.life.supervisor._core import _VERIFICATION_PROBE_AFTER_IDLE_CYCLES as K

    sup = _waiting_supervisor(tmp_path, monkeypatch, reason="gpu busy")

    for _ in range(K - 1):
        assert sup._plan_next_work() == "awaiting_external"
        assert [
            it for it in sup.memory.backlog.all() if "verification_probe" in (it.tags or [])
        ] == []

    assert sup._plan_next_work() is True  # K-th cycle dispatches the probe
    probes = [
        it for it in sup.memory.backlog.all() if "verification_probe" in (it.tags or [])
    ]
    assert len(probes) == 1
    assert probes[0].status == "pending"
    assert sup._consecutive_idle_planner_cycles == 0
    events = _events(sup)
    waiting = [event for event in events if event.get("type") == "life.planner.waiting"]
    assert len(waiting) == K
    assert any(
        event.get("type") == "life.planner.verification_probe" for event in events
    )


def test_verification_probe_not_restacked_while_pending(tmp_path: Path, monkeypatch) -> None:
    """Change C: a pending probe (and the cooldown) prevent a second probe from
    being enqueued even after K more idle cycles."""
    from argus_skill.life.supervisor._core import _VERIFICATION_PROBE_AFTER_IDLE_CYCLES as K

    sup = _waiting_supervisor(tmp_path, monkeypatch, reason="gpu busy")
    for _ in range(2 * K):
        sup._plan_next_work()

    probes = [
        it for it in sup.memory.backlog.all() if "verification_probe" in (it.tags or [])
    ]
    assert len(probes) == 1


def test_waiting_contract_allows_only_one_probe_per_token_across_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.life.supervisor._core import (
        _VERIFICATION_PROBE_AFTER_IDLE_CYCLES as K,
    )

    state = {"token": "source-missing-v1"}

    def _waiting(_planner, **_kwargs):
        return PlannerVerdict(
            project_done=False,
            reason="licensed source unavailable",
            waiting=True,
            waiting_reason="operator must provide the licensed source",
            waiting_contract=WaitingContract(
                blocker_fingerprint="source:chen-2003",
                recheck_condition="a licensed full-text path appears",
                recheck_token=state["token"],
                allow_verification_probe=True,
                recheck_after_seconds=0,
            ),
        )

    sup = _waiting_supervisor(tmp_path, monkeypatch)
    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _waiting)
    for _ in range(K):
        sup._plan_next_work()
    probes = [
        item
        for item in sup.memory.backlog.all()
        if "verification_probe" in (item.tags or [])
    ]
    assert len(probes) == 1
    sup.memory.backlog.update(probes[0].id, status="done")

    restarted = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="keep optimizing",
        open_ended=False,
        full_paper_gate=False,
        project_worktree=tmp_path / "project",
    )
    restarted._vertical_resolved = True
    restarted.planner_runner = object()
    for _ in range(2 * K):
        assert restarted._plan_next_work() == "awaiting_external"
    assert len([
        item
        for item in restarted.memory.backlog.all()
        if "verification_probe" in (item.tags or [])
    ]) == 1

    state["token"] = "source-path-observed-v2"
    for _ in range(K):
        restarted._plan_next_work()
    probes = [
        item
        for item in restarted.memory.backlog.all()
        if "verification_probe" in (item.tags or [])
    ]
    assert len(probes) == 2
    restarted.memory.backlog.update(probes[-1].id, status="done")

    state["token"] = "source-missing-v1"
    restarted._last_verification_probe_at = 0.0
    for _ in range(K):
        assert restarted._plan_next_work() == "awaiting_external"
    assert len([
        item
        for item in restarted.memory.backlog.all()
        if "verification_probe" in (item.tags or [])
    ]) == 2


def test_waiting_contract_can_disable_verification_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.life.supervisor._core import (
        _VERIFICATION_PROBE_AFTER_IDLE_CYCLES as K,
    )

    sup = _waiting_supervisor(tmp_path, monkeypatch)

    def _waiting(_planner, **_kwargs):
        return PlannerVerdict(
            project_done=False,
            reason="operator-only source blocker",
            waiting=True,
            waiting_reason="operator must provide the licensed source",
            waiting_contract=WaitingContract(
                blocker_fingerprint="source:chen-2003",
                recheck_condition="a licensed full-text path appears",
                recheck_token="source-missing-v1",
                allow_verification_probe=False,
                recheck_after_seconds=0,
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _waiting)
    for _ in range(2 * K):
        assert sup._plan_next_work() == "awaiting_external"

    assert not [
        item
        for item in sup.memory.backlog.all()
        if "verification_probe" in (item.tags or [])
    ]


def test_waiting_contract_honors_recheck_delay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.life.supervisor._core import (
        _VERIFICATION_PROBE_AFTER_IDLE_CYCLES as K,
    )

    now = {"value": 1000.0}
    monkeypatch.setattr(
        "argus_skill.life.supervisor._planning_context.time.time",
        lambda: now["value"],
    )
    sup = _waiting_supervisor(tmp_path, monkeypatch)

    def _waiting(_planner, **_kwargs):
        return PlannerVerdict(
            project_done=False,
            reason="external job has not reached its checkpoint",
            waiting=True,
            waiting_reason="wait for checkpoint 10",
            waiting_contract=WaitingContract(
                blocker_fingerprint="job:training-42",
                recheck_condition="checkpoint 10 is published",
                recheck_token="checkpoint-9",
                allow_verification_probe=True,
                recheck_after_seconds=600,
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _waiting)
    for _ in range(2 * K):
        assert sup._plan_next_work() == "awaiting_external"

    now["value"] += 600
    assert sup._plan_next_work() is True


def test_inactive_waiting_contract_is_not_injected_into_planner_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sup = _waiting_supervisor(tmp_path, monkeypatch)
    contract = WaitingContract(
        blocker_fingerprint="source:chen-2003",
        recheck_condition="a licensed full-text path appears",
        recheck_token="source-missing-v1",
        allow_verification_probe=False,
        recheck_after_seconds=0,
    )
    assert sup._persist_planner_waiting_contract(contract) is not None
    assert "source:chen-2003" in sup._planner_waiting_contract_runtime_note()

    sup._deactivate_planner_waiting_contract()

    assert sup._planner_waiting_contract_runtime_note() == ""


def test_waiting_contract_state_is_scoped_by_continuous_objective(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sup = _waiting_supervisor(tmp_path, monkeypatch)
    first_path = sup._planner_waiting_contract_path()
    contract = WaitingContract(
        blocker_fingerprint="source:chen-2003",
        recheck_condition="a licensed full-text path appears",
        recheck_token="source-missing-v1",
        allow_verification_probe=False,
        recheck_after_seconds=0,
    )
    assert sup._persist_planner_waiting_contract(contract) is not None

    sup.config.continuous_objective = "a different operator objective"
    second_path = sup._planner_waiting_contract_path()

    assert second_path != first_path
    assert sup._load_planner_waiting_contract_state() is None
    assert first_path.exists()


def test_pending_probe_reservation_reconciles_against_durable_backlog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sup = _waiting_supervisor(tmp_path, monkeypatch)
    contract = WaitingContract(
        blocker_fingerprint="job:training-42",
        recheck_condition="checkpoint 10 is published",
        recheck_token="checkpoint-9",
        allow_verification_probe=True,
        recheck_after_seconds=0,
    )
    assert sup._persist_planner_waiting_contract(contract) is not None

    missing = BacklogItem.new(title="probe", objective="probe current state")
    assert sup._reserve_planner_waiting_contract_probe(
        contract,
        item_id=missing.id,
    )
    state = sup._load_planner_waiting_contract_state()
    assert state is not None
    reconciled = sup._reconcile_planner_waiting_contract_probe(state)
    assert reconciled is not None
    assert reconciled["pending_probe"] is None
    assert reconciled["probed_conditions"] == []

    durable = BacklogItem.new(title="probe", objective="probe current state")
    assert sup._reserve_planner_waiting_contract_probe(
        contract,
        item_id=durable.id,
    )
    sup.memory.backlog.add(durable)
    state = sup._load_planner_waiting_contract_state()
    assert state is not None
    reconciled = sup._reconcile_planner_waiting_contract_probe(state)
    assert reconciled is not None
    assert reconciled["pending_probe"] is None
    assert {
        "blocker_fingerprint": "job:training-42",
        "recheck_token": "checkpoint-9",
    }.items() <= reconciled["probed_conditions"][0].items()


def test_planner_runtime_carries_idle_stall_note(tmp_path: Path) -> None:
    """Change B': the runtime context gains a CURRENT-REALITY note once the
    planner has idled, and is empty before then."""
    sup = _make_supervisor_cfg(tmp_path / "life")
    assert sup._planner_runtime_with_idle_note() == ""

    sup._consecutive_idle_planner_cycles = 3
    note = sup._planner_runtime_with_idle_note()
    assert "CURRENT-REALITY CHECK" in note
    assert "idled 3" in note


# ---------------------------------------------------------------------------
# Controllability: no-progress-streak operator escalation + notify surfacing.
# ---------------------------------------------------------------------------


def test_no_progress_streak_escalates_to_operator(tmp_path: Path) -> None:
    """After N consecutive missions the reviewer judged forward_progress=false,
    the harness emits ONE operator-notified stall escalation and resets."""
    from argus_skill.life.supervisor._core import (
        _STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS as N,
    )

    sup = _make_supervisor(tmp_path)
    for _ in range(N - 1):
        sup._update_no_progress_streak(
            kind="mission_complete", report={"forward_progress": False}
        )
        assert not any(
            event.get("type") == "life.planner.stall_escalation"
            for event in _events(sup)
        )

    sup._update_no_progress_streak(
        kind="mission_complete", report={"forward_progress": False}
    )
    esc = [
        event
        for event in _events(sup)
        if event.get("type") == "life.planner.stall_escalation"
    ]
    assert len(esc) == 1
    assert sup._consecutive_no_progress_missions == 0


def test_no_progress_streak_reset_by_real_progress(tmp_path: Path) -> None:
    """A mission the reviewer judged forward_progress=true resets the streak, so
    the escalation does not fire on stale accumulation."""
    from argus_skill.life.supervisor._core import (
        _STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS as N,
    )

    sup = _make_supervisor(tmp_path)
    for _ in range(N - 1):
        sup._update_no_progress_streak(
            kind="mission_complete", report={"forward_progress": False}
        )
    sup._update_no_progress_streak(
        kind="mission_complete", report={"forward_progress": True}
    )
    assert sup._consecutive_no_progress_missions == 0
    sup._update_no_progress_streak(
        kind="mission_complete", report={"forward_progress": False}
    )
    assert not any(
        event.get("type") == "life.planner.stall_escalation"
        for event in _events(sup)
    )


def test_no_progress_streak_ignores_unknown_and_non_complete(tmp_path: Path) -> None:
    """Only completed missions with an explicit forward_progress=false count;
    failures and missing-report missions never trip the escalation."""
    sup = _make_supervisor(tmp_path)
    sup._update_no_progress_streak(
        kind="mission_failed", report={"forward_progress": False}
    )
    sup._update_no_progress_streak(kind="mission_complete", report={})
    sup._update_no_progress_streak(kind="mission_complete", report="not-a-dict")
    assert sup._consecutive_no_progress_missions == 0
    assert not any(
        event.get("type") == "life.planner.stall_escalation"
        for event in _events(sup)
    )


def test_stuck_state_events_are_high_value() -> None:
    """Blocking/stall event types are persisted in the default signal log."""
    from argus_skill.life.event_log import HIGH_VALUE_EVENT_TYPES

    for event_type in (
        "life.planner.waiting",
        "life.planner.terminal_idle",
        "life.lifecycle.block",
        "life.planner.verification_probe",
        "life.planner.stall_escalation",
    ):
        assert event_type in HIGH_VALUE_EVENT_TYPES


def test_probe_and_escalation_emit_events(tmp_path: Path, monkeypatch) -> None:
    """Verification probes and stall escalations are first-class events."""
    from argus_skill.life.supervisor._core import (
        _STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS as N,
    )
    from argus_skill.life.supervisor._core import (
        _VERIFICATION_PROBE_AFTER_IDLE_CYCLES as K,
    )

    sup = _waiting_supervisor(tmp_path, monkeypatch, reason="neighbor present")
    for _ in range(K):
        sup._plan_next_work()
    assert any(
        event.get("type") == "life.planner.verification_probe"
        for event in _events(sup)
    )

    for _ in range(N):
        sup._update_no_progress_streak(
            kind="mission_complete", report={"forward_progress": False}
        )
    assert any(
        event.get("type") == "life.planner.stall_escalation"
        for event in _events(sup)
    )
