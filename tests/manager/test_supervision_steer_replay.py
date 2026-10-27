"""Replay of s-efe9deac, 10-27 07:03:32: a Manager steer stopped the Planner.

Receipt 89dbfec3 told the team the review counts and CARE keeps running.
Applying it advanced the continuous generation from 10 to 11, which the
running Planner reads as a newer operator instruction: started at 07:03:28,
it stopped as "superseded by newer continuous generation" though no operator
had spoken. The fixture keeps the control the Manager judged and its decision.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.pipeline_state import write_pipeline_state
from argus.core.workspace_facts import fact_text
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.manager import Manager, supervision
from argus.manager.directive import load_active_manager_directive, set_active_manager_directive
from argus.manager.observation import control_identity

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "steer_0703.json").read_text(encoding="utf-8")
)
CONTROL, RECEIPT = FIXTURE["control"], FIXTURE["receipt"]
OBJECTIVE = CONTROL["continuous"]["objective"]
SOURCE = f"manager.supervision:{RECEIPT['id']}"


class _NoModel:
    def fork(self):
        return self

    def run_exec(self, **_):
        raise AssertionError("an issued decision is applied without asking the model again")


def _restore(root: Path) -> None:
    """Generation 10, the Experiment stage and 9d3f6c89's directive, with 89dbfec3 issued."""
    (root / "continuous.json").write_text(json.dumps(CONTROL["continuous"], ensure_ascii=False), encoding="utf-8")
    write_pipeline_state(root, CONTROL["pipeline"])
    (root / "active_manager_directive.json").write_text(json.dumps(CONTROL["directive"], ensure_ascii=False), encoding="utf-8")
    assert fact_text(control_identity(root)) == fact_text(CONTROL)
    supervision._write(root / "manager-supervision" / f"{RECEIPT['id']}.json", {
        **RECEIPT, "version": 1, "control_revision": fact_text(CONTROL), "status": "issued",
    })


def _running_planner(root: Path):
    """The stop check of the Planner started at 07:03:28."""
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(root), runner=object(), planner_runner=object(),
        sink=JsonlEventSink(None, life_dir=root),
        config=LifeSupervisorConfig(
            continuous=True, continuous_objective=OBJECTIVE, project_worktree=root, artifact_root=root,
        ),
    )
    stopped = supervisor._planner_config().external_interrupt_reason_provider
    assert stopped() is None
    return stopped


def test_a_steer_leaves_the_campaign_and_the_running_planner_alone(tmp_path):
    _restore(tmp_path)
    campaign = (tmp_path / "continuous.json").read_bytes()
    stopped = _running_planner(tmp_path)

    record = supervision.supervise(Manager(tmp_path, runner=_NoModel()), tmp_path, RECEIPT["trigger"])

    assert record["status"] == "applied"
    assert (tmp_path / "continuous.json").read_bytes() == campaign
    assert stopped() is None
    directive = load_active_manager_directive(tmp_path)
    assert (directive.text, directive.source) == (RECEIPT["decision"]["directive"], SOURCE)
    # Meant for the team, the steer does not ask the Planner again either.
    assert directive.planner_revision == CONTROL["directive"]["planner_revision"]


@pytest.mark.parametrize("goal_changed", [False, True], ids=["replayed", "goal-changed"])
def test_a_steer_replayed_after_its_directive_landed(tmp_path, goal_changed):
    """The directive can land before the applied receipt does. Replayed, the
    decision is delivered once; once the operator has changed the goal, it is stale."""
    _restore(tmp_path)
    landed = set_active_manager_directive(
        tmp_path, RECEIPT["decision"]["directive"], source=SOURCE, scope_objective=OBJECTIVE, for_planner=False,
    )
    if goal_changed:
        write_continuous_config(tmp_path, enabled=True, objective=f"{OBJECTIVE}\n- 先完成 vLLM 接入。")

    record = supervision.supervise(Manager(tmp_path, runner=_NoModel()), tmp_path, RECEIPT["trigger"])

    if goal_changed:
        assert (record["status"], record["error_code"]) == ("superseded", "superseded")
        assert load_active_manager_directive(tmp_path) is None
    else:
        assert record["status"] == "applied" and record["effects"]["directive_revision"] == landed.revision
        assert load_active_manager_directive(tmp_path) == landed
    assert read_continuous_state(tmp_path).generation == (11 if goal_changed else 10)
