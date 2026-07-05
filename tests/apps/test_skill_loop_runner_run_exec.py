"""``_SkillLoopRunner.run_exec`` proxies to the manager backend.

Regression: ``Manager.classify_skill_placement`` passes
``runner=(self._session or self.runner)``. On the daemon ``_session`` is the
``_SkillLoopRunner``, which had NO ``run_exec`` — so the placement judge raised
``AttributeError`` (caught by the gate's ``except`` and spammed
"manager skill placement failed").
These tests pin that the runner forwards ``run_exec`` to the manager backend
(falling back to the default backend), with the call kwargs passed through.
"""
from __future__ import annotations

from argus_skill.apps._runtime import _SkillLoopRunner


class _StubBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):
        self.calls.append(kwargs)
        return f"ran:{self.name}"


def _bare_runner() -> _SkillLoopRunner:
    # Bypass __init__ (it builds real CLI backends); we only test the proxy.
    return _SkillLoopRunner.__new__(_SkillLoopRunner)


def test_run_exec_exists() -> None:
    # The bug was a missing method → AttributeError at the gate call site.
    assert callable(getattr(_SkillLoopRunner, "run_exec", None))


def test_run_exec_delegates_to_manager_backend() -> None:
    r = _bare_runner()
    r.manager_backend = _StubBackend("manager")
    r._backend = _StubBackend("default")
    out = r.run_exec(prompt="p", options=None, run_label="manager.skill_placement")
    assert out == "ran:manager"
    assert r.manager_backend.calls[0]["run_label"] == "manager.skill_placement"
    assert not r._backend.calls  # never touches the default when manager is set


def test_run_exec_falls_back_to_default_backend() -> None:
    r = _bare_runner()
    r.manager_backend = None
    r._backend = _StubBackend("default")
    out = r.run_exec(prompt="p", run_label="manager.skill_placement")
    assert out == "ran:default"
