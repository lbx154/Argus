"""Tests for the Manager's one persistent, flock-serialized codex session.

Covers the four guarantees of ``_ManagerSession``:

  * continuation + persistence — incoming turns resume the stored thread_id and
    the new thread_id is written back to ``<root>/.manager_session.json``;
  * cross-process serialization — flock keeps concurrent turns from interleaving;
  * fail-open — any session-mode error degrades to a plain no-session call,
    never raising and never blocking the Manager's decision;
  * Manager wiring — the three Manager LLM calls (is_conversational / divide /
    approve_skill) actually flow through the shared session.
"""
from __future__ import annotations

import json
import threading
import time

from argus_skill.manager import Manager
from argus_skill.manager._core import _ManagerSession, _SESSION_FILE


class _Result:
    """Minimal RunnerResult shape: carries a thread_id and a classifier message."""

    def __init__(self, thread_id: str, msg: str = "") -> None:
        self.thread_id = thread_id
        self.last_agent_message = msg
        self.exit_code = 0


class _RecordingRunner:
    """Records every resume_thread_id it was handed; mints an increasing tid."""

    def __init__(self, reply: str = "") -> None:
        self.resumes: list[str | None] = []
        self.reply = reply
        self._n = 0

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.resumes.append(resume_thread_id)
        self._n += 1
        return _Result(thread_id=f"t{self._n}", msg=self.reply)


# ---------------------------------------------------------------------------
# 4. continuation + persistence
# ---------------------------------------------------------------------------
def test_session_resumes_and_persists_thread_id(tmp_path):
    fake = _RecordingRunner()
    sess = _ManagerSession(fake, tmp_path)

    r1 = sess.run_exec(prompt="a", options=None, run_label="x")
    # First turn: nothing stored yet → no resume; tid t1 written back.
    assert fake.resumes[0] is None
    assert r1.thread_id == "t1"
    stored = json.loads((tmp_path / _SESSION_FILE).read_text())
    assert stored["thread_id"] == "t1"
    assert sess.thread_id == "t1"

    r2 = sess.run_exec(prompt="b", options=None, run_label="x")
    # Second turn: resumes t1 (continuation) and persists t2.
    assert fake.resumes[1] == "t1"
    assert r2.thread_id == "t2"
    assert json.loads((tmp_path / _SESSION_FILE).read_text())["thread_id"] == "t2"
    assert sess.thread_id == "t2"


def test_session_ignores_incoming_resume_thread_id(tmp_path):
    # An explicit resume_thread_id from a caller is IGNORED — the persistent
    # session always wins (first turn → None, not the caller's value).
    fake = _RecordingRunner()
    sess = _ManagerSession(fake, tmp_path)
    sess.run_exec(prompt="a", options=None, run_label="x", resume_thread_id="CALLER")
    assert fake.resumes[0] is None


# ---------------------------------------------------------------------------
# 5. flock serialization — concurrent turns must not interleave
# ---------------------------------------------------------------------------
class _SlowRunner:
    """Asserts non-overlap: increments a shared counter on entry, sleeps, and
    fails the test if a second call entered the critical section meanwhile."""

    def __init__(self, hold: float) -> None:
        self.hold = hold
        self.inside = 0
        self.max_concurrent = 0
        self._n = 0
        self._lock = threading.Lock()

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        with self._lock:
            self.inside += 1
            self.max_concurrent = max(self.max_concurrent, self.inside)
        time.sleep(self.hold)
        with self._lock:
            self.inside -= 1
            self._n += 1
            n = self._n
        return _Result(thread_id=f"t{n}")


def test_two_sessions_same_root_do_not_interleave(tmp_path):
    # Two independent _ManagerSession objects over the SAME project_root model
    # the REPL front-end and the daemon: flock on .manager_session.lock must
    # serialize them so a turn never overlaps another turn.
    runner = _SlowRunner(hold=0.25)
    s_repl = _ManagerSession(runner, tmp_path)
    s_daemon = _ManagerSession(runner, tmp_path)

    t = threading.Thread(
        target=lambda: s_repl.run_exec(prompt="a", options=None, run_label="x")
    )
    t.start()
    time.sleep(0.05)  # let the first turn enter and hold the lock
    s_daemon.run_exec(prompt="b", options=None, run_label="x")
    t.join()

    # If the lock worked, the runner never saw two callers inside at once.
    assert runner.max_concurrent == 1


