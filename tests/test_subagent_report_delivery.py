"""Delivery contract for subagent handoff reports, and codex binary discovery.

Two silent-degradation paths are pinned here:

* ``_queue_to_inbox`` used to swallow a delivery failure and drop the report
  into ``<task_id>_ALERT.md``, a path nothing in production read — the engineer
  simply never learned the run had ended. It also derived the destination inbox
  from the *worker's* cwd, so a ``setsid``-detached worker could deliver into a
  different project's inbox and raise nothing at all.
* ``_find_codex`` used to prove codex was absent and then return the bare name
  ``"codex"``, converting a diagnosable "not installed / PATH not exported /
  installed elsewhere" into a context-free ``FileNotFoundError`` from
  ``subprocess`` much later.
"""
from __future__ import annotations

import json
import logging
import types
from pathlib import Path

import pytest

from argus_skill.core.paths import session_state_root
from argus_skill.core.project import project_fingerprint
from argus_skill.tools import subagent as _sub
from argus_skill.tools.subagent import _cli, _core, _reporting, _text
from argus_skill.tools.subagent._registry import REGISTRY_DIR, _read_task, _write_task

_REPORTING_LOGGER = "argus_skill.tools.subagent._reporting"


def _fail_delivery(monkeypatch: pytest.MonkeyPatch, message: str = "inbox offline") -> None:
    """Make the real inbox writer fail the way a broken life dir would."""
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(message)

    monkeypatch.setattr("argus_skill.apps._inbox.queue_inbox_message", _boom)


def _capture_delivery(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str, str]]:
    """Record (life_dir, text, source) for every queued inbox message."""
    calls: list[tuple[Path, str, str]] = []

    def _record(life_dir: Path | str, text: str, *, source: str, stage: str = "") -> None:
        calls.append((Path(life_dir), text, source))

    monkeypatch.setattr("argus_skill.apps._inbox.queue_inbox_message", _record)
    return calls


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# _queue_to_inbox: a failed delivery is a raised delivery
# ---------------------------------------------------------------------------

def test_queue_to_inbox_raises_and_keeps_the_forensic_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fail_delivery(monkeypatch)
    report = "## Subagent Report: t1 [COMPLETED]"

    with pytest.raises(_reporting.InboxDeliveryError) as excinfo:
        _reporting._queue_to_inbox(report, task_id="t1", life_dir=tmp_path / "life")

    message = str(excinfo.value)
    assert "t1" in message
    assert "inbox offline" in message
    # The last-resort forensics survive, but they are no longer the whole story.
    alert = REGISTRY_DIR / "t1_ALERT.md"
    assert alert.read_text(encoding="utf-8") == report + "\n"
    assert str(alert) in message


