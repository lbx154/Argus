from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus_skill.life.context_packet import (
    create_mission_context,
    record_engineer_handoff,
    record_reviewed_handoff,
)


def test_context_packet_seals_engineer_and_reviewer_handoffs(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path,
        mission_id="mission-1",
        stage="research",
        scope="bounded",
        objective="Screen one candidate on public tasks.",
        acceptance_check="research/screen.json reports a binding pass/fail",
        non_goals=["do not preregister", "do not run GPU inference"],
        context_refs=[{
            "kind": "artifact",
            "ref": "research/IDEA_CANDIDATES.md",
            "why": "candidate universe",
            "content_hash": "abc",
        }],
        plan_id="plan-1",
        plan_version=1,
        node_key="screen",
    )
    checkpoint = mission.parent / "CHECKPOINT.md"
    checkpoint.write_text("# Current State\n\nScreen complete.\n", encoding="utf-8")
    engineer = record_engineer_handoff(
        mission_context_path=mission,
        round_index=1,
        engineer_summary="Created the screen packet.",
        checkpoint_path=checkpoint,
        thread_id="fresh-engineer-session",
    )
    assert engineer is not None
    latest = json.loads((mission.parent / "latest.json").read_text())
    mission_payload = json.loads(mission.read_text())
    engineer_payload = json.loads(engineer.read_text())
    assert latest["kind"] == "handoff_ref"
    assert latest["handoff"]["path"] == str(engineer)
    assert latest["handoff"]["sha256"]
    assert latest["mission"]["path"] == str(mission)
    assert mission_payload["stage"] == "research"
    assert mission_payload["scope"] == "bounded"
    assert mission_payload["objective"] == "Screen one candidate on public tasks."
    assert mission_payload["acceptance_check"].endswith("binding pass/fail")
    assert mission_payload["non_goals"] == [
        "do not preregister",
        "do not run GPU inference",
    ]
    assert mission_payload["context_refs"][0]["ref"] == "research/IDEA_CANDIDATES.md"
    assert not {
        "stage", "scope", "objective", "acceptance_check", "non_goals", "context_refs"
    } & latest.keys()
    assert engineer_payload["checkpoint"]["sha256"]
    assert "control" not in engineer_payload
    assert "text" not in engineer_payload["checkpoint"]
    assert "engineer_summary" not in engineer_payload

    reviewed = record_reviewed_handoff(
        mission_context_path=mission,
        round_index=1,
        engineer_summary="Created the screen packet.",
        review=SimpleNamespace(
            status="done",
            reason="Artifact verified.",
            next_action="Planner may choose the next frontier.",
            operator_question="",
        ),
        checkpoint_path=checkpoint,
    )
    assert reviewed is not None
    latest = json.loads((mission.parent / "latest.json").read_text())
    reviewed_payload = json.loads(reviewed.read_text())
    assert latest["kind"] == "handoff_ref"
    assert latest["mission"] == {"path": str(mission)}
    assert latest["handoff"]["path"] == str(reviewed)
    assert reviewed_payload["review"]["status"] == "done"
    assert set(reviewed_payload["review"]) == {
        "status",
        "reason",
        "next_action",
        "operator_question",
    }
    assert "engineer_summary" not in reviewed_payload
    assert "text" not in reviewed_payload["checkpoint"]
