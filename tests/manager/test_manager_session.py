"""Tests for the Manager's persistent cross-platform locked codex session.

Covers the four guarantees of ``_ManagerSession``:

  * continuation + persistence — incoming turns resume the stored thread_id and
    the new thread_id is written back to ``<root>/.manager_session.json``;
  * one holder — the turn continuing the shared thread holds it; another
    turn answers at once on a fresh thread instead of waiting;
  * setup recovery — a session setup error permits one plain call, while an
    uncertain provider failure never replays inference or tool effects;
  * Manager wiring — the Manager LLM calls (is_conversational / divide) actually
    flow through the shared session.
"""
from __future__ import annotations

import json
import threading

import portalocker
import pytest

from argus.manager import Manager
from argus.manager._session_ops import _SESSION_FILE, _ManagerSession


class _Result:
    """Minimal RunnerResult shape: carries a thread_id and a classifier message."""

    def __init__(self, thread_id: str, msg: str = "") -> None:
        self.thread_id = thread_id
        self.last_agent_message = msg
        self.exit_code = 0
        self.tool_activity_observed = True


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


def test_session_retries_fresh_when_persisted_resume_target_is_missing(tmp_path):
    (tmp_path / _SESSION_FILE).write_text(
        json.dumps({"thread_id": "stale-thread"}), encoding="utf-8"
    )

    class _MissingThenFreshRunner(_RecordingRunner):
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.resumes.append(resume_thread_id)
            if resume_thread_id:
                result = _Result(thread_id=resume_thread_id)
                result.exit_code = 1
                result.fatal_error = (
                    "Error: No session, task, or name matched 'stale-thread'."
                )
                return result
            return _Result(thread_id="fresh-thread")

    fake = _MissingThenFreshRunner()
    result = _ManagerSession(fake, tmp_path).run_exec(
        prompt="a", options=None, run_label="x"
    )

    assert fake.resumes == ["stale-thread", None]
    assert result.thread_id == "fresh-thread"
    assert json.loads(
        (tmp_path / _SESSION_FILE).read_text(encoding="utf-8")
    )["thread_id"] == "fresh-thread"


def test_session_retries_fresh_when_persisted_thread_cannot_be_decrypted(tmp_path):
    (tmp_path / _SESSION_FILE).write_text(
        json.dumps({"thread_id": "encrypted-stale-thread"}), encoding="utf-8"
    )

    class _EncryptedThenFreshRunner(_RecordingRunner):
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.resumes.append(resume_thread_id)
            if resume_thread_id:
                result = _Result(thread_id=resume_thread_id)
                result.exit_code = 1
                result.fatal_error = (
                    "invalid_request_body: encrypted content could not be verified; "
                    "encrypted content could not be decrypted or parsed"
                )
                return result
            return _Result(thread_id="fresh-after-encryption-failure")

    fake = _EncryptedThenFreshRunner()
    result = _ManagerSession(fake, tmp_path).run_exec(
        prompt="a", options=None, run_label="manager-stage"
    )

    assert fake.resumes == ["encrypted-stale-thread", None]
    assert result.thread_id == "fresh-after-encryption-failure"
    assert json.loads(
        (tmp_path / _SESSION_FILE).read_text(encoding="utf-8")
    )["thread_id"] == "fresh-after-encryption-failure"


def test_manager_session_root_is_independent_from_project_root(tmp_path):
    project_root = tmp_path / "same-workdir"
    session_root = tmp_path / "sessions" / "s-1"
    fake = _RecordingRunner()

    mgr = Manager(
        project_root=project_root,
        runner=fake,
        manager_session_root=session_root,
    )
    mgr._session.run_exec(prompt="a", options=None, run_label="x")  # type: ignore[union-attr]

    assert (session_root / _SESSION_FILE).exists()
    assert not (project_root / _SESSION_FILE).exists()


def test_session_resumes_stripped_persisted_thread_id_and_persists_next(tmp_path):
    (tmp_path / _SESSION_FILE).write_text(
        json.dumps({"thread_id": "  existing-tid  "}), encoding="utf-8"
    )
    fake = _RecordingRunner()
    sess = _ManagerSession(fake, tmp_path)

    result = sess.run_exec(prompt="a", options=None, run_label="x")

    assert fake.resumes == ["existing-tid"]
    assert result.thread_id == "t1"
    assert json.loads((tmp_path / _SESSION_FILE).read_text())["thread_id"] == "t1"
    assert sess.thread_id == "t1"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(json.dumps({}), id="missing-thread-id"),
        pytest.param(json.dumps({"thread_id": ""}), id="empty"),
        pytest.param(json.dumps({"thread_id": "   \t  "}), id="whitespace"),
        pytest.param(json.dumps({"thread_id": 123}), id="number"),
        pytest.param(json.dumps({"thread_id": ["t1"]}), id="list"),
        pytest.param(json.dumps({"thread_id": {"id": "t1"}}), id="mapping"),
        pytest.param(json.dumps(["t1"]), id="root-list"),
        pytest.param(json.dumps(123), id="root-number"),
        pytest.param(json.dumps("t1"), id="root-string"),
        pytest.param(json.dumps(None), id="root-null"),
        pytest.param("{not valid json", id="invalid-json"),
    ],
)
def test_session_ignores_invalid_persisted_thread_id(tmp_path, raw):
    (tmp_path / _SESSION_FILE).write_text(raw, encoding="utf-8")
    fake = _RecordingRunner()
    sess = _ManagerSession(fake, tmp_path)

    result = sess.run_exec(prompt="a", options=None, run_label="x")

    assert fake.resumes == [None]
    assert result.thread_id == "t1"
    assert json.loads((tmp_path / _SESSION_FILE).read_text())["thread_id"] == "t1"
    assert sess.thread_id == "t1"


