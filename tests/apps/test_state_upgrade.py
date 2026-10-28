"""Upgrade test (ROADMAP L3.6): project state as 0.1.x releases left it.

Each fixture writes files in the shape a tagged release wrote them (the release
is named beside it), runs ``argus upgrade-state`` and reads the result back
through the current runtime. State the current code writes is left unchanged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.apps import _inbox_protocol as inbox
from argus.apps import state_upgrade
from argus.apps.cli import main as cli_main
from argus.core import paths as core_paths
from argus.core.project import project_fingerprint
from argus.life.failure_experience import FailureExperience, FailureExperienceStore


def _sha1_name(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _old_fingerprint_project(home: Path, workdir: Path) -> Path:
    """A cwd-keyed project as 0.1.1-0.1.9 named it: ``sha1(label)[:12]``."""
    workdir.mkdir(parents=True, exist_ok=True)
    project = core_paths.session_states_root(home) / _sha1_name(str(workdir.resolve()))
    project.mkdir(parents=True)
    (project / "backlog.jsonl").write_text('{"id": "b1", "title": "old work"}\n', encoding="utf-8")
    (project / "daemon.status.json").write_text(
        json.dumps({"pid": 999999, "project_workdir": str(workdir)}), encoding="utf-8",
    )
    return project


def _old_inbox(project: Path) -> None:
    """The JSONL inbox of 0.1.1-0.1.6: one read message, two unread, one stage stream."""
    project.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"ts": 1757000000.0 + index, "text": text}) + "\n"
             for index, text in enumerate(("already read", "steer left", "steer right"))]
    (project / "inbox.jsonl").write_text("".join(lines), encoding="utf-8")
    (project / "inbox.offset").write_text(str(len(lines[0].encode("utf-8"))), encoding="utf-8")
    (project / "inbox.review.jsonl").write_text(
        json.dumps({"ts": 1757000100.0, "text": "for the reviewer"}) + "\n", encoding="utf-8",
    )


def _old_failure_experiences(project: Path) -> Path:
    """The append log of 0.1.1-0.1.6: rows without revision or state, then an annotation."""
    project.mkdir(parents=True, exist_ok=True)
    first = {
        "record_type": "experience", "id": "5f0c2a9e1b7d4c3a", "created_at": 1757000000.0,
        "mission_id": "m-1", "title": "baseline diverged", "objective": "train the baseline",
        "status": "failed", "factual_outcome": "loss went to NaN at step 400",
        "research_narrative": "", "passed_assumptions": [], "lessons": ["clip gradients"],
        "transfer_insights": [], "claim_boundaries": [], "retry_conditions": [],
        "artifact_refs": [], "concepts": ["optimization"], "causes": ["learning rate"],
        "related_experience_ids": [], "annotations": [],
    }
    second = {**first, "id": "8d1e6f0a2c4b9e7f", "title": "eval script crashed",
              "lessons": ["pin the dataset version"]}
    annotation = {
        "record_type": "annotation", "experience_id": first["id"],
        "annotation": {"id": "a1", "created_at": 1757000500.0, "text": "reproduced with lr 3e-4",
                       "relation": "confirms", "evidence_refs": ["runs/12/log.txt"]},
    }
    path = project / state_upgrade.FAILURE_EXPERIENCES
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in (first, second, annotation)),
        encoding="utf-8",
    )
    return path


def _current_project(home: Path) -> Path:
    """A project the current code wrote: escaped name, SQLite inbox, snapshot experiences."""
    workdir = home.parent / "current-work"
    workdir.mkdir(parents=True, exist_ok=True)
    project = core_paths.session_state_root(project_fingerprint(workdir).fingerprint, root=home)
    project.mkdir(parents=True)
    inbox.enqueue_inbox_message(project, "current message", source="test")
    FailureExperienceStore(project / state_upgrade.FAILURE_EXPERIENCES).append(
        FailureExperience(
            id="exp:" + float(1757000000.0).hex() + ":x", created_at=1757000000.0, mission_id="m-2",
            title="current", objective="run", status="failed", factual_outcome="recorded by this release",
        ),
    )
    return project


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_a_sha1_named_project_is_renamed_to_its_escaped_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "papers" / "demo"
    old = _old_fingerprint_project(home, workdir)
    expected = core_paths.session_state_root(project_fingerprint(workdir).fingerprint, root=home)

    # The runtime looks only under the new name; before the upgrade it is empty.
    assert not expected.exists()

    steps = state_upgrade.upgrade_state(home)

    assert steps == [state_upgrade.UpgradeStep(old.name, "rename", True, expected.name)]
    assert not old.exists()
    assert (expected / "backlog.jsonl").read_text(encoding="utf-8") == '{"id": "b1", "title": "old work"}\n'
    assert state_upgrade.upgrade_state(home) == []


def test_an_unidentified_sha1_project_waits_for_its_workdir(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "moved-away"
    old = _old_fingerprint_project(home, workdir)
    (old / "daemon.status.json").unlink()

    [step] = state_upgrade.upgrade_state(home)
    assert (step.change, step.done) == ("rename", False) and "--workdir" in step.note
    assert old.is_dir()

    [step] = state_upgrade.upgrade_state(home, workdirs=[workdir])
    assert step.done and not old.exists()


def test_both_names_keep_the_new_directory_and_leave_the_old_one(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    old = _old_fingerprint_project(home, workdir)
    new = core_paths.session_state_root(project_fingerprint(workdir).fingerprint, root=home)
    new.mkdir()

    [step] = state_upgrade.upgrade_state(home)

    assert not step.done and "already exists" in step.note
    assert old.is_dir() and new.is_dir()


def test_a_jsonl_inbox_is_imported_with_its_read_position(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = core_paths.session_state_root("s-0a1b2c3d", root=home)
    _old_inbox(project)

    with pytest.raises(inbox.InboxMigrationRequired, match="argus upgrade-state"):
        inbox.enqueue_inbox_message(project, "new", source="test")

    assert state_upgrade.upgrade_state(home) == [state_upgrade.UpgradeStep(project.name, "inbox", True)]

    assert inbox.count_durable_inbox_messages(project) == 3
    claim = inbox.claim_inbox_message(project)
    assert claim is not None and claim.text == "steer left"
    inbox.enqueue_inbox_message(project, "new", source="test")
    assert inbox.count_durable_inbox_messages(project) == 4
    assert not (project / "inbox.jsonl").is_file()
    assert state_upgrade.upgrade_state(home) == []


def test_an_append_log_of_failure_experiences_becomes_a_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = core_paths.session_state_root("s-4e5f6a7b", root=home)
    path = _old_failure_experiences(project)
    store = FailureExperienceStore(path)

    with pytest.raises(ValueError, match="argus upgrade-state"):
        store.get("5f0c2a9e1b7d4c3a")

    [step] = state_upgrade.upgrade_state(home)
    assert (step.change, step.done, step.note) == ("failure experiences", True, "2 kept")

    first = store.get("5f0c2a9e1b7d4c3a")
    assert first is not None and first.lessons == ["clip gradients"]
    assert [annotation.text for annotation in first.annotations] == ["reproduced with lr 3e-4"]
    assert store.get("8d1e6f0a2c4b9e7f") is not None
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["record_type"] == "snapshot"
    assert state_upgrade.upgrade_state(home) == []


def test_current_state_is_left_byte_for_byte(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = _current_project(home)
    (project / "failure_experiences.jsonl.lock").unlink(missing_ok=True)
    before = _snapshot(home)

    assert state_upgrade.upgrade_state(home) == []

    after = _snapshot(home)
    after.pop(str((project / "failure_experiences.jsonl.lock").relative_to(home)), None)
    assert after == before


def test_a_project_whose_daemon_runs_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    project = core_paths.session_state_root("s-9c8d7e6f", root=home)
    _old_inbox(project)
    monkeypatch.setattr(state_upgrade, "_is_live", lambda directory: True)

    assert state_upgrade.upgrade_state(home) == [
        state_upgrade.UpgradeStep(project.name, "skipped", False, "its daemon is running"),
    ]
    assert (project / "inbox.jsonl").is_file()


def test_the_command_upgrades_a_whole_0_1_home(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "papers" / "demo"
    old = _old_fingerprint_project(home, workdir)
    _old_inbox(old)
    _old_failure_experiences(old)
    _current_project(home)

    assert cli_main(["--life-dir", str(home), "upgrade-state", "--dry-run"]) == 0
    assert old.is_dir()
    capsys.readouterr()

    assert cli_main(["--life-dir", str(home), "upgrade-state"]) == 0
    out = capsys.readouterr().out
    new = core_paths.session_state_root(project_fingerprint(workdir).fingerprint, root=home)
    assert out.splitlines() == [
        f"ok {old.name}: rename: {new.name}",
        f"ok {new.name}: inbox",
        f"ok {new.name}: failure experiences: 2 kept",
    ]
    assert inbox.count_durable_inbox_messages(new) == 3

    assert cli_main(["--life-dir", str(home), "upgrade-state"]) == 0
    assert "is current" in capsys.readouterr().out
