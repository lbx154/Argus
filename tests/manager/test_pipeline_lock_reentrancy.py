"""Delegation-chain re-entrancy contract of ``manager_pipeline_lock``.

Regression suite for the 2026-09-05 paper-daemon deadlock AND for the
follow-up adversarial review of the first fix.

The incident: the daemon main loop held the pipeline flock
(``daemon/_life_worker_run.py`` ``_rf_main_loop``) while waiting on a
ThreadPoolExecutor future, and the mission worker thread re-entered
``manager_pipeline_lock`` (``life/supervisor/_planning_cycle.py``
``_reconcile_reviewed_stage_empty_plan`` → ``manager/_stage_ops.py``
``decide_stage_transition``). portalocker's POSIX flock is exclusive across
open file descriptions even within one process, so the worker polled the lock
for hours while the main thread waited for its result — a two-party cycle.

The first fix keyed re-entrancy on a PROCESS-WIDE ``flock_held`` flag, which
misclassified EVERY thread of the process as re-entrant: an independent
telegram/feishu poller thread (``daemon/_life_worker_run.py``) committing via
``manager/front_door.py`` would slip past the flock and overlap the in-flight
pass it was designed to wait out.

The corrected semantics, pinned here, key re-entrancy on the DELEGATION
CHAIN (a ``ContextVar`` set of held lock paths, propagated to workers only
via ``contextvars.copy_context()``):

* a worker the flock holder explicitly delegated to (mirrors the daemon main
  loop's ``executor.submit(copy_context().run, ...)``) re-enters and completes;
* delegated workers still exclude EACH OTHER (thread gate);
* an INDEPENDENT thread (plain ``threading.Thread`` — poller / webapi shape)
  is NOT re-entrant: it waits for the flock and only enters after release;
* cross-process exclusion holds for the whole hold window, including while a
  delegated worker is inside the gate (the flock's lifetime is bound to the
  top-level holder, never to gate holders);
* sequential (non-nested) top-level acquisitions still take the real flock;
* same-thread nesting is re-entrant.
"""
from __future__ import annotations

import contextvars
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

import pytest

from argus.manager._session_ops import manager_pipeline_lock

pytestmark = pytest.mark.integration

_LOCK_FILE_NAME = ".manager_pipeline.lock"

# Non-blocking probe run in a REAL separate process: flock exclusion is
# per open file description, so only another process can tell us whether
# the on-disk lock is genuinely held right now.
_NB_PROBE = r"""
import sys
import portalocker

fh = open(sys.argv[1], "a+b")
try:
    portalocker.lock(fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
except (OSError, portalocker.exceptions.LockException):
    print("LOCKED")
else:
    portalocker.unlock(fh)
    print("ACQUIRED")
"""


def _probe_flock(lock_file: Path) -> str:
    """Return LOCKED/ACQUIRED as observed by a fresh subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", _NB_PROBE, str(lock_file)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return result.stdout.strip()


def test_delegated_worker_reentry_while_main_thread_holds_lock(tmp_path) -> None:
    """The incident shape, via the REAL delegation mechanism.

    Mirrors ``_rf_main_loop``: the holder copies its context inside the
    ``with`` and submits the worker through ``Context.run``, so the worker
    carries the holder's re-entry entitlement.

    Old (pre-fix) code: the future never completes (worker polls the flock
    forever) and ``result(timeout=...)`` raises — red. Fixed code: the worker
    is on the delegation chain, takes the in-process gate, and finishes.
    """
    completed = threading.Event()

    def mission_pass() -> str:
        # _planning_cycle.py -> _stage_ops.py: second acquisition of the
        # same pipeline lock, from a delegated pool thread, same process.
        with manager_pipeline_lock(tmp_path):
            completed.set()
        return "stage-decision"

    timed_out = False
    result: str | None = None
    with manager_pipeline_lock(tmp_path):  # _rf_main_loop holds the lock ...
        executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="argus-mission"
        )
        try:
            # ... delegates the pass WITH its context (the daemon main loop's
            # exact submit shape) ...
            ctx = contextvars.copy_context()
            future = executor.submit(ctx.run, mission_pass)
            try:
                result = future.result(timeout=8.0)  # ... and waits inside it
            except FuturesTimeoutError:
                timed_out = True
        finally:
            # wait=False so the red path unwinds instead of hanging pytest:
            # the stuck worker only unblocks once the outer flock below is
            # released.
            executor.shutdown(wait=False)
    executor.shutdown(wait=True)
    assert not timed_out, (
        "delegated worker deadlocked re-entering manager_pipeline_lock while "
        "the main thread held it (2026-09-05 paper-daemon incident)"
    )
    assert result == "stage-decision"
    assert completed.is_set()


def test_external_process_excluded_for_the_whole_hold_window(tmp_path) -> None:
    """The adversarial-review probe, fixed as a regression test.

    While the top-level holder is inside — INCLUDING while a delegated
    worker is inside the gate — an external process's LOCK_NB must fail.
    The flock's lifetime is bound to the top-level holder, so there is no
    window where a gate holder runs without on-disk protection.
    """
    lock_file = tmp_path / _LOCK_FILE_NAME
    worker_in_gate = threading.Event()
    worker_may_exit = threading.Event()

    def delegated() -> None:
        with manager_pipeline_lock(tmp_path):
            worker_in_gate.set()
            worker_may_exit.wait(timeout=20.0)

    with manager_pipeline_lock(tmp_path):
        assert _probe_flock(lock_file) == "LOCKED"
        ctx = contextvars.copy_context()
        worker = threading.Thread(target=ctx.run, args=(delegated,), daemon=True)
        worker.start()
        assert worker_in_gate.wait(timeout=8.0), "delegated worker never entered"
        # Delegated worker is inside the gate right now: still LOCKED.
        assert _probe_flock(lock_file) == "LOCKED", (
            "external process acquired the flock while a delegated worker "
            "was inside the pipeline critical section"
        )
        worker_may_exit.set()
        worker.join(timeout=8.0)
        # Workers all done, holder still inside: still LOCKED.
        assert _probe_flock(lock_file) == "LOCKED"
    assert _probe_flock(lock_file) == "ACQUIRED"