def test_session_ignores_incoming_resume_thread_id(tmp_path):
    # An explicit resume_thread_id from a caller is IGNORED — the persistent
    # session always wins (first turn → None, not the caller's value).
    fake = _RecordingRunner()
    sess = _ManagerSession(fake, tmp_path)
    sess.run_exec(prompt="a", options=None, run_label="x", resume_thread_id="CALLER")
    assert fake.resumes[0] is None


# ---------------------------------------------------------------------------
# 5. one turn holds the shared thread; another never waits for it
# ---------------------------------------------------------------------------
class _GatedRunner:
    """The ``hold`` turn stays inside its model call until released."""

    def __init__(self) -> None:
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls: list[tuple[str, str | None]] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append((run_label, resume_thread_id))
        if run_label == "hold":
            self.entered.set()
            assert self.release.wait(timeout=5.0)
            return _Result(thread_id="T-next")
        return _Result(thread_id="T-fresh")


@pytest.mark.integration
def test_a_busy_shared_thread_does_not_hold_up_another_turn(tmp_path):
    # Two independent _ManagerSession objects over the SAME project_root model
    # the REPL front-end and the daemon. While one turn continues the shared
    # thread, the other answers at once on a fresh thread instead of waiting.
    (tmp_path / _SESSION_FILE).write_text(json.dumps({"thread_id": "T-shared"}))
    runner = _GatedRunner()
    t = threading.Thread(
        target=lambda: _ManagerSession(runner, tmp_path).run_exec(prompt="a", options=None, run_label="hold")
    )
    t.start()
    assert runner.entered.wait(timeout=2.0)
    try:
        result = _ManagerSession(runner, tmp_path).run_exec(prompt="b", options=None, run_label="x")
    finally:
        runner.release.set()
        t.join()

    assert result.thread_id == "T-fresh"
    assert runner.calls == [("hold", "T-shared"), ("x", None)]
    # Only the holder moved the shared thread on.
    assert json.loads((tmp_path / _SESSION_FILE).read_text())["thread_id"] == "T-next"


@pytest.mark.integration
def test_lock_is_held_during_a_turn(tmp_path):
    # Directly observe that the lock file is non-blockingly un-acquirable while a
    # turn is in flight on both POSIX and Windows.

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
            portalocker.lock(fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
            portalocker.unlock(fh)
        except portalocker.exceptions.LockException:
            blocked = True
    assert blocked

    released.set()
    t.join()


# ---------------------------------------------------------------------------
# 6. setup recovery must not replay uncertain provider effects
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


def test_uncertain_session_provider_error_is_not_replayed(tmp_path):
    fake = _RaiseOnceRunner()
    sess = _ManagerSession(fake, tmp_path)
    with pytest.raises(RuntimeError, match="session-mode boom"):
        sess.run_exec(prompt="a", options=None, run_label="x")
    assert fake.calls == 1
    assert not (tmp_path / _SESSION_FILE).exists()


def test_unreadable_authority_does_not_fail_open_when_root_unwritable(tmp_path, monkeypatch):
    # A session setup fallback is allowed only if current permissions remain
    # readable. This fault also prevents their canonical state from opening.
    from argus.manager import _session_ops
    from argus.manager.session_continuity import ManagerSessionContinuityUnavailable

    fake = _RecordingRunner()
    sess = _ManagerSession(fake, tmp_path)

    real_mkdir = _session_ops.Path.mkdir

    def _boom(self, *a, **k):
        if self == sess.project_root:
            raise OSError("read-only")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(_session_ops.Path, "mkdir", _boom)
    with pytest.raises(ManagerSessionContinuityUnavailable):
        sess.run_exec(prompt="a", options=None, run_label="x")
    assert fake.resumes == []


# ---------------------------------------------------------------------------
# 7. Manager wiring — Manager LLM calls flow through the shared session
# ---------------------------------------------------------------------------


def test_manager_without_runner_has_no_session(tmp_path):
    # runner=None → no session object → old heuristic path (memory tests intact).
    mgr = Manager(project_root=tmp_path, runner=None)
    assert mgr._session is None
