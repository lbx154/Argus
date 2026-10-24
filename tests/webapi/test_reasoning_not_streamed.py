"""The inner scratchpad is persisted for debugging and not pushed to the UI.

Operator report (2026-07-26): both the TUI and the web cockpit displayed
"**Considering file edits and testing** / I'm thinking about..." even though
ARGUS_SKILL_SHOW_REASONING defaults to "0" and the web README documents the
scratchpad as hidden.

Measured rather than assumed: the events are correctly tagged `kind="reasoning"`
and cli/render.py returns "" for them at the default knob, so that renderer was
not the leak. The web stream simply never filtered them at all.

Filtering happens at the stream boundary, not at persistence. events.jsonl keeps
the full scratchpad — reading it is what made this diagnosable in the first
place — and only the UI feed drops it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from argus.webapi.server import tail_events

_LEAK = "**Considering file edits and testing**\n\nI'm thinking about editing files."


def _life_dir(tmp_path: Path) -> Path:
    rows = [
        {"type": "life.mission.started", "title": "do the thing"},
        {"type": "engineer.progress", "kind": "reasoning", "text": _LEAK},
        {"type": "engineer.progress", "kind": "agent_message", "text": "Done."},
        {"type": "engineer.progress", "kind": "reasoning", "text": "more scratchpad"},
        {"type": "round.review.completed", "status": "done"},
    ]
    (tmp_path / "events.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return tmp_path


def _replay(life_dir: Path, limit: int = 50) -> list[dict]:
    async def run() -> list[dict]:
        got: list[dict] = []
        gen = tail_events(life_dir, replay_limit=limit)
        try:
            for _ in range(limit):
                got.append(await asyncio.wait_for(gen.__anext__(), timeout=2))
        except (StopAsyncIteration, asyncio.TimeoutError):
            pass
        await gen.aclose()
        return got

    return asyncio.run(run())


def _reasoning(rows: list[dict]) -> list[dict]:
    return [r for r in rows if str(r.get("kind") or "") == "reasoning"]


@pytest.mark.parametrize("configured", [None, "0", "false", "off", ""])
def test_the_stream_hides_the_scratchpad_by_default(
    tmp_path: Path, monkeypatch, configured
) -> None:
    if configured is None:
        monkeypatch.delenv("ARGUS_SKILL_SHOW_REASONING", raising=False)
    else:
        monkeypatch.setenv("ARGUS_SKILL_SHOW_REASONING", configured)

    rows = _replay(_life_dir(tmp_path))

    assert _reasoning(rows) == []
    assert any(r.get("type") == "round.review.completed" for r in rows), (
        "filtering must drop only the scratchpad, not the rest of the stream"
    )


def test_tail_replay_removes_exact_rollover_boundary_overlap(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl.1").write_text(
        "".join(json.dumps({"type": "event", "seq": seq}) + "\n" for seq in (1, 2, 3)),
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps({"type": "event", "seq": seq}) + "\n" for seq in (2, 3, 4)),
        encoding="utf-8",
    )

    assert [row["seq"] for row in _replay(tmp_path, limit=4)] == [1, 2, 3, 4]
