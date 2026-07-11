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

from argus_skill.core.models import ReviewDecision
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from argus_skill.planner import PlannerVerdict, TaskSpec
from argus_skill.reviewer import _find_decision_in_messages

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

    persist_vertical(sup._artifact_root(), "research")


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
    assert sum(
        1 for event in events if event.get("type") == "life.planner.terminal_idle"
    ) == 1


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


def test_restart_verdict_still_handoffs_without_terminal_idle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    restart_reasons: list[str] = []
    sup = _make_supervisor_cfg(
        tmp_path / "life",
        continuous=True,
        continuous_objective="keep improving",
        open_ended=True,
        full_paper_gate=False,
        project_worktree=project,
        planner_restart_handler=lambda reason: restart_reasons.append(reason) or True,
    )
    sup._vertical_resolved = True
    sup._current_pipeline_stage = lambda: "running"  # type: ignore[method-assign]
    sup.planner_runner = object()

    def _plan_next(_planner, **_kwargs):
        return PlannerVerdict(
            project_done=False,
            reason="runtime changed",
            restart_daemon=True,
            restart_reason="reload runtime",
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)

    assert sup._plan_next_work() == "daemon_handoff"
    assert restart_reasons == ["reload runtime"]


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


def test_planner_runtime_carries_idle_stall_note(tmp_path: Path) -> None:
    """Change B': the runtime context gains a CURRENT-REALITY note once the
    planner has idled, and is identical to the base context before then."""
    sup = _make_supervisor_cfg(tmp_path / "life")
    assert sup._planner_runtime_with_idle_note() == sup._planner_project_context()

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
