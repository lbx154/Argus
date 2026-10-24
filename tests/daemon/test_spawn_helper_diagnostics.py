"""The clean-launcher boundary must relay why a daemon refused to start.

Bug #40: the helper ran with ``quiet=True``, so every admission refusal in
``spawn_detached_process`` took its ``if not quiet`` branch to nowhere and
exited non-zero having written nothing. The caller found empty stderr and
raised the bare fallback, "clean daemon launcher exited with code 3". A
testbed re-run in a directory whose previous daemon was still alive therefore
started no executor at all, and the mission sat queued with no stated reason.

The guarantee is unchanged; the seam moved. The launcher now *returns* its exit
code rather than raising, because ``webapi.daemon_lifecycle`` branches on that
code — ``_retryable_windows_spawn_failure`` for the one transient Win32 retry,
and ``rc == 3`` for an admission refusal — and an exception routes past both.
So the refusal travels as data: the launcher records the helper's whole stderr
on ``config.last_spawn_error``, and ``_launcher_failure_message`` renders it for
the operator. These tests drive that pair the same way the caller does, so what
they pin is still the string an operator actually reads.
"""
from __future__ import annotations

import inspect
import subprocess
from types import SimpleNamespace

import argus.daemon._life_worker_admission as admission

# What ``_busy_message`` actually produces when a live daemon holds the lease:
# an owner line, indented context, then the ways out. Only the first line names
# the pid, so a last-line-only summary throws away the entire diagnosis.
BUSY = (
    "argus: workdir /home/u/proj is already leased by pid 3870690\n"
    "  session: s-7d03352c\n"
    "  project: /home/u/.argus-skill/projects/s-7d03352c\n"
    "  a workdir runs one daemon at a time. Either:\n"
    "    - watch the one already there:  argus --status   (or --follow)\n"
    "    - stop it:                      kill 3870690\n"
    "    - or start this objective in a different directory\n"
)


def _run_clean_launcher(
    monkeypatch, tmp_path, *, stderr: str, returncode: int
) -> str:
    """Drive the real launcher against a canned helper result; return the error.

    The two steps are exactly ``daemon_lifecycle``'s: spawn with ``quiet=True``,
    then summarize whatever landed on ``last_spawn_error``.
    """
    monkeypatch.setattr(
        admission.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=returncode, stderr=stderr, stdout=""
        ),
    )
    config = SimpleNamespace(
        life_dir=tmp_path,
        project_workdir=None,
        log_path=None,
        last_spawn_error="",
    )
    monkeypatch.setattr(admission, "_config_payload", lambda _c: {})

    rc = admission.spawn_detached_daemon_clean(config, quiet=True)

    assert rc == returncode, "the caller branches on this code; it must survive"
    return admission._launcher_failure_message(
        str(config.last_spawn_error or ""), rc
    )


def test_silence_still_reports_the_exit_code(monkeypatch, tmp_path) -> None:
    """A helper that dies mute must still say so, and name the code.

    The launcher now supplies that sentence itself rather than leaving stderr
    empty for the caller to paper over, so the operator gets the same fact from
    one layer earlier — and ``_launcher_failure_message`` passes it through
    untouched instead of reaching its own fallback.
    """
    message = _run_clean_launcher(monkeypatch, tmp_path, stderr="", returncode=3)

    assert message == (
        "daemon spawn helper exited with rc=3 without diagnostic output"
    )
    assert "3" in message


def test_a_pre_rename_launcher_prefix_still_anchors_the_message(monkeypatch, tmp_path) -> None:
    """A helper from a tree installed before the rename prefixes ``argus-skill:``."""
    message = _run_clean_launcher(
        monkeypatch,
        tmp_path,
        stderr="warning: unrelated preamble\n" + BUSY.replace("argus:", "argus-skill:", 1),
        returncode=3,
    )

    assert message.startswith("argus-skill: workdir")
    assert "unrelated preamble" not in message


def test_the_helper_result_is_captured_rather_than_inherited() -> None:
    """If the caller ever stopped capturing, unmuting the helper would spray
    the parent's own stderr instead of being relayed."""
    source = inspect.getsource(admission.spawn_detached_daemon_clean)

    assert "capture_output=True" in source


def test_subprocess_is_the_real_module() -> None:
    assert admission.subprocess is subprocess
