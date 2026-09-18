from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.release_tools.pr_gate import owned_process
from argus.release_tools.pr_gate.llm import LLMError, _run_bounded
from argus.release_tools.pr_gate.owned_process import run_owned
from argus.release_tools.pr_gate.regression import evidence as local_evidence
from argus.release_tools.pr_gate.regression import probe
from argus.release_tools.pr_gate.regression import runner as local_runner

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux owned-process/subreaper contract")


def alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


def cleanup(pid: int) -> None:
    if alive(pid):
        os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("parent_exits", [False, True])
def test_llm_output_pipe_cannot_outlive_its_deadline(tmp_path, parent_exits):
    pid_file = tmp_path / "child.pid"
    program = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"Path({str(pid_file)!r}).write_text(str(p.pid)); "
        + ("print('finished',flush=True)" if parent_exits else "time.sleep(30)")
    )
    started = time.monotonic()
    try:
        if parent_exits:
            code, stdout, _ = _run_bounded(
                [sys.executable, "-c", program], cwd=tmp_path, environment={},
                timeout_seconds=1, output_limit=4096,
            )
            assert code == 0 and b"finished" in stdout
        else:
            with pytest.raises(LLMError) as failure:
                _run_bounded(
                    [sys.executable, "-c", program], cwd=tmp_path, environment={},
                    timeout_seconds=1, output_limit=4096,
                )
            assert failure.value.code == "timeout"
        assert time.monotonic() - started < 4
        assert not alive(int(pid_file.read_text()))
    finally:
        if pid_file.exists():
            cleanup(int(pid_file.read_text()))


def test_normal_exit_reaps_an_immediately_detached_child(tmp_path):
    output = tmp_path / "output.log"
    program = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True); "
        "print(p.pid,flush=True)"
    )
    result = run_owned(
        [sys.executable, "-c", program], cwd=tmp_path, environment={},
        timeout=2, stdout_path=output, output_limit=4096,
    )
    pid = int(output.read_text())
    try:
        assert result.reason is None and result.cleanup_complete
        assert not alive(pid)
    finally:
        cleanup(pid)


def test_native_cancellation_reaps_the_real_probe_helper_and_its_test(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    test_pid = tmp_path / "test.pid"
    helper_pid = tmp_path / "helper.pid"
    command = shlex.join([
        sys.executable, "-c",
        f"import os,time; from pathlib import Path; Path({str(test_pid)!r}).write_text(str(os.getpid())); time.sleep(30)",
    ])
    helper_code = (
        "import os; from pathlib import Path; "
        "from argus.release_tools.pr_gate.regression.probe import run_one; "
        f"Path({str(helper_pid)!r}).write_text(str(os.getpid())); "
        f"run_one(root=Path({str(tmp_path)!r}),command={command!r},log=Path({str(tmp_path / 'probe.log')!r}))"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{helper_code!r}],start_new_session=True); "
        "time.sleep(30)"
    )
    environment = dict(os.environ, PYTHONPATH=str(repository), PR_GATE_WORK_ROOT=str(tmp_path))
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = run_owned(
            [sys.executable, "-c", parent_code], cwd=tmp_path, environment=environment,
            timeout=10, cancelled=test_pid.exists,
            stdout_path=tmp_path / "outer.log", output_limit=4096,
        )
        assert test_pid.exists(), (tmp_path / "outer.log").read_text()
        assert result.reason == "cancelled" and result.cleanup_complete
        assert not alive(int(helper_pid.read_text()))
        assert not alive(int(test_pid.read_text()))
        assert unrelated.poll() is None
    finally:
        for path in (test_pid, helper_pid):
            if path.exists():
                cleanup(int(path.read_text()))
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_probe_stops_noisy_output_before_loading_or_storing_it_all(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "WORK", tmp_path)
    probe.STOP.clear()
    path = tmp_path / "probe.log"
    command = shlex.join([
        sys.executable, "-c",
        f"import sys; sys.stdout.buffer.write(b'x'*{probe.OUTPUT_LIMIT_BYTES * 4})",
    ])
    result = probe.run_one(root=tmp_path, command=command, log=path)
    assert result["output_limit_exceeded"] is True
    assert result["cleanup_complete"] is True
    assert path.stat().st_size <= probe.OUTPUT_LIMIT_BYTES
    assert result["execution_error"] == "output_limit"


