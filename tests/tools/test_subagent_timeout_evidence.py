from __future__ import annotations

import os
import shlex
import signal
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from argus.core.daemon_lock import is_process_group_running
from argus.tools.subagent import _direct_run, _registry

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX timeout exit evidence")


@pytest.mark.parametrize("command, state, code", [
    ("printf complete", "done", 0),
    ("exit 7", "error", 7),
    ("sleep 30", "timeout", -signal.SIGTERM),
])
def test_real_direct_run_preserves_terminal_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    command: str, state: str, code: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    notifications = []
    monkeypatch.setattr(_direct_run, "_alert_engineer",
                        lambda *args: notifications.append(args[:2]))
    _direct_run._run_direct("fixture", command, "Disposable framework fixture", 1,
                            str(tmp_path))
    record = _registry._read_task("fixture")
    assert record["state"] == state, record
    assert record["exit_code"] == code
    path = _registry._exit_status_path("fixture", record["run_id"])
    assert path.read_text() == f"{code}\n"
    assert not is_process_group_running(record["pid"])
    assert len(notifications) == 1
    if state == "timeout":
        assert record["exit_status_source"] == "timeout_waitpid"
        assert record["timeout_wrapper_returncode"] == code
        assert record["timeout_settlement_scope"] == "owned_posix_process_group"
        assert Path(record["exit_status_path"]) == path.absolute()


def _terminated_process(monkeypatch: pytest.MonkeyPatch) -> Mock:
    proc = Mock(pid=43210)
    proc.poll.return_value = -signal.SIGTERM
    monkeypatch.setattr(_direct_run, "_owned_process_group", lambda _: proc.pid)
    monkeypatch.setattr(_direct_run, "is_process_group_running", lambda _: False)
    return proc


@pytest.mark.parametrize("existing", [b"0\n", b"7\n", b"-9\n"])
def test_existing_sidecar_wins_without_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bytes,
) -> None:
    monkeypatch.chdir(tmp_path)
    proc = _terminated_process(monkeypatch)
    path = _registry._exit_status_path("fixture", "one")
    path.parent.mkdir(parents=True)
    path.write_bytes(existing)
    before = path.stat()
    result = _direct_run._timeout_exit_evidence("fixture", "one", proc)
    assert result["exit_code"] == int(existing)
    assert result["exit_status_source"] == "durable_wrapper"
    assert path.read_bytes() == existing
    assert path.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("problem", ["live", "unreaped", "unowned", "natural"])
def test_missing_evidence_is_not_fabricated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    proc = _terminated_process(monkeypatch)
    path = _registry._exit_status_path("fixture", "one")
    path.parent.mkdir(parents=True)
    if problem == "live":
        monkeypatch.setattr(_direct_run, "is_process_group_running", lambda _: True)
    elif problem == "unowned":
        monkeypatch.setattr(_direct_run, "_owned_process_group", lambda _: 0)
    else:
        proc.poll.return_value = None if problem == "unreaped" else 0
    with pytest.raises(RuntimeError):
        _direct_run._timeout_exit_evidence("fixture", "one", proc)
    assert not path.exists()


@pytest.mark.parametrize("kind", ["malformed", "oversize", "symlink", "fifo"])
def test_invalid_sidecar_is_preserved_and_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    proc = _terminated_process(monkeypatch)
    path = _registry._exit_status_path("fixture", "one")
    path.parent.mkdir(parents=True)
    if kind == "symlink":
        target = tmp_path / "other"
        target.write_text("0\n")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"bad\n" if kind == "malformed" else b"1" * 65)
    with pytest.raises((OSError, RuntimeError)):
        _direct_run._timeout_exit_evidence("fixture", "one", proc)


def test_term_resistant_child_settles_before_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    child = tmp_path / "child.py"
    child.write_text(
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path('ready').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    with (tmp_path / "stdout").open("w") as stdout:
        proc = _registry._launch_durable_command(
            task_id="fixture", run_id="resistant",
            command=f"{shlex.quote(sys.executable)} {shlex.quote(str(child))}",
            cwd=str(tmp_path), stdout=stdout, stderr=stdout,
        )
        try:
            deadline = time.monotonic() + 5
            while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert (tmp_path / "ready").exists()
            _direct_run._terminate_proc(proc, grace=0.1)
            assert not is_process_group_running(proc.pid)
            result = _direct_run._timeout_exit_evidence("fixture", "resistant", proc)
            assert result["exit_code"] < 0
        finally:
            if is_process_group_running(proc.pid):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def test_publication_race_keeps_first_complete_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    proc = _terminated_process(monkeypatch)
    path = _registry._exit_status_path("fixture", "race")
    path.parent.mkdir(parents=True)

    def concurrent_publish(source, destination):
        Path(destination).write_text("7\n")
        raise FileExistsError(destination)

    monkeypatch.setattr(_direct_run.os, "link", concurrent_publish)
    result = _direct_run._timeout_exit_evidence("fixture", "race", proc)
    assert result["exit_code"] == 7
    assert result["exit_status_source"] == "durable_wrapper"
    assert not list(path.parent.glob(".timeout-exit-*"))


def test_publication_failure_is_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    proc = _terminated_process(monkeypatch)
    path = _registry._exit_status_path("fixture", "failure")
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(_direct_run.os, "link", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(PermissionError, match="denied"):
        _direct_run._timeout_exit_evidence("fixture", "failure", proc)
    assert not path.exists()
    assert not list(path.parent.glob(".timeout-exit-*"))


def test_reused_leader_is_not_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = Mock(pid=43210, _argus_durable_process_group=43210)
    proc.poll.return_value = -signal.SIGTERM
    monkeypatch.setattr(_direct_run.os, "kill", lambda *_: None)
    assert _direct_run._owned_process_group(proc) == 0
