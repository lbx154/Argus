"""Regression: ``Manager.classify_front_door`` must run FRESH on the raw backend
— never resume the persistent Manager session, and with bounded classify effort.

The merged front-door classify is a stateless form call. It must go to
``self.runner`` with ``resume_thread_id=None`` (no giant-session resume, which is
what made every cockpit message slow), at ``low`` effort by default, with only
the ``hand_off`` action bound.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
from argus.manager import Manager


def _form(**fields: Any) -> dict[str, Any]:
    return {"to": "manager", "intake": {"type": "ephemeral", "scope": "project"}, **fields}


class _RecordingBackend:
    backend = "pi"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def run_exec(self, **kwargs) -> Any:
        self.calls.append(kwargs)
        bridge_request(PREFIX, "hand_off", self.payload, env=kwargs["options"].extension_env)
        return SimpleNamespace(exit_code=0)


class _ExplodingSession:
    def run_exec(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError(
            "classify_front_door must NOT resume the persistent Manager session"
        )


def _manager(payload: dict[str, Any], tmp_path) -> tuple[Manager, _RecordingBackend]:
    backend = _RecordingBackend(payload)
    mgr = Manager(project_root=tmp_path, runner=backend)
    mgr._session = _ExplodingSession()  # explode if resumed
    return mgr, backend


def test_front_door_runs_fresh_low_effort(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_LIGHT_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(
        "argus.core.knobs.resolve_light_model",
        lambda **_kwargs: "fast-manager",
    )
    mgr, backend = _manager(_form(), tmp_path)

    assert mgr.classify_front_door("你好") == (None, None, "simple")
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["resume_thread_id"] is None                    # fresh, no session
    assert call["run_label"] == "manager-frontdoor-classify"
    assert call["options"].reasoning_effort == "low"
    assert call["options"].model == "fast-manager"
    assert call["options"].disable_tools is True
    assert call["options"].trusted_tool_names == ["hand_off"]
    assert not call["options"].watchdog_hard_idle_seconds
    assert call["options"].extra_args == [
        "--system-prompt",
        "Classify the Argus operator message with hand_off only.",
    ]


@pytest.mark.parametrize("selected,expected", [("software", "software"), ("../escape", "")])
@pytest.mark.parametrize("active", [False, True])
def test_single_agent_vertical_selection_is_validated_in_existing_classifier(tmp_path, selected, expected, active):
    mgr, backend = _manager(_form(self_mode="implement", domain={"vertical": selected}), tmp_path)
    decisions = []
    result = mgr.classify_front_door("Fix one function", domain_sink=decisions.append, active_mission=active)
    assert result == (None, None, "simple")
    assert decisions == [{"action": "none", "vertical": expected}]
    assert len(backend.calls) == 1
    assert "domain.vertical" in backend.calls[0]["prompt"]


def test_front_door_effort_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIGHT_REASONING_EFFORT", "high")
    mgr, backend = _manager(_form(to="team"), tmp_path)
    mgr.classify_front_door("optimize as many kernels as possible")
    assert backend.calls[0]["options"].reasoning_effort == "high"


def test_front_door_config_axis(tmp_path) -> None:
    mgr, _backend = _manager(_form(config=[{"knob": "backend", "value": "copilot"}]), tmp_path)
    intent, control, route = mgr.classify_front_door("用 copilot")
    assert intent is not None and intent.knob == "backend" and intent.value == "copilot"
    assert (control, route) == (None, "simple")
    # the exploding session was never touched (else the assertion above would fire)


def test_explicit_run_exec_still_honoured(tmp_path) -> None:
    mgr, backend = _manager(_form(), tmp_path)
    seen: list[str] = []

    def _explicit(prompt: str, actions: Any) -> Any:
        seen.append(prompt)
        actions.dispatch("hand_off", _form(config=[{"knob": "safe_mode", "value": "on"}]))
        return SimpleNamespace(exit_code=0)

    intent, control, route = mgr.classify_front_door("be careful", run_exec=_explicit)
    assert intent is not None and intent.knob == "safe_mode"
    assert (control, route) == (None, "simple")
    assert seen and not backend.calls  # used the explicit run_exec, not the default
