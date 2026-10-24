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

import pytest

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
