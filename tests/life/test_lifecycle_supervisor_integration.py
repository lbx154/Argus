"""Tests for D2: F5 supervisor integration.

Covers:

* project_lifecycle_io — load / write / append_event round-trip,
  malformed-file recovery, overlay onto observable status.
* infer_observable_status — observable signals (evidence dir mtime,
  draft / submission artifacts) drive the initial state.
* CLI --lifecycle-resume / --lifecycle-archive — write-side transitions.

The full supervisor.tick() integration is exercised via a small custom
LifeSupervisor stub that drives the public ``_maybe_block_on_lifecycle``
entry point with a fake memory and fake budget.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus_skill.life.project_lifecycle import (
    LifecycleEvent,
    ProjectState,
    ProjectStatus,
    infer_observable_status,
)
from argus_skill.life.project_lifecycle_io import (
    LifecycleIOError,
    append_event,
    apply_persisted_to_status,
    lifecycle_path,
    load_history,
    load_persisted,
    write_persisted,
)

# ---------------------------------------------------------------------------
# infer_observable_status
# ---------------------------------------------------------------------------


def test_fresh_dir_is_incubating(tmp_path: Path) -> None:
    status = infer_observable_status(tmp_path)
    assert status.state == ProjectState.INCUBATING
    assert status.last_evidence_at is None
    assert status.has_draft is False
    assert status.has_submission_artifact is False


def test_evidence_dir_with_bundle_promotes_to_running(tmp_path: Path) -> None:
    bundle = tmp_path / "benchmarks" / "evidence" / "demo"
    bundle.mkdir(parents=True)
    (bundle / "summary.tsv").write_text("x\n", encoding="utf-8")

    status = infer_observable_status(tmp_path)
    assert status.state == ProjectState.RUNNING
    assert status.last_evidence_at is not None


def test_paper_main_tex_promotes_to_writing(tmp_path: Path) -> None:
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")

    status = infer_observable_status(tmp_path)
    assert status.state == ProjectState.WRITING
    assert status.has_draft is True


def test_submission_artifact_keeps_writing_initial_state(tmp_path: Path) -> None:
    # The init heuristic still puts us in WRITING when only the PDF
    # exists; the policy engine will later promote WRITING → DONE on
    # decide_next_state.
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "main.pdf").write_text("pdf", encoding="utf-8")

    status = infer_observable_status(tmp_path)
    assert status.has_submission_artifact is True
    assert status.state == ProjectState.WRITING


# ---------------------------------------------------------------------------
# project_lifecycle_io
# ---------------------------------------------------------------------------


def _status(state: ProjectState = ProjectState.RUNNING) -> ProjectStatus:
    now = datetime.now(timezone.utc)
    return ProjectStatus(
        project_id="proj",
        state=state,
        created_at=now,
        last_state_change_at=now,
    )


def test_load_persisted_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_persisted(tmp_path) == {}


def test_write_then_load_persisted_round_trip(tmp_path: Path) -> None:
    status = _status(ProjectState.QUARANTINED)
    event = LifecycleEvent(
        at=status.last_state_change_at,
        from_state=ProjectState.RUNNING,
        to_state=ProjectState.QUARANTINED,
        reason="manual_test",
    )
    write_persisted(tmp_path, status=status, history=[event])

    persisted = load_persisted(tmp_path)
    assert persisted["state"] == "quarantined"
    assert isinstance(persisted["history"], list)
    assert len(persisted["history"]) == 1
    assert persisted["history"][0]["reason"] == "manual_test"


def test_load_persisted_rejects_non_object_top_level(tmp_path: Path) -> None:
    path = lifecycle_path(tmp_path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(LifecycleIOError):
        load_persisted(tmp_path)


def test_load_persisted_rejects_malformed_json(tmp_path: Path) -> None:
    path = lifecycle_path(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LifecycleIOError):
        load_persisted(tmp_path)


def test_apply_persisted_overlays_state(tmp_path: Path) -> None:
    observed = _status(ProjectState.RUNNING)
    overlaid = apply_persisted_to_status(observed, {"state": "quarantined"})
    assert overlaid.state == ProjectState.QUARANTINED


def test_apply_persisted_ignores_unknown_state(tmp_path: Path) -> None:
    observed = _status(ProjectState.RUNNING)
    overlaid = apply_persisted_to_status(observed, {"state": "nonsense"})
    # falls through, observed state wins
    assert overlaid.state == ProjectState.RUNNING


def test_append_event_extends_history(tmp_path: Path) -> None:
    status = _status(ProjectState.RUNNING)
    e1 = LifecycleEvent(
        at=datetime.now(timezone.utc),
        from_state=ProjectState.INCUBATING,
        to_state=ProjectState.RUNNING,
        reason="first_evidence",
    )
    append_event(tmp_path, new_status=status, event=e1)

    e2 = LifecycleEvent(
        at=datetime.now(timezone.utc),
        from_state=ProjectState.RUNNING,
        to_state=ProjectState.QUARANTINED,
        reason="user_quarantine",
    )
    quarantined = _status(ProjectState.QUARANTINED)
    append_event(tmp_path, new_status=quarantined, event=e2)

    history = load_history(tmp_path)
    assert len(history) == 2
    assert history[0].reason == "first_evidence"
    assert history[1].reason == "user_quarantine"


def test_load_history_tolerates_malformed_entries(tmp_path: Path) -> None:
    # Write a payload where one history entry is missing required fields.
    path = lifecycle_path(tmp_path)
    path.write_text(
        json.dumps(
            {
                "state": "running",
                "history": [
                    {"at": "2026-05-01T00:00:00+00:00",
                     "from_state": "incubating", "to_state": "running",
                     "reason": "ok"},
                    {"bad": "entry"},
                    {"at": "2026-05-02T00:00:00+00:00",
                     "from_state": "running", "to_state": "quarantined",
                     "reason": "ok-2"},
                ],
            }
        ),
        encoding="utf-8",
    )
    history = load_history(tmp_path)
    # Only the two well-formed entries survive.
    assert len(history) == 2
    assert history[0].reason == "ok"
    assert history[1].reason == "ok-2"


# ---------------------------------------------------------------------------
# CLI: --lifecycle-resume / --lifecycle-archive
# ---------------------------------------------------------------------------


def test_cli_archive_writes_persisted_state(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-archive", "--project-root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ARCHIVED" in proc.stdout.upper() or "archived" in proc.stdout

    persisted = load_persisted(tmp_path)
    assert persisted["state"] == "archived"


def test_cli_resume_refuses_non_quarantined(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-resume", "--project-root", str(tmp_path)],
        capture_output=True, text=True,
    )
    # Fresh project is incubating, not quarantined → resume refuses.
    assert proc.returncode == 1
    assert "cannot resume" in proc.stderr or "cannot resume" in proc.stdout


def test_cli_resume_after_quarantine_returns_to_running(tmp_path: Path) -> None:
    # Seed evidence so the inferred state on resume is RUNNING.
    bundle = tmp_path / "benchmarks" / "evidence" / "demo"
    bundle.mkdir(parents=True)
    (bundle / "summary.tsv").write_text("x\n", encoding="utf-8")

    # Manually persist quarantine state.
    qstatus = ProjectStatus(
        project_id=tmp_path.name,
        state=ProjectState.QUARANTINED,
        created_at=datetime.now(timezone.utc),
    )
    write_persisted(tmp_path, status=qstatus, history=[])

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-resume", "--project-root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr

    persisted = load_persisted(tmp_path)
    assert persisted["state"] == "running"


def test_cli_status_shows_persisted_marker(tmp_path: Path) -> None:
    # Persist a quarantine, then run --lifecycle-status; it should show
    # effective_state = quarantined (persisted) even though observable
    # signals say incubating.
    qstatus = ProjectStatus(
        project_id=tmp_path.name,
        state=ProjectState.QUARANTINED,
        created_at=datetime.now(timezone.utc),
    )
    write_persisted(tmp_path, status=qstatus, history=[])

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-status", "--project-root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "observed_state    : incubating" in proc.stdout
    assert "effective_state   : quarantined  (persisted)" in proc.stdout
    assert "token_allocatable : False" in proc.stdout


def test_cli_mutual_exclusion_blocks_two_lifecycle_flags(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-resume", "--lifecycle-archive",
         "--project-root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "mutually exclusive" in proc.stderr


# ---------------------------------------------------------------------------
# F5 gate: EMNLP completion-authority guard (premature-DONE from preflight PDF)
# ---------------------------------------------------------------------------
#
# The lifecycle gate must NOT terminate a full-EMNLP project just because a
# ``paper/main.pdf`` exists (it is compiled for format preflight long before
# the draft is submission-ready). DONE is terminal + non-allocatable, so a
# premature flip starves the project of tokens forever. The supervisor defers
# to the L2 reviewer's ``final_submission`` certification instead.
from types import SimpleNamespace  # noqa: E402

from argus_skill.life.supervisor import LifeSupervisor  # noqa: E402


class _GateStub:
    """Minimal stand-in driving ``_maybe_block_on_lifecycle`` in isolation."""

    def __init__(
        self,
        *,
        project_root: Path,
        memory_root: Path,
        full_emnlp_gate: bool,
        certified: bool,
    ) -> None:
        memory_root.mkdir(parents=True, exist_ok=True)
        self.config = SimpleNamespace(full_emnlp_gate=full_emnlp_gate)
        self.journal_entries: list[object] = []
        self.emitted: list[str] = []
        self.events: list[dict] = []
        self.memory = SimpleNamespace(
            root=memory_root,
            journal=SimpleNamespace(append=self.journal_entries.append),
        )
        self._project_root = project_root
        self._certified = certified

    # --- methods _maybe_block_on_lifecycle depends on ---
    def _project_workdir(self) -> Path:
        return self._project_root

    def _lifecycle_root(self) -> Path:
        return self.memory.root

    def _migrate_global_lifecycle_if_needed(self, per_root: Path) -> None:
        return None

    def _lifecycle_budget_snapshot(self) -> tuple[float, float]:
        return (0.0, 0.0)

    def _journal_has_full_emnlp_gate_success(self) -> bool:
        return self._certified

    def _effective_full_emnlp_gate(self, _workdir: object) -> bool:
        # These gate tests model a default-research project, where the
        # vertical-effective gate equals the raw config flag.
        return bool(self.config.full_emnlp_gate)

    def _emit_status(self, text: str) -> None:
        self.emitted.append(text)

    def _emit(self, event: dict) -> None:
        self.events.append(event)

    def block(self):
        item = SimpleNamespace(id="item-1", title="finish paper")
        return LifeSupervisor._maybe_block_on_lifecycle(self, item)


def _with_preflight_pdf(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    (project_root / "paper").mkdir(parents=True)
    (project_root / "paper" / "main.pdf").write_text("pdf", encoding="utf-8")
    return project_root


def _journal_kinds(stub: _GateStub) -> list[str]:
    return [getattr(e, "kind", None) for e in stub.journal_entries]


def test_gate_suppresses_premature_done_for_uncertified_emnlp(tmp_path: Path) -> None:
    # main.pdf exists but reviewer has NOT certified → no DONE, dispatch
    # proceeds, and the suppressed transition is never journaled/persisted.
    stub = _GateStub(
        project_root=_with_preflight_pdf(tmp_path),
        memory_root=tmp_path / "mem",
        full_emnlp_gate=True,
        certified=False,
    )
    result = stub.block()
    assert result is None  # allocatable → dispatch proceeds
    assert not any(e.get("type") == "life.lifecycle.transition" for e in stub.events)
    assert not any(e.get("type") == "life.lifecycle.block" for e in stub.events)
    # nothing premature was persisted
    assert load_persisted(tmp_path / "mem") == {}


def test_gate_repairs_existing_persisted_done_once(tmp_path: Path) -> None:
    memory_root = tmp_path / "mem"
    memory_root.mkdir()
    # Seed a pre-existing bad DONE with prior history (as the live wedge had).
    write_persisted(
        memory_root,
        status=_status(ProjectState.DONE),
        history=[
            LifecycleEvent(
                at=datetime.now(timezone.utc),
                from_state=ProjectState.WRITING,
                to_state=ProjectState.DONE,
                reason="submission_artifact_present",
            )
        ],
    )
    stub = _GateStub(
        project_root=_with_preflight_pdf(tmp_path),
        memory_root=memory_root,
        full_emnlp_gate=True,
        certified=False,
    )
    result = stub.block()
    assert result is None  # repaired → allocatable
    persisted = load_persisted(memory_root)
    assert persisted["state"] == "writing"  # repaired
    # repair preserved prior history and appended the repair event
    history = load_history(memory_root)
    assert history[-1].to_state == ProjectState.WRITING
    assert history[-1].reason == "full_emnlp_gate_not_certified"
    assert any(h.reason == "submission_artifact_present" for h in history)
    assert any(
        event.get("type") == "life.lifecycle.transition"
        and event.get("to_state") == "writing"
        for event in stub.events
    )

    # A second tick must NOT repair again or re-spam the event timeline: persisted
    # is now WRITING, decide_next_state re-fires DONE which is suppressed.
    stub2 = _GateStub(
        project_root=stub._project_root,
        memory_root=memory_root,
        full_emnlp_gate=True,
        certified=False,
    )
    assert stub2.block() is None
    assert not any(e.get("type") == "life.lifecycle.transition" for e in stub2.events)
    assert load_persisted(memory_root)["state"] == "writing"


def test_gate_allows_done_when_reviewer_certified(tmp_path: Path) -> None:
    # full_emnlp_gate True AND certified → the PDF→DONE transition stands,
    # so the project is correctly terminated and dispatch is blocked.
    stub = _GateStub(
        project_root=_with_preflight_pdf(tmp_path),
        memory_root=tmp_path / "mem",
        full_emnlp_gate=True,
        certified=True,
    )
    result = stub.block()
    assert result is not None
    assert result["status"] == "lifecycle_block"
    assert result["lifecycle_state"] == "done"


def test_gate_keeps_legacy_done_when_gate_disabled(tmp_path: Path) -> None:
    # Non-EMNLP mission (full_emnlp_gate False): old behavior is unchanged —
    # main.pdf still promotes to terminal DONE and blocks.
    stub = _GateStub(
        project_root=_with_preflight_pdf(tmp_path),
        memory_root=tmp_path / "mem",
        full_emnlp_gate=False,
        certified=False,
    )
    result = stub.block()
    assert result is not None
    assert result["lifecycle_state"] == "done"


def test_lifecycle_block_is_deduped_across_repeated_ticks(tmp_path: Path) -> None:
    # Log hygiene: a project sitting in the same blocked state must emit
    # the held-item status + event line only ONCE, not on every tick — this
    # is what used to flood events.jsonl with tens of thousands
    # of identical lines. Dispatch behavior is unchanged: every call still
    # returns the block dict so the supervisor keeps holding the item.
    stub = _GateStub(
        project_root=_with_preflight_pdf(tmp_path),
        memory_root=tmp_path / "mem",
        full_emnlp_gate=False,
        certified=False,
    )
    results = [stub.block() for _ in range(5)]
    assert all(r is not None and r["status"] == "lifecycle_block" for r in results)

    gate_lines = [t for t in stub.emitted if "lifecycle gate" in t]
    assert len(gate_lines) == 1

    block_events = [
        e for e in stub.events if e.get("type") == "life.lifecycle.block"
    ]
    assert len(block_events) == 1


def test_planner_waiting_records_external_dependency_status(tmp_path: Path) -> None:
    from argus_skill.core.models import RunnerResult
    from argus_skill.life.memory import LifeMemory
    from argus_skill.planner import PlannerConfig

    class _Budget:
        def remaining_today(self, _journal) -> float:
            return 100.0

    class _PlannerRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            payload = {
                "project_done": False,
                "reason": "provider image route is blocked",
                "restart_daemon": False,
                "restart_reason": "",
                "waiting": True,
                "waiting_reason": (
                    "paper/figures/IMAGE2_OPERATOR_ACTION_REQUIRED.md documents "
                    "the image generation unknown_model external capability "
                    "blocker; all local high-impact work is exhausted"
                ),
                "new_tasks": [],
            }
            return RunnerResult(
                exit_code=0,
                agent_messages=[json.dumps(payload)],
                stdout_lines=[],
                stderr_lines=[],
                thread_id=None,
                fatal_error=None,
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
            )

    mem = LifeMemory.open(tmp_path)
    mem.init()
    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.memory = mem
    sup.config = SimpleNamespace(
        continuous_objective="finish draft gate",
        budget=_Budget(),
        full_emnlp_gate=False,
    )
    sup.planner_runner = _PlannerRunner()
    sup.skill_store = None
    sup.runner = SimpleNamespace(stream_to=None)
    sup.sink = None
    sup.reviewer_model = "gpt-5.5"
    sup._planning_cycles = 0
    sup._consecutive_idle_planner_cycles = 0
    sup._suggested_sleep_s = 0.0
    emitted: list[object] = []
    statuses: list[str] = []
    sup._emit = emitted.append
    sup._emit_status = statuses.append
    sup._render_journal_for_planner = lambda: ""
    sup._planner_project_context = lambda: ""
    sup._planner_config = lambda: PlannerConfig(
        working_dir=str(tmp_path),
        skip_git_repo_check=True,
        full_auto=True,
        dangerous_yolo=False,
    )

    from argus_skill.skills.vertical_select import persist_vertical

    # The Manager decides + persists the vertical before planning; seed research
    # so _resolve_vertical_once trusts it (no runner call) and the planner runs.
    persist_vertical(tmp_path, "research")

    assert LifeSupervisor._plan_next_work(sup) == "awaiting_external"

    assert any(s.startswith("awaiting external dependency:") for s in statuses)
    waiting_events = [
        e for e in emitted
        if isinstance(e, dict) and e.get("type") == "life.planner.waiting"
    ]
    assert len(waiting_events) == 1
    assert "IMAGE2_OPERATOR_ACTION_REQUIRED.md" in waiting_events[0]["reason"]