def test_signal_terminated_probe_records_incomplete_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "WORK", tmp_path)
    probe.STOP.clear()
    command = "exec " + shlex.join([
        sys.executable, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGKILL)",
    ])
    result = probe.run_one(root=tmp_path, command=command, log=tmp_path / "signal.log")
    assert result["exit_code"] == -signal.SIGKILL
    assert result["interrupted"] is True
    assert result["execution_error"] == "signal_terminated"
    assert result["cleanup_complete"] is True


def test_shell_signal_status_is_incomplete_but_assertion_failure_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "WORK", tmp_path)
    probe.STOP.clear()
    stopped = probe.run_one(root=tmp_path, command="exit 137", log=tmp_path / "shell-signal.log")
    assertion = probe.run_one(root=tmp_path, command="exit 1", log=tmp_path / "assertion.log")
    assert stopped["interrupted"] and stopped["execution_error"] == "signal_exit_status"
    assert not assertion["interrupted"] and assertion["execution_error"] is None


@pytest.mark.parametrize("later_cleanup", [None, True])
def test_windows_cleanup_failure_remains_visible_after_pipe_eof(tmp_path, monkeypatch, later_cleanup):
    from types import SimpleNamespace

    from argus.core import windows_job

    class WindowsOs:
        name = "nt"

        def __getattr__(self, key):
            return getattr(os, key)

    monkeypatch.setattr(owned_process, "os", WindowsOs())
    monkeypatch.setattr(owned_process, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(
        windows_job, "spawn_owned_process",
        lambda command, **kwargs: subprocess.Popen(command, start_new_session=True, **kwargs),
    )
    calls = []

    def fail_first(process):
        calls.append(process.pid)
        return False if len(calls) == 1 else later_cleanup

    monkeypatch.setattr(windows_job, "terminate_owned_process", fail_first)
    result = run_owned(
        [sys.executable, "-c", "print('done')"], cwd=tmp_path, environment={},
        timeout=2, stdout_path=tmp_path / "windows.log", output_limit=4096,
    )
    assert calls and result.returncode == 0
    assert result.cleanup_complete is False


def test_llm_missing_executable_is_a_structured_error(tmp_path):
    with pytest.raises(LLMError) as failure:
        _run_bounded(
            [str(tmp_path / "missing-cli")], cwd=tmp_path, environment={},
            timeout_seconds=1, output_limit=100,
        )
    assert failure.value.code == "cli_unavailable"


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf")])
def test_llm_invalid_deadline_is_not_an_unbounded_call(tmp_path, timeout):
    with pytest.raises(LLMError) as failure:
        _run_bounded(
            [sys.executable, "-c", "pass"], cwd=tmp_path, environment={},
            timeout_seconds=timeout, output_limit=100,
        )
    assert failure.value.code == "invalid_configuration"


def test_copied_probe_runtime_works_without_importing_the_repository(tmp_path):
    import json

    local_runner.stage_assets(tmp_path, local_evidence.SKILL_PATH)
    work = tmp_path / "work"
    (work / "base").mkdir(parents=True)
    (work / "candidate").mkdir()
    (work / "input.json").write_text(json.dumps({
        "base_sha": "a" * 40, "candidate_sha": "b" * 40,
    }))
    command = shlex.join([sys.executable, "-c", "print('probe-ok')"])
    result = subprocess.run(
        [sys.executable, str(tmp_path / "runner/probe.py"), "--id", "asset-smoke",
         "--base-command", command, "--candidate-command", command],
        cwd=work, env={
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "HOME": str(tmp_path), "PR_GATE_WORK_ROOT": str(work),
        },
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((work / "evidence/asset-smoke/receipt.json").read_text())
    assert all(
        receipt[label]["exit_code"] == 0 and receipt[label]["cleanup_complete"]
        for label in ("base", "candidate")
    )
