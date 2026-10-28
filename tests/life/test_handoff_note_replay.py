"""Replay of s-efe9deac mission 8c23c57c9240: a hand_off note is for the next holder only.

The Engineer handed the task to the Manager with a note asking it to "supply the
scheduler-assigned GPU UUID" and to "not create an infrastructure task or reopen
planning". Settlement stored that note as the capsule's narrative; recall then
put it in front of a reflection, a Planner and two later Engineers as a "prior
mission experience". The settled record keeps the form's summary only, and the
reflection is never shown the note either.
"""
from __future__ import annotations

import json
from dataclasses import replace
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

from argus.core.handoff import HandOffActions
from argus.engineer.round_routing import _handoff_record
from argus.life import reflection
from argus.life.memory import BacklogItem, MemoryBundle
from argus.life.supervisor._mission_execution_helpers import _MissionRunState
from argus.life.supervisor._mission_execution_settlement import MissionExecutionSettlementMixin

RECORD = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "handoff_note_to_manager.json")
    .read_text(encoding="utf-8")
)
NOTE_ONLY = "do not create an infrastructure task or reopen planning"


def _settled(tmp_path: Path) -> tuple[SimpleNamespace, _MissionRunState]:
    mission, settled = RECORD["mission"], RECORD["settled"]
    actions = HandOffActions("engineer", operator_available=True)
    actions.dispatch("hand_off", RECORD["hand_off"])
    handoff = actions.handoff
    review = _handoff_record(handoff.to, handoff)
    item = replace(
        BacklogItem.new(item_id=mission["id"], title=mission["title"], objective=mission["objective"],
                        tags=mission["tags"], manager_decision={"vertical": "research"}),
        started_ts=mission["started_ts"],
    )
    packet = tmp_path / "state" / "handoffs" / mission["id"] / "mission.json"
    state = _MissionRunState(
        item=item, status=settled["status"], stop_reason=settled["stop_reason"], context_packet_path=packet,
        outcome=SimpleNamespace(
            final_message=handoff.note, summary=handoff.note, final_review_reason=review.reason,
            final_review_status=review.status, final_review_source=review.review_source,
        ),
    )
    memory = MemoryBundle.for_cwd(tmp_path / "workspace", global_root=tmp_path / "tenant", fingerprint="s-efe9deac")
    host = SimpleNamespace(
        memory=memory, runner=None, _pending_reviewed_fact=None, _emit=lambda _event: None,
        _project_workdir=lambda: tmp_path / "workspace", _budget_global_root=lambda: tmp_path / "tenant",
    )
    return host, state


def test_the_settled_capsule_keeps_the_summary_and_points_at_the_handoff(tmp_path: Path) -> None:
    host, state = _settled(tmp_path)
    assert NOTE_ONLY in state.outcome.final_message

    MissionExecutionSettlementMixin._capture_failure_experience(host, state)

    (capsule,) = host.memory.failure_experiences.recent()
    assert capsule.factual_outcome == RECORD["hand_off"]["summary"]
    assert NOTE_ONLY not in json.dumps(capsule.to_jsonable(), ensure_ascii=False)
    assert str(state.context_packet_path.parent / "latest.json") in capsule.artifact_refs
    recalled = host.memory.render_recall_context("candidate seed0 GPU UUID 训练")
    assert RECORD["hand_off"]["summary"] in recalled and NOTE_ONLY not in recalled


def test_the_reflection_prompt_never_carries_the_note(tmp_path: Path, monkeypatch) -> None:
    host, state = _settled(tmp_path)
    prompts: list[str] = []
    accepted = signature(reflection.build_reflection_prompt).parameters

    def build(**kwargs):
        prompts.append(reflection.build_reflection_prompt(
            **{key: value for key, value in kwargs.items() if key in accepted},
            vertical_root=tmp_path / "v", project_wiki=None, skills_dir=tmp_path / "skills", existing_lessons=[],
        ))
        return {"skipped": "", "created": [], "updated": []}

    monkeypatch.setattr(reflection, "reflect_after_mission", build)

    MissionExecutionSettlementMixin._reflect_after_mission(host, state)

    (prompt,) = prompts
    assert RECORD["hand_off"]["summary"] in prompt
    assert NOTE_ONLY not in prompt
