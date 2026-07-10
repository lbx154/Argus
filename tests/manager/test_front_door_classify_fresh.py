"""Regression: ``Manager.classify_front_door`` must run FRESH on the raw backend
— never resume the persistent Manager session, and with a LOW-effort classify.

Same discipline as ``classify_config_intent`` (see test_config_intent_fresh):
the merged front-door classify is a stateless two-axis label call. It must go to
``self.runner`` with ``resume_thread_id=None`` (no giant-session resume, which is
what made every cockpit message slow), at the cheap ``low`` effort by default.
"""
from __future__ import annotations

from argus_skill.manager._core import Manager


class _FakeResult:
    def __init__(self, msg: str, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.last_agent_message = msg


class _RecordingBackend:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict] = []

    def run_exec(self, **kwargs) -> _FakeResult:
        self.calls.append(kwargs)
        return _FakeResult(self.answer)


class _ExplodingSession:
    def run_exec(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError(
            "classify_front_door must NOT resume the persistent Manager session"
        )


def _manager(answer: str, tmp_path) -> tuple[Manager, _RecordingBackend]:
    backend = _RecordingBackend(answer)
    mgr = Manager(project_root=tmp_path, runner=backend)
    mgr._session = _ExplodingSession()  # explode if resumed
    return mgr, backend


def test_front_door_runs_fresh_low_effort(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", raising=False)
    mgr, backend = _manager("CONFIG: NONE\nROUTE: SELF", tmp_path)

    intent, route = mgr.classify_front_door("你好")

    assert intent is None
    assert route == "simple"
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["resume_thread_id"] is None                    # fresh, no session
    assert call["run_label"] == "manager-frontdoor-classify"
    assert call["options"].reasoning_effort == "low"           # cheap by default


def test_front_door_effort_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "medium")
    mgr, backend = _manager("CONFIG: NONE\nROUTE: TEAM", tmp_path)
    mgr.classify_front_door("optimize as many kernels as possible")
    assert backend.calls[0]["options"].reasoning_effort == "medium"


def test_front_door_config_axis(tmp_path) -> None:
    mgr, backend = _manager("CONFIG: SET backend ALL copilot\nROUTE: SELF", tmp_path)
    intent, route = mgr.classify_front_door("用 copilot")
    assert intent is not None and intent.knob == "backend" and intent.value == "copilot"
    # the exploding session was never touched (else the assertion above would fire)


def test_explicit_run_exec_still_honoured(tmp_path) -> None:
    mgr, backend = _manager("CONFIG: NONE\nROUTE: SELF", tmp_path)
    seen: list[str] = []

    def _explicit(prompt: str) -> _FakeResult:
        seen.append(prompt)
        return _FakeResult("CONFIG: SET safe_mode - on\nROUTE: SELF")

    intent, route = mgr.classify_front_door("be careful", run_exec=_explicit)
    assert intent is not None and intent.knob == "safe_mode"
    assert route == "simple"
    assert seen and not backend.calls  # used the explicit run_exec, not the default
