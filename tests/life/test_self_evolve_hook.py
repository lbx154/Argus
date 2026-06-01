"""Tests for the self-evolve advisory hook in
LifeSupervisor._maybe_journal_self_evolve_advisory.

Post-redesign (skill 04 boundary fix): the harness no longer
auto-enqueues mint-skill BacklogItems. It writes journal advisories
of kind ``self_evolve.missing_tool_advisory``; the reviewer / planner
agent decides whether to act on them. The detector is structural
(harness plumbing); the mint decision is judgment (agent's call).

Mirrors how F3 mediocrity_finding surfaces facts to the reviewer
without ruling.
"""
from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any

import pytest


def _make_stub_supervisor(tmp_path: Path):
    """Build a LifeSupervisor instance with only what the advisory
    hook needs. Avoids the full __init__ machinery."""
    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeSupervisor

    mem = LifeMemory.open(tmp_path)
    mem.init()

    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.memory = mem
    sup._inject_cumulative_cost = lambda entry: None
    sup._emit_status = lambda *a, **kw: None
    sup._MINT_SKILL_TAG = LifeSupervisor._MINT_SKILL_TAG
    return sup, mem


def _make_backlog_item(title: str = "test-mission", tags: list[str] | None = None):
    from argus_skill.life.memory import BacklogItem
    return BacklogItem.new(
        title=title,
        objective="some research work",
        tags=list(tags or []),
    )


# ---------------------------------------------------------------------------
# Happy path: missing tool → advisory journal entry (NOT a BacklogItem)
# ---------------------------------------------------------------------------


def test_hook_writes_advisory_for_command_not_found(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": [
            "/bin/bash: line 1: pdftotext: command not found\n"
        ],
    }
    surfaced = sup._maybe_journal_self_evolve_advisory(item, result)
    assert "pdftotext" in surfaced

    # Journal got an advisory entry
    journal_kinds = [e.kind for e in mem.journal.all()]
    assert "self_evolve.missing_tool_advisory" in journal_kinds

    # CRITICAL: NO BacklogItem was enqueued (judgment moved to agent)
    backlog = mem.backlog.all()
    assert not any(
        "mint-skill" in (i.tags or []) for i in backlog
    ), "harness must not auto-enqueue mint-skill missions"


def test_hook_writes_advisory_for_module_not_found(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": [
            "ModuleNotFoundError: No module named 'pdfplumber'\n"
        ],
    }
    surfaced = sup._maybe_journal_self_evolve_advisory(item, result)
    assert "pdfplumber" in surfaced

    advisories = [
        e for e in mem.journal.all()
        if e.kind == "self_evolve.missing_tool_advisory"
    ]
    assert len(advisories) == 1
    assert "pdfplumber" in advisories[0].title
    # Tags include tool:<name> so the dedup helper can find it.
    assert "tool:pdfplumber" in advisories[0].tags


# ---------------------------------------------------------------------------
# Dedup: same tool already advised in journal → not re-surfaced this tick
# ---------------------------------------------------------------------------


