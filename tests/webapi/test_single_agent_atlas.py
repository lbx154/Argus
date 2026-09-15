from argus.webapi.manager_dispatch import _TurnEmitter
from argus.webapi.map_view import turn_records


def test_single_agent_task_is_visible_before_reply_and_finishes_without_tools():
    asks, turns = {}, {}
    rows = [
        {"type": "ui.operator", "message_id": "web-solo-operator", "ts": 10, "text": "Write a short story"},
        {"type": "manager.turn.started", "message_id": "web-solo", "ts": 11, "text": "Write a short story"},
    ]
    running = turn_records(rows, turns, asks)["turn:web-solo"]
    assert running["card"]["status"] == "running" and running["card"]["started_ts"] == 11
    assert running["events"][0]["steps"] == []
    result = turn_records([{"type": "ui.argus", "message_id": "web-solo-argus", "ts": 15,
                            "task_turn": True, "success": True, "text": "The story."}], turns, asks)
    assert len(result) == 1
    assert result["turn:web-solo"]["card"]["status"] == "done"
    assert result["turn:web-solo"]["card"]["started_ts"] == 11
    assert result["turn:web-solo"]["events"][-1]["text"] == "The story."


def test_cancelled_single_agent_task_survives_history_page_boundaries():
    asks = {}
    turn_records([{"type": "manager.turn.started", "message_id": "web-solo", "ts": 11,
                   "text": "Do work"}], {}, asks)
    later = turn_records([{"type": "manager.turn.cancelled", "message_id": "web-solo", "ts": 12}], {}, asks)
    assert later["turn:web-solo"]["card"]["status"] == "cancelled"
    assert later["turn:web-solo"]["events"][0]["status"] == "cancelled"
    assert not asks


def test_emitter_journals_single_agent_task_once_and_retains_tool_free_result(tmp_path):
    import json

    emitter = _TurnEmitter(tmp_path, "web-solo", lambda *_: None, task_objective="Summarize this text", solo=True)
    emitter.start_task()
    emitter.start_task()
    emitter.journal_and_respond("Summary", {"kind": "chat"})
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len([row for row in rows if row["type"] == "manager.turn.started"]) == 1
    result = turn_records(rows)["turn:web-solo"]
    assert result["card"]["status"] == "done"
    assert result["events"][0]["tool_details_recorded"] is False
