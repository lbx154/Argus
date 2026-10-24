from argus.webapi.map_view import turn_records


def test_cancelled_single_agent_task_survives_history_page_boundaries():
    asks = {}
    turn_records([{"type": "manager.turn.started", "message_id": "web-solo", "ts": 11,
                   "text": "Do work"}], {}, asks)
    later = turn_records([{"type": "manager.turn.cancelled", "message_id": "web-solo", "ts": 12}], {}, asks)
    assert later["turn:web-solo"]["card"]["status"] == "cancelled"
    assert later["turn:web-solo"]["events"][0]["status"] == "cancelled"
    assert not asks
