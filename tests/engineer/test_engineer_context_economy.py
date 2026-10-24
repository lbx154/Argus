"""The Engineer's per-step context: what each tool step re-sends.

An agent CLI re-sends the whole session context on every tool step. These
tests pin what Argus controls about that baseline without depending on what
an earlier turn of the session held: the CLI tool surface it asks for and
copies of the same text within one prompt.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.life.context_packet import render_mission_brief

# ── CLI tool surface ─────────────────────────────────────────────────────────


# ── Skill discovery ──────────────────────────────────────────────────────────


# ── Copies of the same text within one prompt ────────────────────────────────


def _mission(root: Path, *, reason: str, next_action: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mission.json").write_text(json.dumps({
        "kind": "mission_context", "mission_id": "m", "stage": "delivery",
        "objective": "ship", "execution_workdir": str(root),
    }), encoding="utf-8")
    (root / "round-0001.json").write_text(json.dumps({
        "kind": "round_reviewed_handoff", "round": 1, "created_at": 1.0,
        "engineer_summary": "Implemented the CLI.",
        "review": {"status": "continue", "reason": reason, "next_action": next_action},
    }), encoding="utf-8")
    (root / "latest.json").write_text(json.dumps({
        "kind": "handoff_ref", "path": str(root / "round-0001.json"),
    }), encoding="utf-8")
    return root / "mission.json"


def test_brief_shows_a_next_action_identical_to_the_review_reason_once(
    tmp_path: Path,
) -> None:
    text = "The header still sends a literal; send the configured token."
    brief = render_mission_brief(_mission(tmp_path / "same", reason=text, next_action=text))

    assert f"- Last review: continue: {text}" in brief
    assert brief.count(text) == 1
