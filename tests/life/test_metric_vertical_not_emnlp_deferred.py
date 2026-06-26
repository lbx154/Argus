"""A metric (non-research) vertical run open-ended must NOT have its legitimate
project_done deferred by the EMNLP/paper completion gate just because the raw
config flag defaults True. The gate the supervisor consults must be the
vertical-effective one. (Careful-hunt finding; roadmap #3-core.)
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus_skill.life.supervisor._core import LifeSupervisor


def _supervisor(*, effective_gate: bool, tmp_path: Path) -> tuple[LifeSupervisor, list[bool]]:
    consulted: list[bool] = []
    sup = LifeSupervisor.__new__(LifeSupervisor)
    # Raw flag is True (open-ended default), but the VERTICAL-EFFECTIVE gate is
    # what the supervisor must consult.
    sup.config = SimpleNamespace(full_emnlp_gate=True)
    sup._effective_full_emnlp_gate = lambda _w: effective_gate  # type: ignore[attr-defined]
    sup._project_workdir = lambda: tmp_path  # type: ignore[attr-defined]
    sup._journal_has_full_emnlp_gate_success = lambda: False  # type: ignore[attr-defined]

    def _wait_reason() -> None:
        consulted.append(True)  # only reached once the paper gate has passed
        return None

    sup._operator_only_external_blocker_wait_reason = _wait_reason  # type: ignore[attr-defined]
    return sup, consulted


def test_metric_vertical_project_done_is_not_deferred(tmp_path: Path) -> None:
    # Metric vertical → effective gate False → the paper-cert defer must short-circuit
    # at the gate, even with the raw config flag True and no certification.
    sup, consulted = _supervisor(effective_gate=False, tmp_path=tmp_path)
    verdict = SimpleNamespace(project_done=True)
    out = sup._defer_project_done_for_operator_external_blocker(verdict)
    assert out is verdict  # unchanged — legitimate project_done stands
    assert consulted == []  # never reached the blocker check; gate stopped it cold


def test_research_vertical_passes_the_gate_then_checks_for_a_blocker(tmp_path: Path) -> None:
    # Research vertical → effective gate True → the defer logic gets PAST the gate and
    # consults the external-blocker reason (which is None here → verdict returned),
    # proving the gate is still honored for research.
    sup, consulted = _supervisor(effective_gate=True, tmp_path=tmp_path)
    verdict = SimpleNamespace(project_done=True)
    out = sup._defer_project_done_for_operator_external_blocker(verdict)
    assert out is verdict  # no blocker → not deferred
    assert consulted == [True]  # but it DID pass the gate and check for a blocker
