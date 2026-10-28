"""A role's turn ends when its CLI and every process the CLI started have ended.

#365 made the bubblewrap sandbox wait for them. These run the other hosts'
path (no sandbox, which the test suite forces): the runner itself waits for
the jobs, and a stop of the turn stops them and names them. Windows Job
Objects are replaced by a stand-in here; tests/agent_cli/
test_windows_turn_ownership.py runs the real ones on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.core import sandbox, turn_jobs, windows_job

posix = pytest.mark.skipif(os.name == "nt", reason="POSIX process tracking")

DETACHED = {
    # The Engineer's long-run rule: a new session, re-parented to init once
    # the shell that started it exits.
    "setsid": "setsid nohup sh -c {job!r} > log 2>&1 < /dev/null &",
    # Stays in the CLI's process group.
    "nohup": "nohup sh -c {job!r} > log 2>&1 < /dev/null &",
}


def _fake_codex(path: Path, launch: str) -> Path:
    """A CLI that starts a job, says so and ends its turn while the job runs."""
    path.write_text(
        f"#!{sys.executable}\nimport json, subprocess, sys\nsys.stdin.read()\n"
        f"subprocess.run({launch!r}, shell=True)\n"
        "for event in ({'type': 'thread.started', 'thread_id': 't'},\n"
        "              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'started'}},\n"
        "              {'type': 'turn.completed', 'usage': {}}):\n"
        "    print(json.dumps(event), flush=True)\n"
    )
    path.chmod(0o755)
    return path


def _live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except OSError:
        return True


@posix
@pytest.mark.parametrize("detach", sorted(DETACHED))
def test_a_job_started_detached_finishes_before_the_turn_ends(tmp_path, detach):
    assert sandbox.sandbox_tool() is None  # the path without bubblewrap
    job = "sleep 3; echo done > flag"
    fake = _fake_codex(tmp_path / "codex", DETACHED[detach].format(job=job))

    result = AgentCliRunner(agent_bin=str(fake), backend="codex").run_exec(
        prompt="work", resume_thread_id=None,
        options=RunnerOptions(working_dir=str(tmp_path)),
    )

    assert result.turn_completed, result.fatal_error
    assert result.last_agent_message == "started"
    assert (tmp_path / "flag").read_text() == "done\n"
    assert any(
        line.startswith(sandbox.SANDBOX_WAIT_MARKER) and "sleep 3" in line
        for line in result.stderr_lines
    )
    assert result.orphan_process_group_id == 0


@posix
def test_a_turn_without_jobs_ends_with_its_cli(tmp_path):
    fake = _fake_codex(tmp_path / "codex", "true")
    started = time.monotonic()
    result = AgentCliRunner(agent_bin=str(fake), backend="codex").run_exec(
        prompt="work", resume_thread_id=None, options=RunnerOptions(working_dir=str(tmp_path)),
    )
    assert result.turn_completed
    assert time.monotonic() - started < 5
    assert not any(line.startswith(sandbox.SANDBOX_WAIT_MARKER) for line in result.stderr_lines)


@posix
def test_a_turn_stopped_while_its_job_runs_names_and_stops_the_job(tmp_path):
    job = "echo $$ > pid; sleep 30; echo done > flag"
    fake = _fake_codex(tmp_path / "codex", DETACHED["setsid"].format(job=job))

    def decide(snapshot):
        return "restart: the Manager steered" if snapshot.running_tool else "continue"

    result = AgentCliRunner(agent_bin=str(fake), backend="codex").run_exec(
        prompt="work", resume_thread_id=None,
        options=RunnerOptions(
            working_dir=str(tmp_path), watchdog_soft_idle_seconds=1, inactivity_callback=decide,
        ),
    )

    assert result.turn_failed
    assert "running tool:" in result.fatal_error and "sleep 30" in result.fatal_error
    assert not _live(int((tmp_path / "pid").read_text()))
    assert not (tmp_path / "flag").exists()


@posix
def test_an_operator_stop_stops_a_job_outside_the_cli_group(tmp_path):
    job = "echo $$ > pid; sleep 30"
    fake = _fake_codex(tmp_path / "codex", DETACHED["setsid"].format(job=job))
    stop = []

    def reason():
        return "operator stop" if stop else None

    def watch(stream, line):
        if line.startswith(sandbox.SANDBOX_WAIT_MARKER):
            stop.append(line)

    runner = AgentCliRunner(agent_bin=str(fake), backend="codex", event_callback=watch)
    result = runner.run_exec(
        prompt="work", resume_thread_id=None,
        options=RunnerOptions(working_dir=str(tmp_path), external_interrupt_reason_provider=reason),
    )

    assert "External interrupt: operator stop" in result.fatal_error
    assert not _live(int((tmp_path / "pid").read_text()))


@posix
def test_the_turn_tag_finds_a_job_that_left_the_group(tmp_path):
    env = dict(os.environ)
    token = turn_jobs.tag_turn(env)
    job = subprocess.Popen(["sleep", "30"], env=env, start_new_session=True)
    other = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert [p.pid for p in turn_jobs.running(token)] == [job.pid]
        assert "sleep 30" in turn_jobs.describe([job.pid])
    finally:
        for process in (job, other):
            process.kill()
            process.wait()


class _Job:
    """The turn's Job Object as the runner sees it: members until they end."""

    def __init__(self, member: subprocess.Popen, *, ends_after: float | None):
        self.member = member
        self.ends_at = None if ends_after is None else time.monotonic() + ends_after
        self.terminated = False

    def active_processes(self) -> int:
        if self.ends_at is not None and time.monotonic() >= self.ends_at and self.member.poll() is None:
            self.member.kill()
            self.member.wait()
        return 1 if self.member.poll() is None else 0

    def process_ids(self) -> list[int]:
        return [self.member.pid]

    def terminate(self) -> bool:
        self.terminated = True
        if self.member.poll() is None:
            self.member.kill()
            self.member.wait()
        return True


