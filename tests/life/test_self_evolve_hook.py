"""Tests for the self-evolve hook in LifeSupervisor._maybe_enqueue_mint_skill.

We can't easily spin a full SupervisedEngineer in a unit test (it'd need
a real codex backend), so the tests exercise the helper directly with a
fake supervisor stub. The helper does pure plumbing — read mission
result + events.jsonl, run detector, dedup vs backlog, append BacklogItem
— so a unit test that asserts BacklogItem appears in memory.backlog is
sufficient. End-to-end smoke (real daemon → real mint mission) is
covered by manual daemon runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Build a minimal supervisor stub that uses the real method
# ---------------------------------------------------------------------------


def _make_stub_supervisor(tmp_path: Path):
    """Build a LifeSupervisor with only what _maybe_enqueue_mint_skill needs.

    The helper touches: self.memory.backlog (add + all), self.memory.journal
    (append), self._inject_cumulative_cost (logging), and reads
    <memory.root>/events.jsonl.
    """
    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeSupervisor

    mem = LifeMemory.open(tmp_path)
    mem.init()

    # Build the bare-minimum supervisor instance.  We avoid the full
    # LifeSupervisor(__init__) machinery (which wants a real runner)
    # by constructing the object via __new__ and bolting on just what
    # the hook reads.
    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.memory = mem
    sup._inject_cumulative_cost = lambda entry: None  # noop for tests
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
# Happy path: mission produced a missing-tool signal → enqueued
# ---------------------------------------------------------------------------


def test_hook_enqueues_mint_skill_for_command_not_found(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": [
            "I tried to convert the PDF.\n"
            "/bin/bash: line 1: pdftotext: command not found\n"
        ],
    }
    enqueued = sup._maybe_enqueue_mint_skill(item, result)
    assert "pdftotext" in enqueued

    # BacklogItem now exists with mint-skill tag.
    minted = [
        i for i in mem.backlog.all()
        if "mint-skill" in (i.tags or [])
    ]
    assert len(minted) == 1
    assert minted[0].title == "mint-skill: pdftotext"
    assert "Mint a skill" in minted[0].objective
    assert minted[0].priority == 50  # higher than default 100


def test_hook_enqueues_for_module_not_found(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": [
            "Traceback (most recent call last):\n"
            "  File ...\n"
            "ModuleNotFoundError: No module named 'pdfplumber'\n"
        ],
    }
    sup._maybe_enqueue_mint_skill(item, result)

    minted = [
        i for i in mem.backlog.all()
        if "mint-skill" in (i.tags or [])
    ]
    assert len(minted) == 1
    assert "pdfplumber" in minted[0].title


# ---------------------------------------------------------------------------
# Dedup: in-flight mint-skill mission for same tool not re-enqueued
# ---------------------------------------------------------------------------


def test_hook_dedups_against_inflight_mint_skill(tmp_path: Path) -> None:
    from argus_skill.life.memory import BacklogItem

    sup, mem = _make_stub_supervisor(tmp_path)
    # Pre-seed an in-flight mint-skill mission for pdftotext.
    mem.backlog.add(BacklogItem.new(
        title="mint-skill: pdftotext",
        objective="(in flight)",
        tags=["mint-skill"],
    ))

    item = _make_backlog_item()
    result = {
        "agent_messages": [
            "/bin/bash: line 1: pdftotext: command not found\n"
        ],
    }
    enqueued = sup._maybe_enqueue_mint_skill(item, result)
    assert enqueued == []

    # Only the originally seeded item should remain (no duplicate).
    minted = [
        i for i in mem.backlog.all()
        if "mint-skill" in (i.tags or [])
    ]
    assert len(minted) == 1


# ---------------------------------------------------------------------------
# Anti-recursion: mint-skill missions don't recursively enqueue
# ---------------------------------------------------------------------------


def test_hook_does_not_recurse_on_mint_skill_missions(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    # The "source" item IS a mint-skill mission. Its own trajectory
    # might include 'command not found' (because the candidate script
    # crashes during minting), but we MUST NOT enqueue a meta-mint-skill.
    item = _make_backlog_item(
        title="mint-skill: pdftotext",
        tags=["mint-skill", "self-evolve"],
    )
    result = {
        "agent_messages": [
            "ModuleNotFoundError: No module named 'pdfplumber'\n"
        ],
    }
    enqueued = sup._maybe_enqueue_mint_skill(item, result)
    assert enqueued == []

    minted = [
        i for i in mem.backlog.all()
        if "mint-skill" in (i.tags or [])
    ]
    assert minted == []


# ---------------------------------------------------------------------------
# No signal → nothing enqueued
# ---------------------------------------------------------------------------


def test_hook_noop_when_no_missing_tool(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": ["Everything worked fine. Tests pass."],
    }
    enqueued = sup._maybe_enqueue_mint_skill(item, result)
    assert enqueued == []
    assert mem.backlog.all() == []


def test_hook_noop_on_empty_result(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    enqueued = sup._maybe_enqueue_mint_skill(item, None)
    assert enqueued == []


# ---------------------------------------------------------------------------
# Events.jsonl ingestion (for command_execution events the runner emits)
# ---------------------------------------------------------------------------


def test_hook_reads_events_jsonl_for_exit_code_127(tmp_path: Path) -> None:
    import time as _time

    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    # Event ts must be >= item.ts (the helper filters to events that
    # happened after the mission started). Stamp the event in the
    # future so it always passes the filter regardless of test clock.
    event_ts = _time.time() + 60.0
    events_path = Path(mem.root) / "events.jsonl"
    events_path.write_text(
        "\n".join([
            json.dumps({"type": "engineer.progress",
                        "kind": "command_execution",
                        "text": "/bin/bash -lc \"ocrmypdf in.pdf out.pdf\"",
                        "exit_code": 127,
                        "output_excerpt": "/bin/bash: ocrmypdf: command not found",
                        "ts": event_ts}),
        ]) + "\n",
        encoding="utf-8",
    )
    # The result dict itself has no signal — only events.jsonl does.
    enqueued = sup._maybe_enqueue_mint_skill(item, {})
    # Detector picks up "ocrmypdf" from the bash error AND from the
    # exit_code=127 synthetic signal; dedup keeps one slug.
    assert "ocrmypdf" in enqueued


# ---------------------------------------------------------------------------
# Multiple distinct missing tools in one mission → multiple enqueues
# ---------------------------------------------------------------------------


def test_hook_enqueues_one_per_distinct_tool(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {
        "agent_messages": [
            "ModuleNotFoundError: No module named 'wandb'\n"
            "/bin/bash: line 2: convert: command not found\n"
        ],
    }
    enqueued = sup._maybe_enqueue_mint_skill(item, result)
    assert set(enqueued) == {"wandb", "convert"}

    minted_titles = sorted(
        i.title for i in mem.backlog.all()
        if "mint-skill" in (i.tags or [])
    )
    assert minted_titles == ["mint-skill: convert", "mint-skill: wandb"]


# ---------------------------------------------------------------------------
# Journal entry recorded when enqueue happens
# ---------------------------------------------------------------------------


def test_hook_journals_self_evolve_event(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _make_backlog_item()
    result = {"agent_messages": ["ModuleNotFoundError: No module named 'foo'\n"]}
    sup._maybe_enqueue_mint_skill(item, result)

    journal_kinds = [e.kind for e in mem.journal.all()]
    assert "self_evolve.mint_enqueued" in journal_kinds
