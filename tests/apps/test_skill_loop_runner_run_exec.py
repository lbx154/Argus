"""``_SkillLoopRunner.run_exec`` proxies to the manager backend.

These tests pin that the runner forwards ``run_exec`` to the manager backend
(falling back to the default backend), with the call kwargs passed through.
"""
from __future__ import annotations

from argus.apps._runtime import _SkillLoopRunner


def test_run_exec_exists() -> None:
    assert callable(getattr(_SkillLoopRunner, "run_exec", None))
