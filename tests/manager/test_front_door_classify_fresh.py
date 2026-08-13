"""Regression: ``Manager.classify_front_door`` must run FRESH on the raw backend
— never resume the persistent Manager session, and with bounded classify effort.

The merged front-door classify is a stateless label call. It must go to
``self.runner`` with ``resume_thread_id=None`` (no giant-session resume, which is
what made every cockpit message slow), at ``medium`` effort by default.
"""
from __future__ import annotations

from argus_skill.manager import Manager


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
    monkeypatch.setattr(
        "argus_skill.core.knobs.resolve_manager_classify_model",
        lambda: "fast-manager",
    )
    mgr, backend = _manager(
        "CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF",
        tmp_path,
    )

    intent, control, route = mgr.classify_front_door("你好")

    assert intent is None
    assert control is None
    assert route == "simple"
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["resume_thread_id"] is None                    # fresh, no session
    assert call["run_label"] == "manager-frontdoor-classify"
    assert call["options"].reasoning_effort == "low"
    assert call["options"].model == "fast-manager"


def test_front_door_effort_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "high")
    mgr, backend = _manager(
        "CONFIG: NONE\nCONTROL: NONE\nROUTE: TEAM",
        tmp_path,
    )
    mgr.classify_front_door("optimize as many kernels as possible")
    assert backend.calls[0]["options"].reasoning_effort == "high"


def test_front_door_config_axis(tmp_path) -> None:
    mgr, backend = _manager(
        "CONFIG: SET backend ALL copilot\nCONTROL: NONE\nROUTE: SELF",
        tmp_path,
    )
    intent, control, route = mgr.classify_front_door("用 copilot")
    assert intent is not None and intent.knob == "backend" and intent.value == "copilot"
    assert control is None
    # the exploding session was never touched (else the assertion above would fire)


def test_front_door_no_dispatch_axis(tmp_path) -> None:
    mgr, _backend = _manager(
        "CONFIG: NONE\nCONTROL: NO_DISPATCH\nROUTE: TEAM",
        tmp_path,
    )

    intent, control, route = mgr.classify_front_door(
        "inspect the source tree read-only; do not dispatch"
    )

    assert intent is None
    assert control == "no_dispatch"
    assert route == "simple"


def test_explicit_run_exec_still_honoured(tmp_path) -> None:
    mgr, backend = _manager(
        "CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF",
        tmp_path,
    )
    seen: list[str] = []

    def _explicit(prompt: str) -> _FakeResult:
        seen.append(prompt)
        return _FakeResult(
            "CONFIG: SET safe_mode - on\nCONTROL: NONE\nROUTE: SELF"
        )

    intent, control, route = mgr.classify_front_door(
        "be careful",
        run_exec=_explicit,
    )
    assert intent is not None and intent.knob == "safe_mode"
    assert control is None
    assert route == "simple"
    assert seen and not backend.calls  # used the explicit run_exec, not the default