def test_hook_dedups_against_recent_advisory(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item1 = _make_backlog_item(title="mission-1")

    # First tick surfaces the advisory
    sup._maybe_journal_self_evolve_advisory(
        item1, {"agent_messages": ["No such cmd: pdftotext: command not found"]}
    )

    # Second tick with same signal → NOT re-surfaced
    item2 = _make_backlog_item(title="mission-2")
    surfaced = sup._maybe_journal_self_evolve_advisory(
        item2, {"agent_messages": ["Tried again: pdftotext: command not found"]}
    )
    assert surfaced == []

    # Only one advisory total
    advisories = [
        e for e in mem.journal.all()
        if e.kind == "self_evolve.missing_tool_advisory"
    ]
    assert len(advisories) == 1


# ---------------------------------------------------------------------------
# Anti-recursion: mint-skill missions don't surface their own missing tools
# ---------------------------------------------------------------------------


def test_hook_does_not_surface_from_mint_skill_missions(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    # The "source" item IS a mint-skill mission. Its trajectory likely
    # includes 'command not found' (because the candidate script
    # crashes during minting) — we MUST NOT surface those as new
    # advisories (would create infinite reflection on minting itself).
    item = _make_backlog_item(
        title="mint-skill: pdftotext",
        tags=["mint-skill", "self-evolve"],
    )
    surfaced = sup._maybe_journal_self_evolve_advisory(
        item,
        {"agent_messages": ["ModuleNotFoundError: No module named 'pdfplumber'"]},
    )
    assert surfaced == []

    advisories = [
        e for e in mem.journal.all()
        if e.kind == "self_evolve.missing_tool_advisory"
    ]
    assert advisories == []


# ---------------------------------------------------------------------------
# No signal → no advisory
# ---------------------------------------------------------------------------


def test_hook_noop_when_no_missing_tool(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": ["Everything worked fine. Tests pass."],
    }
    surfaced = sup._maybe_journal_self_evolve_advisory(item, result)
    assert surfaced == []
    # No advisory journal entry and no BacklogItem either.
    assert all(
        e.kind != "self_evolve.missing_tool_advisory"
        for e in mem.journal.all()
    )
    assert mem.backlog.all() == []


def test_hook_noop_on_empty_result(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    surfaced = sup._maybe_journal_self_evolve_advisory(item, None)
    assert surfaced == []


# ---------------------------------------------------------------------------
# Events.jsonl: exit_code 127 → surfaces from events
# ---------------------------------------------------------------------------


def test_hook_reads_events_jsonl_for_exit_code_127(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    # Event ts must be >= item.ts; use future ts so test is robust.
    event_ts = _time.time() + 60.0
    events_path = Path(mem.root) / "events.jsonl"
    events_path.write_text(
        json.dumps({
            "type": "engineer.progress",
            "kind": "command_execution",
            "text": "/bin/bash -lc \"ocrmypdf in.pdf out.pdf\"",
            "exit_code": 127,
            "output_excerpt": "/bin/bash: ocrmypdf: command not found",
            "ts": event_ts,
        }) + "\n",
        encoding="utf-8",
    )
    surfaced = sup._maybe_journal_self_evolve_advisory(item, {})
    assert "ocrmypdf" in surfaced


# ---------------------------------------------------------------------------
# Multiple distinct missing tools → multiple advisories
# ---------------------------------------------------------------------------


def test_hook_surfaces_one_advisory_per_distinct_tool(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": [
            "ModuleNotFoundError: No module named 'wandb'\n"
            "/bin/bash: line 2: convert: command not found\n"
        ],
    }
    surfaced = sup._maybe_journal_self_evolve_advisory(item, result)
    assert set(surfaced) == {"wandb", "convert"}

    advisories = [
        e for e in mem.journal.all()
        if e.kind == "self_evolve.missing_tool_advisory"
    ]
    titles = sorted(e.title for e in advisories)
    assert titles == ["missing tool: convert", "missing tool: wandb"]


# ---------------------------------------------------------------------------
# Anti-regression: old enqueue API must stay gone
# ---------------------------------------------------------------------------


def test_old_enqueue_api_is_gone() -> None:
    """The pre-skill-04 design auto-enqueued BacklogItems from harness.
    That's a judgment harness shouldn't make. Lock in the demoted API
    so a future "convenient" re-introduction trips this test.
    """
    from argus_skill.life.supervisor import LifeSupervisor
    forbidden = (
        "_maybe_enqueue_mint_skill",
        "_enqueue_mint_skill_item",
        "_inflight_mint_skill_tools",
    )
    for name in forbidden:
        assert not hasattr(LifeSupervisor, name), (
            f"{name} is the old auto-enqueue API; harness must only "
            f"surface advisories. Reviewer / planner agent decides "
            f"whether to enqueue mint-skill missions."
        )
