"""One small project, written by the runtime's own writers, and every prompt
text that names its state files.

``tests/test_derived_state_prompts.py`` compares these texts with the ones
recorded before derived state stopped being written to disk
(``tests/fixtures/derived_state_prompts.json``).
"""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

MISSION_ID = "a1b2c3d4e5f6"


def _clock(monkeypatch) -> None:
    ticks = itertools.count(1_800_000_000, 7)
    import time

    monkeypatch.setattr(time, "time", lambda: float(next(ticks)))


def build(root: Path, monkeypatch) -> dict[str, str]:
    """Write the project under ``root`` and return the prompt texts, paths as ``<root>``."""
    from argus.core.role_session import RoleSessionCapsule
    from argus.engineer.checkpoint import shared_checkpoint_instructions
    from argus.life.context_packet import (
        create_mission_context,
        record_engineer_handoff,
        record_reviewed_handoff,
        render_mission_brief,
    )
    from argus.life.event_log import JsonlEventSink
    from argus.life.memory import Backlog, BacklogItem
    from argus.manager import supervision
    from argus.manager.observation import observe_project
    from argus.manager.session_continuity import _control_capsule

    _clock(monkeypatch)
    root.mkdir(parents=True, exist_ok=True)
    (root / "continuous.json").write_text(json.dumps({
        "version": 1, "enabled": True, "objective": "Measure the parser speed-up", "generation": 1,
    }), encoding="utf-8")
    item = BacklogItem.new(title="Speed up the parser", objective="Make parse() twice as fast",
                           item_id=MISSION_ID)
    Backlog(root / "backlog.jsonl").add(item)
    sink = JsonlEventSink(None, life_dir=root)
    for event in (
        {"type": "life.manager.intent.completed", "ts": 10, "item_id": MISSION_ID,
         "objective": "Make parse() twice as fast", "vertical": "software", "stages": []},
        {"type": "life.mission.started", "ts": 11, "item_id": MISSION_ID,
         "title": "Speed up the parser", "objective": "Make parse() twice as fast"},
        {"type": "round.review.completed", "ts": 12, "item_id": MISSION_ID, "round_index": 1,
         "status": "continue", "reason": "Add a benchmark against the old parser."},
    ):
        assert sink.append(event)

    mission = create_mission_context(
        life_dir=root, mission_id=MISSION_ID, stage="build",
        objective="Make parse() twice as fast", acceptance_check="benchmark shows 2x",
    )
    checkpoint = mission.parent / "CHECKPOINT.md"
    texts: dict[str, str] = {}
    texts["brief_before_rounds"] = render_mission_brief(mission)

    def observed() -> str:
        facts: dict[str, Any] = dict(observe_project(root).facts)
        facts.pop("observed_at", None)
        return json.dumps(facts, sort_keys=True, ensure_ascii=False)

    def references() -> str:
        capsule = RoleSessionCapsule.open(
            role="engineer", policy="rolling", objective_revision="r1", workdir=root,
            backend="codex", model="m", checkpoint_path=checkpoint,
            path=mission.parent / "engineer.json", mission_context_path=str(mission),
        )
        return capsule.prompt_block() + "\n" + shared_checkpoint_instructions(checkpoint, role="engineer")

    texts["references_before_rounds"] = references()
    texts["manager_observation_before_rounds"] = observed()
    record_engineer_handoff(
        mission_context_path=mission, round_index=1,
        engineer_summary="Rewrote the tokenizer loop.", checkpoint_path=checkpoint,
    )
    texts["brief_pending_engineer"] = render_mission_brief(mission)
    review = SimpleNamespace(status="continue", reason="Add a benchmark against the old parser.",
                             next_action="Benchmark both parsers on the same input.")
    record_reviewed_handoff(
        mission_context_path=mission, round_index=1,
        engineer_summary="Rewrote the tokenizer loop.", review=review, checkpoint_path=checkpoint,
    )
    texts["brief_reviewed"] = render_mission_brief(mission)
    record_engineer_handoff(
        mission_context_path=mission, round_index=2,
        engineer_summary="Added the benchmark; 2.3x faster.", checkpoint_path=checkpoint,
    )
    texts["brief_second_round"] = render_mission_brief(mission)

    texts["references_second_round"] = references()

    record = {
        "id": "0" * 31 + "1", "status": "applied", "trigger": {"type": "life.mission.completed"},
        "decision": {"action": "continue", "reason": "On course."}, "evidence_refs": [],
        "effects": {}, "control_revision": "c1",
    }
    supervision._write(root / "manager-supervision" / f"{record['id']}.json", record)
    texts["supervision_latest"] = json.dumps(supervision._latest_record(root), sort_keys=True)
    capsule_facts, rendered = _control_capsule(root)
    texts["continuity_capsule"] = json.dumps(capsule_facts, sort_keys=True)
    texts["continuity_evidence"] = rendered

    texts["manager_observation"] = observed()

    def normalized(text: str) -> str:
        text = re.sub(re.escape(str(root.resolve())), "<root>", text).replace(str(root), "<root>")
        return re.sub(r'(\\?"event_id\\?": \\?")[0-9a-f]{32}', r"\1<event-id>", text)

    return {name: normalized(text) for name, text in texts.items()}
