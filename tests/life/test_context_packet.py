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
    assert latest["kind"] == "round_engineer_handoff"
    assert latest["stage"] == "research"
    assert latest["scope"] == "bounded"
    assert latest["objective"] == "Screen one candidate on public tasks."
    assert latest["acceptance_check"].endswith("binding pass/fail")
    assert latest["non_goals"] == ["do not preregister", "do not run GPU inference"]
    assert latest["context_refs"][0]["ref"] == "research/IDEA_CANDIDATES.md"
    assert latest["mission"]["path"] == str(mission)
    assert latest["checkpoint"]["sha256"]
    assert "text" not in latest["checkpoint"]
    assert "engineer_summary" not in latest

    reviewed = record_reviewed_handoff(
        mission_context_path=mission,
        round_index=1,
        engineer_summary="Created the screen packet.",
        review=SimpleNamespace(
            status="done",
            reason="Artifact verified.",
            next_action="Planner may choose the next frontier.",
            progress_class="decision",
            failure_cause="",
            failure_layer="",
            planner_report={
                "forward_progress": True,
                "plan_signal": "continue",
                "evidence_files": [{"path": "research/screen.json"}],
                "recommended_next": "legacy duplicate",
            },
            harness_control={"stage_reconciliation_required": True},
        ),
        checkpoint_path=checkpoint,
    )
    assert reviewed is not None
    latest = json.loads((mission.parent / "latest.json").read_text())
    assert latest["kind"] == "round_reviewed_handoff"
    assert latest["objective"] == "Screen one candidate on public tasks."
    assert latest["acceptance_check"].endswith("binding pass/fail")
    assert latest["scope"] == "bounded"
    assert latest["review"]["status"] == "done"
    assert latest["review"]["planner_report"]["plan_signal"] == "continue"
    assert "recommended_next" not in latest["review"]["planner_report"]
    assert latest["review"]["harness_control"] == {
        "stage_reconciliation_required": True
    }
    assert "engineer_summary" not in latest
    assert "text" not in latest["checkpoint"]
