"""Regression: --bounded must disable the full EMNLP completion gate.

The unify_RL_argus bounded survey loop escalated into repeated "Prove
final submission readiness" missions because LifeSupervisorConfig inherited
`full_emnlp_gate=True` even when CLI bounded mode set open_ended=False.
"""
from __future__ import annotations

import inspect

from argus_skill.apps import _life_repl
from argus_skill.daemon.life_worker import LifeWorker


def test_daemon_bounded_mode_disables_full_emnlp_gate():
    src = inspect.getsource(LifeWorker.run_forever)
    assert "full_emnlp_gate=not cfg.continuous_open_ended" in src


def test_repl_bounded_mode_disables_full_emnlp_gate():
    src = inspect.getsource(_life_repl.run_life_supervisor)
    assert "full_emnlp_gate=not open_ended" in src