def _windows_turn(ends_after: float | None):
    cli = subprocess.Popen(
        [sys.executable, "-c", "print('{\"type\": \"turn.completed\", \"usage\": {}}')"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    member = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    job = _Job(member, ends_after=ends_after)
    cli._argus_windows_job = job
    return cli, job


def test_windows_turn_waits_until_its_job_has_no_active_process():
    cli, job = _windows_turn(ends_after=2)
    started = time.monotonic()
    state = AgentCliRunner(agent_bin=sys.executable, backend="codex")._stream_turn_output(
        process=cli, command=[sys.executable], options=RunnerOptions(),
        run_label="engineer-r1", thread_id=None,
    )
    assert time.monotonic() - started >= 2
    assert job.member.poll() is not None
    assert state.turn_completed and not state.watchdog_terminated
    assert state.running_tool.startswith(f"[{job.member.pid}]") and "time.sleep(30)" in state.running_tool
    assert state.orphan_process_group_id == 0


def test_windows_turn_stopped_while_its_job_runs_names_the_job():
    cli, job = _windows_turn(ends_after=None)

    def decide(snapshot):
        return "restart: the Manager steered" if snapshot.running_tool else "continue"

    try:
        state = AgentCliRunner(agent_bin=sys.executable, backend="codex")._stream_turn_output(
            process=cli, command=[sys.executable],
            options=RunnerOptions(watchdog_soft_idle_seconds=1, inactivity_callback=decide),
            run_label="engineer-r1", thread_id=None,
        )
    finally:
        job.terminate()
    assert state.watchdog_terminated and job.terminated
    assert "running tool:" in state.watchdog_reason and "time.sleep(30)" in state.watchdog_reason


def test_windows_job_lists_its_process_ids(monkeypatch):
    class Api:
        def QueryInformationJobObject(self, handle, kind, pointer, size, _):  # noqa: N802
            assert kind == 3  # JobObjectBasicProcessIdList
            listed = pointer._obj
            listed.NumberOfAssignedProcesses = 2
            listed.NumberOfProcessIdsInList = 2
            listed.ProcessIdList[0], listed.ProcessIdList[1] = 41, 42
            return 1

        def CloseHandle(self, handle):  # noqa: N802
            return 1

    job = windows_job._WindowsTurnJob.__new__(windows_job._WindowsTurnJob)
    job.api, job.handle = Api(), 1
    assert job.process_ids() == [41, 42]
    job.handle = None
    assert job.process_ids() == []


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows Job Objects")
def test_native_windows_turn_waits_for_a_detached_job(tmp_path):
    flag = tmp_path / "flag"
    child = f"import time; time.sleep(3); open({str(flag)!r}, 'w').write('done')"
    cli = (
        "import json, subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}], creationflags=0x00000008); "
        "print(json.dumps({'type': 'turn.completed', 'usage': {}}), flush=True)"
    )
    runner = AgentCliRunner(agent_bin=sys.executable, backend="codex")
    runner._build_command = lambda **kw: [sys.executable, "-c", cli]
    result = runner.run_exec(prompt="work", resume_thread_id=None, options=RunnerOptions(working_dir=str(tmp_path)))
    assert result.turn_completed, result.fatal_error
    assert flag.read_text() == "done"


