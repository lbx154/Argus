"""Vertical packages — domain-specific adapters that sit on top of argus core.

Argus is a 7×24 supervisor framework (daemon + planner/engineer/reviewer
loop + budget + routing + persistence + sandbox + integrity gates) that
is itself **domain-agnostic**. Domain knowledge — what a "stage" means,
what artifacts a "done" mission produces, which gates fire — lives in a
*vertical* package under this namespace.

Current verticals:

* ``argus_skill.verticals.research`` — the EMNLP/ACL/NeurIPS paper
  vertical (research → plan → benchmark → run → analysis → draft → review
  → submission stages, paper/main.tex + paper/refs.bib +
  benchmarks/evidence/ + paper/claims_to_evidence.tsv artifacts,
  paper-structural / evidence-chain / draft-outline gates).
* ``argus_skill.verticals.kernel_engineering`` — environment-first production
  GPU kernel work (scope → environment → baseline → optimize → validate →
  report), distinct from the fixed SOL-ExecBench ``kernelbench`` vertical.
* ``argus_skill.verticals.chip_design`` — complete digital ASIC and accelerator
  work from workload definition through RTL, verification, DFT, synthesis,
  physical implementation, sign-off, public-baseline comparison, and a
  reproducible pre-tapeout release.

Planned verticals:

* ``argus_skill.verticals.quant`` — finance factor research (universe →
  factor → backtest → report).
* ``argus_skill.verticals.rollout`` — RL rollout + scoring jobs
  (prep → dispatch → score → aggregate).

This is a re-export namespace today; physical file relocation is a
follow-up. The point of this anchor commit is to make the dichotomy
*visible in import paths* so new code can be written against the
vertical interface from day one without breaking the running daemon.
"""
from __future__ import annotations
