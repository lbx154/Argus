from __future__ import annotations

from argus.core.operator_decision import build_operator_decision
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import BacklogItem, MemoryBundle
from argus.webapi import manager_pending_question


def _bound_blocked_project(tmp_path, sid: str = "s-decision"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    mem = MemoryBundle.for_cwd(workspace, global_root=tmp_path, fingerprint=sid)
    mem.init()
    write_continuous_config(mem.project_root, enabled=True, objective="standing work")
    item = mem.backlog.add(
        BacklogItem.new(title="Blocked", objective="Do work", item_id="item")
    )
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason="Provider access is missing.",
        question="Use the fallback?",
        options=[
            {
                "id": "local-fallback",
                "label": "Use the local fallback",
                "description": "Use the local fallback.",
            },
            {
                "id": "stop",
                "label": "Stop this campaign",
                "description": "Keep the current work and stop.",
            },
        ],
        project_id=sid,
    )
    mem.backlog.update(
        item.id,
        park_reason="operator_pause",
        pending_question="Use the fallback?",
        operator_decision=card,
    )
    return mem, card


def test_stop_decision_replay_does_not_advance_campaign_twice(tmp_path) -> None:
    mem, card = _bound_blocked_project(tmp_path)

    first = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "stop",
        global_root=tmp_path,
    )
    generation_after_first = read_continuous_state(mem.project_root).generation
    second = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "stop",
        global_root=tmp_path,
    )

    assert first is not None and first["application_status"] == "accepted"
    assert second is not None and second["application_status"] == "already_applied"
    assert read_continuous_state(mem.project_root).generation == generation_after_first
