from __future__ import annotations

from argus_skill.core.operator_decision import build_operator_decision
from argus_skill.daemon.state import read_continuous_state, write_continuous_config
from argus_skill.life.memory import BacklogItem, MemoryBundle
from argus_skill.webapi import manager_pending_question


def _blocked_project(tmp_path, sid: str = "s-decision"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    mem = MemoryBundle.for_cwd(workspace, global_root=tmp_path, fingerprint=sid)
    mem.init()
    item = mem.backlog.add(BacklogItem.new(title="Blocked", objective="Do work", item_id="item"))
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason="Provider access is missing.",
        question="Use the fallback?",
        recommendation="Use the local fallback.",
    )
    mem.backlog.update(
        item.id,
        status="paused_operator",
        pending_question="Use the fallback?",
        operator_decision=card,
    )
    return mem, card


def test_stop_option_resolves_item_and_disables_campaign(tmp_path) -> None:
    mem, card = _blocked_project(tmp_path)
    write_continuous_config(mem.project_root, enabled=True, objective="standing work")

    result = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "stop",
        global_root=tmp_path,
    )

    assert result is not None and result["stopped"] is True
    item = next(row for row in mem.backlog.all() if row.id == "item")
    assert item.status == "aborted"
    assert item.operator_decision["selected_option"] == "stop"
    assert read_continuous_state(mem.project_root).enabled is False


def test_recommended_option_routes_text_through_manager(tmp_path, monkeypatch) -> None:
    _mem, card = _blocked_project(tmp_path)
    seen: dict[str, object] = {}

    def answer(sid, item_id, text, **kwargs):
        seen.update(sid=sid, item_id=item_id, text=text, kwargs=kwargs)
        return {"resolved": True, "reply": "continued"}

    monkeypatch.setattr(manager_pending_question, "manager_answer_pending_question", answer)
    result = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "recommended",
        expected_revision=1,
        global_root=tmp_path,
    )

    assert result == {
        "resolved": True,
        "reply": "continued",
        "decision_id": card["id"],
    }
    assert seen["text"] == "Use the local fallback."
    assert seen["kwargs"]["decision_option"] == "recommended"
