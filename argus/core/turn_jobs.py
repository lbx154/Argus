"""The jobs a role's turn started, so the turn ends only when they have ended.

A turn ends when its agent CLI and every process the CLI started have exited.
Inside bubblewrap the sandbox itself waits for them (``core.sandbox``). Without
it (macOS ``sandbox-exec``, a Linux host without bwrap) the CLI's own exit used
to end the turn: the runner then killed what was left in the CLI's process
group, while a job started with ``setsid`` silently outlived the turn. On
Windows the turn's Job Object was terminated at the CLI's exit. A detached job
the Engineer had started was gone, or unaccounted for, when the next round
looked for its exit flag.

On POSIX a turn's jobs are the processes that carry the turn's tag in their
environment, which every descendant inherits however it detaches (``nohup``,
``setsid``, a double fork, re-parenting to init), together with anything left
in the CLI's process group. On Windows they are the members of the turn's Job
Object (``core.windows_job``). A process that clears its own environment, or
breaks away from the Job, has opted out of the turn.

While the runner waits for them it names them once, after ``TURN_JOBS_MARKER``,
as the bubblewrap sandbox does; a stop of the turn names what it stopped.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterable, MutableMapping

TURN_ENV = "ARGUS_TURN"
TURN_JOBS_MARKER = "[argus-sandbox] the agent CLI exited; its turn runs on with what it started:"
# Each job's command line is cut to this many characters when it is named.
_COMMAND_CHARS = 200


def tag_turn(env: MutableMapping[str, str]) -> str:
    """Tag ``env`` as one new turn's; every process started from it carries the tag."""
    token = uuid.uuid4().hex
    env[TURN_ENV] = token
    return token


def running(token: str | None, *, group: int = 0) -> list:
    """The live processes (``psutil.Process``) of the turn tagged ``token``,
    and of POSIX process group ``group``, other than this process."""
    import psutil

    if not token and group <= 0:
        return []
    me = os.getpid()
    uid = os.getuid() if hasattr(os, "getuid") else None
    found = []
    for process in psutil.process_iter():
        if process.pid == me:
            continue
        try:
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            if group > 0 and os.getpgid(process.pid) == group:
                found.append(process)
                continue
            if not token or (uid is not None and process.uids().real != uid):
                continue
            if process.environ().get(TURN_ENV) == token:
                found.append(process)
        except (psutil.Error, OSError):
            continue
    return found


def command_line(pid: int) -> str:
    import psutil

    try:
        return " ".join(psutil.Process(pid).cmdline())[:_COMMAND_CHARS]
    except (psutil.Error, OSError):
        return ""


def describe(pids: Iterable[int]) -> str:
    """``[pid] command; …`` for the jobs, the form the sandbox prints them in."""
    return " ".join(f"[{pid}] {command_line(pid)};" for pid in pids)


def stop(processes: list, *, grace: float = 2.0) -> None:
    """Terminate ``processes``, then kill those still running after ``grace``."""
    import psutil

    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(processes, timeout=grace)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=5.0)


__all__ = ["TURN_ENV", "TURN_JOBS_MARKER", "command_line", "describe", "running", "stop", "tag_turn"]