def test_queue_to_inbox_uses_the_life_dir_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _capture_delivery(monkeypatch)

    def _must_not_infer(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("identity must not be inferred when life_dir is given")

    monkeypatch.setattr("argus_skill.core.project.project_fingerprint", _must_not_infer)
    explicit = tmp_path / "life" / "abc123def456"

    _reporting._queue_to_inbox("report body", task_id="t1", life_dir=explicit)

    assert calls == [(explicit, "report body", "subagent")]


def test_queue_to_inbox_without_a_life_dir_says_it_is_guessing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _capture_delivery(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=_REPORTING_LOGGER):
        _reporting._queue_to_inbox("report body", task_id="t1")

    assert len(calls) == 1
    assert "inferring the engineer inbox" in caplog.text


# ---------------------------------------------------------------------------
# _alert_engineer: identity comes from the run, failures land on the record
# ---------------------------------------------------------------------------

def test_alert_engineer_delivers_to_the_runs_cwd_not_the_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The detached worker's own cwd must not decide which inbox is written."""
    worker_cwd = tmp_path / "detached-worker"
    run_cwd = tmp_path / "campaign-project"
    worker_cwd.mkdir()
    run_cwd.mkdir()
    monkeypatch.chdir(worker_cwd)
    calls = _capture_delivery(monkeypatch)

    task_data = {
        "task_id": "t1",
        "mode": "direct",
        "cwd": str(run_cwd),
        "elapsed_seconds": 1.0,
    }
    _reporting._alert_engineer("t1", "COMPLETED", task_data)

    delivered = calls[0][0]
    assert delivered == session_state_root(project_fingerprint(run_cwd).fingerprint)
    assert delivered != session_state_root(project_fingerprint(worker_cwd).fingerprint)
    assert task_data["report_delivery"] == "delivered"


def test_alert_engineer_recovers_the_cwd_from_the_persisted_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In-memory task data without a cwd still resolves via the task record."""
    run_cwd = tmp_path / "campaign-project"
    run_cwd.mkdir()
    tid = "train-cwdless"
    _write_task(tid, {"task_id": tid, "state": "running", "cwd": str(run_cwd)})
    calls = _capture_delivery(monkeypatch)

    _reporting._alert_engineer(tid, "COMPLETED", {"task_id": tid, "mode": "direct"})

    assert calls[0][0] == session_state_root(project_fingerprint(run_cwd).fingerprint)


def test_alert_engineer_records_a_failed_delivery_on_the_task_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "train-lost"
    _write_task(
        tid,
        {"task_id": tid, "state": "running", "run_id": "r1", "cwd": str(tmp_path)},
    )
    _fail_delivery(monkeypatch)
    task_data = {
        "task_id": tid,
        "mode": "direct",
        "run_id": "r1",
        "cwd": str(tmp_path),
        "elapsed_seconds": 2.0,
    }

    # A lost report must not kill the worker mid-teardown...
    report = _reporting._alert_engineer(tid, "CRASHED", task_data)

    assert report.startswith(f"## Subagent Report: {tid}")
    # ...but it must be visible afterwards, in memory and on disk.
    assert task_data["report_delivery"] == "failed"
    persisted = _read_task(tid)
    assert persisted is not None
    assert persisted["report_delivery"] == "failed"
    assert persisted["report_delivery_event"] == "CRASHED"
    assert "InboxDeliveryError" in persisted["report_delivery_error"]
    assert "inbox offline" in persisted["report_delivery_error"]
    assert (REGISTRY_DIR / f"{tid}_ALERT.md").exists()


def test_alert_engineer_does_not_persist_onto_a_superseded_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A newer run owns the record now; the stale worker must not stamp it."""
    tid = "train-superseded"
    _write_task(
        tid,
        {"task_id": tid, "state": "running", "run_id": "r2", "cwd": str(tmp_path)},
    )
    _fail_delivery(monkeypatch)

    _reporting._alert_engineer(
        tid,
        "CRASHED",
        {"task_id": tid, "mode": "direct", "run_id": "r1", "cwd": str(tmp_path)},
    )

    persisted = _read_task(tid)
    assert persisted is not None
    assert "report_delivery" not in persisted


# ---------------------------------------------------------------------------
# subagent status is the consumer for an orphaned _ALERT.md
# ---------------------------------------------------------------------------

def test_status_surfaces_an_orphaned_alert_file(capsys: pytest.CaptureFixture[str]) -> None:
    tid = "train-polled"
    _write_task(tid, {"task_id": tid, "state": "done", "mode": "direct"})
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    alert = REGISTRY_DIR / "train-lost_ALERT.md"
    alert.write_text("## Subagent Report: train-lost [CRASHED]\n", encoding="utf-8")

    rc = _cli.cmd_status(types.SimpleNamespace(task_id=tid))
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert [r["task_id"] for r in payload["undelivered_reports"]] == ["train-lost"]
    assert payload["undelivered_reports"][0]["path"] == str(alert)
    assert "never reached your inbox" in payload["UNDELIVERED_REPORTS"]


def test_status_stays_quiet_when_every_report_landed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tid = "train-clean"
    _write_task(tid, {"task_id": tid, "state": "done", "mode": "direct"})

    assert _cli.cmd_status(types.SimpleNamespace(task_id=tid)) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "undelivered_reports" not in payload
    assert "UNDELIVERED_REPORTS" not in payload


# ---------------------------------------------------------------------------
# _find_codex: report absence instead of guessing
# ---------------------------------------------------------------------------

def test_find_codex_raises_with_the_probe_trail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    missing = (str(tmp_path / "usr/local/bin/codex"), str(tmp_path / "usr/bin/codex"))
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    monkeypatch.setattr(_text, "_CODEX_SYSTEM_PATHS", missing)

    with pytest.raises(FileNotFoundError) as excinfo:
        _text._find_codex()

    message = str(excinfo.value)
    assert "PATH lookup" in message
    for candidate in missing:
        assert candidate in message
    assert f"PATH={empty_bin}" in message
    assert "ARGUS_SKILL_RUNNER_BIN" in message


def test_find_codex_reports_an_unusable_runner_bin_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """"Operator set the knob to the wrong path" is its own diagnosis."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    override = tmp_path / "nowhere" / "codex"
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BIN", str(override))
    monkeypatch.setattr(_text, "_CODEX_SYSTEM_PATHS", (str(tmp_path / "usr/bin/codex"),))

    with pytest.raises(FileNotFoundError) as excinfo:
        _text._find_codex()

    assert str(override) in str(excinfo.value)
    assert "not an executable file" in str(excinfo.value)


def test_find_codex_returns_the_binary_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex = _executable(tmp_path / "bin" / "codex")
    monkeypatch.setenv("PATH", str(codex.parent))

    assert _text._find_codex() == str(codex)


def test_find_codex_falls_back_to_a_system_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    present = _executable(tmp_path / "usr" / "bin" / "codex")
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setattr(
        _text,
        "_CODEX_SYSTEM_PATHS",
        (str(tmp_path / "usr/local/bin/codex"), str(present)),
    )

    assert _text._find_codex() == str(present)


def test_find_codex_honors_the_runner_bin_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    override = _executable(tmp_path / "opt" / "custom-codex")
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BIN", str(override))

    assert _text._find_codex() == str(override)


def test_find_codex_stays_reachable_through_the_reexport_shims() -> None:
    assert _core._find_codex is _text._find_codex
    assert _sub._find_codex is _text._find_codex


def test_a_failed_discussion_notice_is_not_settled_as_a_crashed_run(
    monkeypatch,
    tmp_path,
) -> None:
    """An undeliverable discussion reply must stay a notification fault.

    ``_run_discussion`` is called from inside ``_run_supervised``'s broad
    handler, which settles anything escaping it as ``state: "error"`` and sends
    the engineer a CRASHED report. Letting ``InboxDeliveryError`` propagate from
    the discussion notice would therefore report a GPU run as crashed because a
    message could not be queued — the run is already finished and unaffected.
    """
    import os

    from argus_skill.tools.subagent import _run_discussion
    from argus_skill.tools.subagent._discussion_log import (
        _append_discussion,
        _render_discussion,
    )
    from argus_skill.tools.subagent._registry import _read_task, _write_task
    from argus_skill.tools.subagent._reporting import InboxDeliveryError

    monkeypatch.chdir(tmp_path)
    tid = "train-undeliverable"
    _write_task(tid, {
        "state": "early_stopped", "task_id": tid, "mode": "supervised",
        "pid": 0, "worker_pid": os.getpid(),
    })
    _append_discussion(tid, "engineer", "here is my fix: num_generations 2 -> 8")

    def fake_discuss(task_id, task_data, model, cwd, thread_id=None):
        return (True, "Agreed, that restores group contrast.", thread_id, (0, 0, 0, 0))

    delivered: list[str] = []

    def refuse_delivery(*_args, **_kwargs):
        delivered.append("attempted")
        raise InboxDeliveryError("inbox unwritable")

    monkeypatch.setattr(
        "argus_skill.tools.subagent._discuss_run._supervisor_discuss_with_usage",
        fake_discuss,
    )
    monkeypatch.setattr(
        "argus_skill.tools.subagent._discuss_run._queue_to_inbox",
        refuse_delivery,
    )
    monkeypatch.setattr(
        "argus_skill.tools.subagent._discuss_run.DISCUSSION_POLL_INTERVAL", 0
    )
    _write_task(tid, {"state": "discussing", "task_id": tid})

    # Must not raise: that is the whole point.
    _run_discussion(tid, {"concern": "x", "command": "python t.py"}, "gpt-5.5", str(tmp_path))

    assert delivered, "the delivery should have been attempted"
    # The reply is still on the durable thread even though the notice failed.
    assert "restores group contrast" in _render_discussion(tid)
    assert str((_read_task(tid) or {}).get("state")) != "error"
