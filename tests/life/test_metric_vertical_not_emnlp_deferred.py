"""A metric (non-research) vertical run open-ended must NOT have its legitimate
project_done deferred by the EMNLP/paper completion gate just because the raw
config flag defaults True. The gate the supervisor consults must be the
vertical-effective one. (Careful-hunt finding; roadmap #3-core.)
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus_skill.life.memory import BacklogItem
from argus_skill.life.supervisor._core import LifeSupervisor
from argus_skill.skills import checklist_store as cs
from argus_skill.verticals import _data_domain as dd


def _supervisor(*, effective_gate: bool, tmp_path: Path) -> tuple[LifeSupervisor, list[bool]]:
    consulted: list[bool] = []
    sup = LifeSupervisor.__new__(LifeSupervisor)
    # Raw flag is True (open-ended default), but the VERTICAL-EFFECTIVE gate is
    # what the supervisor must consult.
    sup.config = SimpleNamespace(
        full_emnlp_gate=True,
        artifact_root=tmp_path,
        telemetry_dir=None,
    )
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


def test_stale_research_state_with_bounded_data_domain_disables_emnlp_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A Manager-authored bounded domain must beat stale ``vertical=research``.

    This pins the perf_tuning regression: the completed bounded report workspace
    had ``PIPELINE_STATE.json`` stuck at ``vertical=research`` / ``inspect`` while
    the active checklist store and domain index clearly described a
    completion_gate=none data domain. The final-submission override must not fire
    in that state.
    """
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(
        tmp_path,
        "perf_tuning",
        stages=["profile", "isolate", "optimize", "benchmark", "test", "report"],
    )
    state_path = tmp_path / "research" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"current_stage": "inspect", "vertical": "research"}\n',
        encoding="utf-8",
    )
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "profile", "id": "profile.ground_truth",
         "statement": "measure ground truth"},
        {"op": "add", "stage": "report", "id": "report.final",
         "statement": "write the bounded report"},
    ])

    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.config = SimpleNamespace(full_emnlp_gate=True)

    assert sup._effective_full_emnlp_gate(tmp_path) is False
    assert state_path.read_text(encoding="utf-8") == (
        '{"current_stage": "inspect", "vertical": "research"}\n'
    )


def test_tick_skips_inapplicable_final_submission_for_bounded_domain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    dd.write_data_domain(
        tmp_path,
        "perf_tuning",
        stages=["profile", "isolate", "optimize", "benchmark", "test", "report"],
    )
    state_path = tmp_path / "research" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"current_stage": "inspect", "vertical": "research"}\n',
        encoding="utf-8",
    )
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "profile", "id": "profile.ground_truth",
         "statement": "measure ground truth"},
        {"op": "add", "stage": "report", "id": "report.final",
         "statement": "write the bounded report"},
    ])
    item = BacklogItem.new(
        title="Prove final submission readiness",
        objective="Project-final task. Scope: final_submission.",
        tags=["planner", "scope:final_submission"],
    )
    updates: list[dict] = []

    class _Backlog:
        def next_pending(self):
            return item

        def update(self, item_id, **fields):
            updates.append({"item_id": item_id, **fields})
            return item

    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.config = SimpleNamespace(
        full_emnlp_gate=True,
        artifact_root=tmp_path,
        telemetry_dir=None,
    )
    sup.memory = SimpleNamespace(backlog=_Backlog())
    sup._emit = lambda event: updates.append({"event": event})  # type: ignore[method-assign]
    sup._emit_status = lambda status: updates.append({"status": status})  # type: ignore[method-assign]
    sup.runner = SimpleNamespace(
        execute=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("runner must not be called")
        )
    )

    result = sup.tick()

    assert result["status"] == "skipped"
    assert updates[0]["item_id"] == item.id
    assert updates[0]["status"] == "skipped"
    assert "not full_emnlp" in updates[0]["last_error"]
    assert state_path.read_text(encoding="utf-8") == (
        '{"current_stage": "inspect", "vertical": "research"}\n'
    )