def test_lock_is_held_during_a_turn(tmp_path):
    # Directly observe that the lock file is non-blockingly un-acquirable while a
    # turn is in flight (proves the flock is actually held cross-process-style).
    import fcntl

    barrier = threading.Event()
    released = threading.Event()

    class _Gate:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            barrier.set()
            released.wait(timeout=2.0)
            return _Result(thread_id="t1")

    sess = _ManagerSession(_Gate(), tmp_path)
    t = threading.Thread(
        target=lambda: sess.run_exec(prompt="a", options=None, run_label="x")
    )
    t.start()
    assert barrier.wait(timeout=2.0)  # turn is now inside, holding the lock

    # A second, independent handle on the same lock file cannot get LOCK_EX|NB.
    blocked = False
    with open(tmp_path / ".manager_session.lock", "a+b") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except BlockingIOError:
            blocked = True
    assert blocked

    released.set()
    t.join()


# ---------------------------------------------------------------------------
# 6. fail-open — errors degrade to a plain no-session call, never raise/block
# ---------------------------------------------------------------------------
class _RaiseOnceRunner:
    """Raises on the FIRST (session-mode) call, succeeds on the retry."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_kwargs: dict = {}

    def run_exec(self, *, prompt, options, run_label, resume_thread_id="__none__"):
        self.calls += 1
        self.last_kwargs = {
            "resume_passed": resume_thread_id != "__none__",
            "resume_thread_id": None if resume_thread_id == "__none__" else resume_thread_id,
        }
        if self.calls == 1:
            raise RuntimeError("session-mode boom")
        return _Result(thread_id="t1")


def test_fail_open_session_error_degrades_to_plain_call(tmp_path):
    fake = _RaiseOnceRunner()
    sess = _ManagerSession(fake, tmp_path)
    # Must NOT raise: the session-mode call blew up, the fallback succeeded.
    res = sess.run_exec(prompt="a", options=None, run_label="x")
    assert res.thread_id == "t1"
    assert fake.calls == 2
    # The fallback was a no-session call: resume_thread_id was NOT passed.
    assert fake.last_kwargs["resume_passed"] is False


def test_fail_open_when_root_unwritable(tmp_path, monkeypatch):
    # Lock/IO error (here: a forced mkdir failure) must still degrade, not block.
    import argus_skill.manager._core as core

    fake = _RecordingRunner()
    sess = _ManagerSession(fake, tmp_path)

    real_mkdir = core.Path.mkdir

    def _boom(self, *a, **k):
        if self == sess.project_root:
            raise OSError("read-only")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(core.Path, "mkdir", _boom)
    res = sess.run_exec(prompt="a", options=None, run_label="x")
    # Degraded to a plain no-session call (resume_thread_id not passed → None).
    assert res.thread_id == "t1"
    assert fake.resumes == [None]


# ---------------------------------------------------------------------------
# 7. Manager wiring — all three LLM calls flow through the shared session
# ---------------------------------------------------------------------------
def test_manager_calls_flow_through_one_session(tmp_path):
    fake = _RecordingRunner(reply="research")
    mgr = Manager(project_root=tmp_path, runner=fake)

    # is_conversational → manager-converse turn (first → resume None).
    mgr.is_conversational("hello there")
    # divide → vertical-classify turn on the SAME session (resumes prior tid).
    mgr.divide("write a paper for EMNLP submission")
    # approve_skill → skill_review turn on the SAME session (resumes prior tid).
    mgr.approve_skill(content="# skill\nbody", task="t", op="create")

    # Three turns total, one continuous thread: first None then non-None resumes.
    assert len(fake.resumes) == 3
    assert fake.resumes[0] is None
    assert fake.resumes[1] is not None
    assert fake.resumes[2] is not None
    # And the persistent session file advanced to the latest minted tid.
    assert json.loads((tmp_path / _SESSION_FILE).read_text())["thread_id"] == "t3"


def test_manager_without_runner_has_no_session(tmp_path):
    # runner=None → no session object → old heuristic path (memory tests intact).
    mgr = Manager(project_root=tmp_path, runner=None)
    assert mgr._session is None
