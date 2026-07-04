"""Tests for the flow-conservation probe (approach C · Phase-1 OBSERVE).

Offline + deterministic: event history is a real on-disk :class:`LifeMemory`
under ``tmp_path``; events are hand-written events.jsonl rows. No LLM, no
network.

The flagship test catches a dead self-evolve wire:
N ``self_evolve.missing_tool_advisory`` produced, 0 ``skill.created`` consumed.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.memory import JournalEntry, LifeMemory
from argus_skill.life.self_experiment import (
    GAP_KIND,
    ConservationProbe,
    FlowInvariant,
    GapFinding,
    maybe_journal_gap_advisory,
    read_events_jsonl,
    resolve_events_dir,
    run_probe,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _memory(tmp_path: Path) -> LifeMemory:
    return LifeMemory.open(tmp_path / "life")


def _append_lessons(
    mem: LifeMemory,
    n: int,
    *,
    kind: str = "self_evolve.missing_tool_advisory",
) -> None:
    _write_events(
        mem,
        [
            {
                "type": kind,
                "title": f"signal {i}",
                "summary": f"self-evolve signal number {i}",
                "ts": float(i),
            }
            for i in range(n)
        ],
    )


def _write_events(mem: LifeMemory, events: list[dict]) -> None:
    path = Path(mem.root) / "events.jsonl"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    addition = "\n".join(json.dumps(e) for e in events)
    if addition:
        addition += "\n"
    path.write_text(existing + addition, encoding="utf-8")


def _gap_entries(mem: LifeMemory) -> list[JournalEntry]:
    return [e for e in mem.journal.all() if e.kind == GAP_KIND]


# --------------------------------------------------------------------------
# 1. flagship regression — the real bug must be detectable + non-fabricable
# --------------------------------------------------------------------------
def test_flagship_missing_tool_dead_wire(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 7)  # 7 producers
    # events with NO skill.created — the wire is dead
    _write_events(mem, [{"type": "round.start", "ts": 1.0}, {"type": "loop.done", "ts": 2.0}])

    findings = run_probe(mem)

    names = {f.invariant_name for f in findings}
    assert names == {"missing_tool_to_skill"}
    (f,) = findings
    assert f.producer_count == 7
    assert f.consumer_count == 0
    assert len(f.sample_sites) == 3  # capped sample


# --------------------------------------------------------------------------
# 2. consumer alive — a live wire must NOT be flagged
# --------------------------------------------------------------------------
def test_consumer_alive_no_finding(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 9)
    _write_events(
        mem,
        [
            {"type": "skill.created", "ts": 1.0},
            {"type": "skill.created", "ts": 2.0},
            {"type": "skill.created", "ts": 3.0},
        ],
    )

    findings = run_probe(mem)

    assert [f.invariant_name for f in findings] == []


# --------------------------------------------------------------------------
# 3. noise floor — below min_producer, stay silent (no early-run false alarm)
# --------------------------------------------------------------------------
def test_noise_floor_below_min_producer(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 2)  # < min_producer (3)
    _write_events(mem, [])

    assert run_probe(mem) == []


# --------------------------------------------------------------------------
# 4. both invariants can fire independently
# --------------------------------------------------------------------------
def test_missing_tool_invariant_fires(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 4, kind="self_evolve.missing_tool_advisory")
    _write_events(mem, [{"type": "loop.done", "ts": 1.0}])

    findings = run_probe(mem)

    assert {f.invariant_name for f in findings} == {"missing_tool_to_skill"}
    (f,) = findings
    assert f.producer_count == 4


# --------------------------------------------------------------------------
# 5. scan is pure — no writes as a side effect
# --------------------------------------------------------------------------
def test_scan_is_pure(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 5)
    before = len(mem.journal.all())

    ConservationProbe().scan(mem.journal.all(), [])

    assert len(mem.journal.all()) == before  # scan wrote nothing


# --------------------------------------------------------------------------
# 6. surfacing writes an advisory event with the right shape
# --------------------------------------------------------------------------
def test_maybe_journal_writes_advisory(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 6)
    _write_events(mem, [])
    findings = run_probe(mem)
    written = maybe_journal_gap_advisory(mem, findings)

    assert written == ["missing_tool_to_skill"]
    entries = _gap_entries(mem)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == GAP_KIND
    assert "invariant:missing_tool_to_skill" in entry.tags
    assert entry.extra["producer_count"] == 6
    assert entry.extra["consumer_count"] == 0
    assert entry.extra["sample_sites"]


# --------------------------------------------------------------------------
# 7. dedup — a standing gap is not re-surfaced every epoch
# --------------------------------------------------------------------------
def test_dedup_across_calls(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 6)
    _write_events(mem, [])

    first = maybe_journal_gap_advisory(mem, run_probe(mem))
    second = maybe_journal_gap_advisory(mem, run_probe(mem))

    assert first == ["missing_tool_to_skill"]
    assert second == []  # already surfaced within the recent window
    assert len(_gap_entries(mem)) == 1  # exactly one advisory persisted


# --------------------------------------------------------------------------
# 8. fail-soft — malformed / missing events.jsonl never raises
# --------------------------------------------------------------------------
def test_read_events_skips_bad_lines(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    path = Path(mem.root) / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "skill.created", "ts": 1.0}),
                "{ this is not json",
                "",
                "42",  # valid json but not a dict — must be skipped
                json.dumps({"type": "loop.done", "ts": 2.0}),
            ]
        ),
        encoding="utf-8",
    )

    rows = read_events_jsonl(mem.root)

    assert [r["type"] for r in rows] == ["skill.created", "loop.done"]


def test_missing_events_file_is_empty(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    # no events.jsonl written at all
    assert read_events_jsonl(mem.root) == []
    # run_probe with 5 producers + no events file => dead wire, no raise
    _append_lessons(mem, 5)
    findings = run_probe(mem)
    assert [f.invariant_name for f in findings] == ["missing_tool_to_skill"]


# --------------------------------------------------------------------------
# 9. custom invariant registry (extensibility)
# --------------------------------------------------------------------------
def test_custom_invariant_events_to_events() -> None:
    inv = FlowInvariant(
        name="capture_to_outcome",
        producer_kind="self_repair.captured",
        producer_source="events",
        consumer_kind="self_evolve.landed",
        consumer_source="events",
        min_producer=2,
    )
    events = [
        {"type": "self_repair.captured", "ts": 1.0},
        {"type": "self_repair.captured", "ts": 2.0},
        {"type": "self_repair.captured", "ts": 3.0},
    ]
    findings = ConservationProbe([inv]).scan([], events)
    assert findings == [
        GapFinding(
            invariant_name="capture_to_outcome",
            producer_count=3,
            consumer_count=0,
            sample_sites=findings[0].sample_sites,
        )
    ]
    assert len(findings[0].sample_sites) == 3


# --------------------------------------------------------------------------
# 10. split-memory correctness — events.jsonl lives at PROJECT root, not the
#     global root. Reading the wrong dir would miss skill.created and falsely
#     flag a LIVE wire dead. resolve_events_dir must prefer project_root.
# --------------------------------------------------------------------------
class _FakeBundle:
    """Mimics MemoryBundle: .root is GLOBAL, events live under project_root."""

    def __init__(self, journal_obj, global_root: Path, project_root: Path) -> None:
        self.journal = journal_obj
        self.root = global_root
        self.project_root = project_root


def test_resolve_events_dir_prefers_project_root(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    project_root = tmp_path / "project"
    global_root.mkdir()
    project_root.mkdir()
    bundle = _FakeBundle(journal_obj=None, global_root=global_root, project_root=project_root)
    assert resolve_events_dir(bundle) == project_root


def test_run_probe_reads_events_from_project_root(tmp_path: Path) -> None:
    # journal (producers) is a real LifeMemory; events.jsonl is written to a
    # SEPARATE project root, while the bundle's .root points elsewhere (global).
    proj = _memory(tmp_path / "proj")
    _append_lessons(proj, 5)
    # live consumer at PROJECT root
    _write_events(proj, [{"type": "skill.created", "ts": 1.0}])
    global_root = tmp_path / "global"
    global_root.mkdir()  # empty — a naive reader here would see 0 skill.created

    bundle = _FakeBundle(
        journal_obj=proj.journal, global_root=global_root, project_root=proj.root
    )
    # wire is ALIVE (skill.created present at project root) => no finding
    assert run_probe(bundle) == []
