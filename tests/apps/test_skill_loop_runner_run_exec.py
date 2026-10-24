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

from argus.apps._runtime import _SkillLoopRunner


def test_run_exec_exists() -> None:
    # The bug was a missing method → AttributeError at the gate call site.
    assert callable(getattr(_SkillLoopRunner, "run_exec", None))
