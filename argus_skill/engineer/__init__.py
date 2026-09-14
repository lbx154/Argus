"""The Engineer role: one Reviewer-gated round loop over a real backend.

Layer: roles

Belongs here: ``runner.SupervisedEngineer`` and the ``round_*`` phase modules
it is split into (config, prompt, execution, waits, reviewer call,
self-review, settlement, signals, state, stop-signal classification), the
Markdown ``checkpoint`` shared with fresh Reviewer turns, the
``external_work`` liveness protocol for long-running jobs, and
``background_subagents`` cost folding. The Reviewer itself lives in the
sibling ``reviewer`` package: it is the single source of truth for
done / continue / blocked and this package only invokes it.

Does not belong here (and where it goes): named verticals (this package must
stay vertical-agnostic; tests/test_architecture_invariants.py enforces it),
Skill libraries (``skills``), and the per-mission loop that drives rounds
(``loop.py`` today, ``mission_runner/`` after phase 5). An Engineer that
imports a vertical makes every vertical load for every mission.
"""
