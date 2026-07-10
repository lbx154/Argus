"""Regression: ``Manager.classify_config_intent`` must run FRESH, not resume the
persistent Manager session.

Config-intent is a stateless yes/no check on the current message. It used to
default its LLM call through ``self._session`` (``_backend = self._session or
self.runner``), which resumes the persistent Manager session — reloading its
full history on EVERY operator message. On a long-lived copilot session that was
adding ~30s per message at the cockpit front door (and polluting the
conversation with throwaway classify prompts). ``route`` already runs fresh at
the front door for the same reason; these pin config-intent to the same
behaviour: a fresh call on the RAW backend with ``resume_thread_id=None``, and
the persistent session never touched.
"""
from __future__ import annotations

from argus_skill.manager._core import Manager


class _FakeResult:
    def __init__(self, msg: str, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.last_agent_message = msg


class _RecordingBackend:
    """Raw backend spy: records every run_exec call's kwargs."""

    def __init__(self, answer: str = "NONE") -> None:
        self.answer = answer
        self.calls: list[dict] = []

    def run_exec(self, **kwargs) -> _FakeResult:
        self.calls.append(kwargs)
        return _FakeResult(self.answer)


class _ExplodingSession:
    """Stand-in for the persistent session: fails the test if resumed."""

    def run_exec(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError(
            "config-intent must NOT resume the persistent Manager session"
        )


def _manager(answer: str, tmp_path) -> tuple[Manager, _RecordingBackend]:
    backend = _RecordingBackend(answer)
    mgr = Manager(project_root=tmp_path, runner=backend)
    # Replace the real persistent session with one that explodes if used, so the
    # test proves config-intent goes to the raw backend, not the session.
    mgr._session = _ExplodingSession()
    return mgr, backend


def test_config_intent_runs_fresh_not_through_session(tmp_path) -> None:
    mgr, backend = _manager("SET backend ALL copilot", tmp_path)
    intent = mgr.classify_config_intent("用 copilot")
    # It still classifies correctly (the exploding session was never touched — if
    # it had been, the AssertionError above would have failed the test)…
    assert intent is not None
    assert intent.knob == "backend"
    assert intent.value == "copilot"
    # …and the raw-backend call was a FRESH one (no session resume).
    assert len(backend.calls) == 1
    assert backend.calls[0]["resume_thread_id"] is None
    assert backend.calls[0]["run_label"] == "manager-config-intent"


def test_config_intent_none_verdict_also_fresh(tmp_path) -> None:
    mgr, backend = _manager("NONE", tmp_path)
    assert mgr.classify_config_intent("train a model on imagenet") is None
    assert backend.calls[0]["resume_thread_id"] is None


def test_explicit_run_exec_still_honoured(tmp_path) -> None:
    # An explicit run_exec must still win (front-door callers pass their own).
    mgr, backend = _manager("NONE", tmp_path)
    seen: list[str] = []

    def _explicit(prompt: str) -> _FakeResult:
        seen.append(prompt)
        return _FakeResult("SET safe_mode - on")

    intent = mgr.classify_config_intent("be careful", run_exec=_explicit)
    assert intent is not None and intent.knob == "safe_mode"
    assert seen  # the explicit run_exec was used
    assert not backend.calls  # the default raw-backend path was NOT built
