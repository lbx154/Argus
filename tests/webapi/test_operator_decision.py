from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.core.operator_context import OperatorContextStore
from argus_skill.core.operator_decision import build_operator_decision
from argus_skill.daemon.state import read_continuous_state, write_continuous_config
from argus_skill.life.memory import BacklogItem, MemoryBundle
from argus_skill.manager import front_door
from argus_skill.webapi import manager_pending_question


@pytest.mark.parametrize("asked_at", [100.0, None])
def test_snapshot_preserves_question_time_without_backfilling_legacy_cards(
    tmp_path, asked_at,
) -> None:
    from argus_skill.webapi.project_state import build_snapshot

    mem, card = _blocked_project(tmp_path)
    if asked_at is None:
        card.pop("asked_at")
    else:
        card["asked_at"] = asked_at
    mem.backlog.update("item", operator_decision=card)

    for compact in (False, True):
        snapshot = build_snapshot("s-decision", global_root=tmp_path, compact=compact)
        assert snapshot is not None
        projected = snapshot["backlog"][0]["operator_decision"]
        assert projected.get("asked_at") == asked_at
        assert ("asked_at" in projected) == (asked_at is not None)
        if compact:
            assert snapshot["pending_questions"][0]["operator_decision"] == projected


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
        options=[
            {
                "id": "local-fallback",
                "label": "Use the local fallback",
                "description": "Use the local fallback.",
                "requires_note": False,
            },
            {
                "id": "stop",
                "label": "Stop this campaign",
                "description": "Keep the current work and stop.",
                "requires_note": False,
            },
        ],
    )
    mem.backlog.update(
        item.id,
        status="paused_operator",
        pending_question="Use the fallback?",
        operator_decision=card,
    )
    return mem, card


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
                "requires_note": False,
            },
            {
                "id": "stop",
                "label": "Stop this campaign",
                "description": "Keep the current work and stop.",
                "requires_note": False,
            },
        ],
        project_id=sid,
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
    assert item.operator_decision["resume_requested"] is False
    assert read_continuous_state(mem.project_root).enabled is False
    events = [
        json.loads(line)
        for line in (mem.project_root / "events.jsonl").read_text().splitlines()
    ]
    stopped = [
        event
        for event in events
        if event.get("type") == "life.operator_question.answered"
    ]
    assert stopped and stopped[-1]["stopped"] is True
    assert "event_validation" not in stopped[-1]


def test_agent_option_routes_text_through_answer_handler(tmp_path, monkeypatch) -> None:
    _mem, card = _blocked_project(tmp_path)
    seen: dict[str, object] = {}

    def answer(sid, item_id, text, **kwargs):
        seen.update(sid=sid, item_id=item_id, text=text, kwargs=kwargs)
        return {"resolved": True, "reply": "continued"}

    monkeypatch.setattr(manager_pending_question, "manager_answer_pending_question", answer)
    result = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )

    assert result == {
        "resolved": True,
        "reply": "continued",
        "decision_id": card["id"],
    }
    assert seen["text"] == "Use the local fallback."
    assert seen["kwargs"]["decision_option"] == "local-fallback"


def test_repeated_decision_is_idempotent_across_reopened_memory(
    tmp_path,
    monkeypatch,
) -> None:
    mem, card = _bound_blocked_project(tmp_path)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit decisions must not require a model call")
        ),
    )
    first = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )
    answer_ledger = (mem.project_root / "operator_context.jsonl").read_bytes()
    second = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )

    assert first is not None and first["application_status"] == "accepted"
    assert second is not None and second["application_status"] == "already_applied"
    assert first["item"]["id"] == second["item"]["id"]
    assert first["resolution_id"] == second["resolution_id"]
    assert first["resume_requested"] is True
    assert len(mem.backlog.all()) == 2
    assert (mem.project_root / "operator_context.jsonl").read_bytes() == answer_ledger
    directives = OperatorContextStore(mem.project_root).records()
    assert len(directives) == 1 and directives[0].source == "operator.explicit_answer"

    stale = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "custom",
        "Try a different route.",
        global_root=tmp_path,
    )
    assert stale is not None and stale["application_status"] == "stale"


def test_campaign_generation_change_does_not_block_pending_decision(
    tmp_path,
    monkeypatch,
) -> None:
    mem, card = _bound_blocked_project(tmp_path)
    card["campaign_generation"] = read_continuous_state(mem.project_root).generation
    mem.backlog.update("item", operator_decision=card)
    write_continuous_config(
        mem.project_root,
        enabled=True,
        objective="a newer standing objective",
    )
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit decisions must not require a model call")
        ),
    )

    result = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )

    assert result is not None and result["application_status"] == "accepted"
    assert result["resolved"] is True
    item = next(row for row in mem.backlog.all() if row.id == "item")
    assert item.pending_question == ""
    assert item.operator_decision["status"] == "resolved"


def test_concurrent_same_decision_returns_accepted_and_already_applied(
    tmp_path,
    monkeypatch,
) -> None:
    mem, card = _bound_blocked_project(tmp_path)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit decisions must not require a model call")
        ),
    )

    def resolve():
        return manager_pending_question.manager_resolve_operator_decision(
            "s-decision",
            card["id"],
            "local-fallback",
            global_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: resolve(), range(2)))

    assert {result["application_status"] for result in results if result} == {
        "accepted",
        "already_applied",
    }
    assert len(mem.backlog.all()) == 2


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
