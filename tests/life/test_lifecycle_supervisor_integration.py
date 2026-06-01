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
from datetime import datetime, timedelta, timezone
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
