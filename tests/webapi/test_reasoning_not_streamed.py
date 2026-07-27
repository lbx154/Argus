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

from argus_skill.webapi.server import tail_events

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


def test_an_operator_who_asks_for_it_still_gets_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SHOW_REASONING", "1")

    rows = _replay(_life_dir(tmp_path))

    assert len(_reasoning(rows)) == 2


def test_the_scratchpad_is_still_written_to_the_authoritative_log(
    tmp_path: Path, monkeypatch
) -> None:
    """Hiding it from the UI must not cost us the ability to debug with it."""
    monkeypatch.delenv("ARGUS_SKILL_SHOW_REASONING", raising=False)
    life = _life_dir(tmp_path)

    _replay(life)

    persisted = [
        json.loads(line)
        for line in (life / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(_reasoning(persisted)) == 2
