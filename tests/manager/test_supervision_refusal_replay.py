"""Replay of s-efe9deac: Manager checks the host refused before asking the model.

Nine checks there ended in ``observation_incomplete`` within milliseconds:
task objectives longer than the snapshot budget, or a previous 135 KB receipt
past the read bound. Among them were the Engineer's two "no GPU was assigned"
handoffs at 13:27, which never reached the Manager. The fixture keeps what
each refused receipt observed: the objective, each task's requirements and
status, the trigger, and the size of the receipt then in latest.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.models import RunnerResult
from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
from argus.daemon.state import write_continuous_config
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager, supervision
from argus.manager.observation_projection import (
    EVIDENCE_PREAMBLE,
    MAX_OBSERVATION_BYTES,
    render_facts,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "supervision_refused"
RECORD = json.loads((FIXTURE / "receipts.json").read_text(encoding="utf-8"))


class _Model:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.prompts.append(prompt)
        bridge_request(PREFIX, "hand_off", {
            "to": "team", "summary": "The course holds.", "note": "Bind the GPU the scheduler chose, then train.",
            "evidence_refs": ["backlog.jsonl"], "assumptions": [],
        }, env=options.extension_env)
        return RunnerResult(exit_code=0, call_id="replay", agent_messages=["Judged."])


def _restore(root: Path, receipt: dict) -> dict[str, dict]:
    write_continuous_config(root, enabled=True, objective=RECORD["objective"])
    tasks = {}
    for row in receipt["tasks"]:
        task = tasks[row["id"]] = json.loads((FIXTURE / "tasks" / f"{row['id']}.json").read_text(encoding="utf-8"))
        item = BacklogItem.new(item_id=row["id"], title=task["title"], objective=task["objective"])
        item.status, item.acceptance_check, item.pending_question = (
            row["status"], task["acceptance_check"], task["pending_question"])
        Backlog(root / "backlog.jsonl").add(item)
    latest = receipt["latest_receipt"]
    if latest:
        # The previous receipt at its recorded size: its evidence_revision held
        # the whole snapshot it observed.
        body = {"version": 1, "id": latest["id"], "status": "failed",
                "error_code": "observation_incomplete", "evidence_revision": ""}
        body["evidence_revision"] = "x" * (latest["bytes"] - len(json.dumps(body)))
        path = root / "manager-supervision" / "latest.json"
        path.parent.mkdir()
        path.write_text(json.dumps(body), encoding="utf-8")
        assert path.stat().st_size == latest["bytes"]
    return tasks


@pytest.mark.parametrize("receipt", RECORD["receipts"], ids=lambda receipt: receipt["at"])
def test_a_check_refused_for_unread_facts_now_asks_the_manager(tmp_path, receipt):
    assert receipt["recorded"]["error_code"] == "observation_incomplete"
    tasks = _restore(tmp_path, receipt)
    model = _Model()
    trigger = receipt["trigger"]
    if trigger["consult_reason"].startswith(supervision.HANDED_TO_MANAGER["engineer_handoff"]):
        # An Engineer's handoff now reaches the Manager as a stuck process.
        trigger = {**trigger, "process_stuck": True}

    record = supervision.supervise(Manager(tmp_path, runner=model), tmp_path, trigger)

    assert len(model.prompts) == 1
    assert record["status"] == "applied" and "error_code" not in record
    assert ("## Process stuck brief" in model.prompts[0]) == bool(trigger.get("process_stuck"))
    facts, _ = json.JSONDecoder().raw_decode(model.prompts[0].partition(EVIDENCE_PREAMBLE)[2])
    assert facts["objective"] == RECORD["objective"]
    assert {row["id"] for row in facts["tasks"]} == set(tasks)
    # A requirement is shown whole while the snapshot holds it; past the budget
    # the longest is cut to a prefix, and the cut is said.
    for row in facts["tasks"]:
        objective = tasks[row["id"]]["objective"]
        cut = f"tasks[{row['id']}].objective: only the first 1600 of {len(objective)} characters were observed"
        assert row["objective"] == (objective[:1600] if cut in facts["limitations"] else objective)
    whole = {**facts, "tasks": [{**row, "objective": tasks[row["id"]]["objective"]} for row in facts["tasks"]]}
    assert whole == facts or len(render_facts(whole).encode("utf-8")) > MAX_OBSERVATION_BYTES
    # The Manager's own earlier receipt is not project evidence.
    assert "manager-supervision/latest.json" not in model.prompts[0]
