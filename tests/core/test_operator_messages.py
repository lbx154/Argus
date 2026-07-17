from __future__ import annotations

import json

from argus_skill.core.operator_messages import publish_operator_message
from argus_skill.core.transcript import read_turns


def test_publish_operator_message_is_idempotent_across_transcript_and_event(tmp_path) -> None:
    assert publish_operator_message(
        tmp_path,
        text="Team completed.",
        message_id="team-summary-1",
    )
    assert not publish_operator_message(
        tmp_path,
        text="Team completed.",
        message_id="team-summary-1",
    )

    assert [turn["text"] for turn in read_turns(tmp_path)] == ["Team completed."]
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["message_id"] for event in events if event["type"] == "ui.argus"] == [
        "team-summary-1",
    ]
