"""Tests for the live dead-wire ledger (C Phase-2 · close-the-loop).

Offline + deterministic. Proves the pure ``render_open_gaps_block`` surfaces
CURRENTLY-open dead wires with the triage mandate, self-clears the instant a
consumer fires, and that ``run_probe`` stays fail-soft / kill-switchable.
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.life.memory import JournalEntry, LifeMemory
from argus_skill.life.self_experiment import (
    ConservationProbe,
    GapFinding,
    render_open_gaps_block,
    run_probe,
)

# Reuse the exact helpers from the probe test module (same offline shape).
from tests.life.test_self_experiment import (  # noqa: E402
    _append_lessons,
    _memory,
    _write_events,
)


# --------------------------------------------------------------------------
# 1. empty in => empty out (healthy path: planner prompt byte-for-byte unchanged)
# --------------------------------------------------------------------------
def test_render_empty_when_no_findings() -> None:
    assert render_open_gaps_block([]) == ""
    assert render_open_gaps_block(None) == ""  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 2. a finding renders counts + wire + samples + the triage mandate framing
# --------------------------------------------------------------------------
def test_render_contains_counts_and_mandate() -> None:
    finding = GapFinding(
        invariant_name="process_lesson_to_skill",
        producer_count=7,
        consumer_count=0,
        sample_sites=["journal:self_evolve.process_lesson:a1"],
    )
    block = render_open_gaps_block([finding])
    # the wire identity + counts + sample
    assert "process_lesson_to_skill" in block
    assert "self_evolve.process_lesson" in block
    assert "skill.created" in block
    assert "7x" in block and "0x" in block
    assert "journal:self_evolve.process_lesson:a1" in block
    # COUNT-framing + triage mandate + stage-gate subordination (non-negotiable)
    assert "COUNT ONLY" in block
    assert "defer/reject" in block
    assert "do NOT silently drop" in block
    assert "rule 0" in block


# --------------------------------------------------------------------------
# 3. scan -> render integration (a real dead wire surfaces)
# --------------------------------------------------------------------------
def test_scan_to_render_surfaces_dead_wire() -> None:
    journal = [{"kind": "self_evolve.process_lesson"} for _ in range(7)]
    findings = ConservationProbe().scan(journal, [])
    assert [f.invariant_name for f in findings] == ["process_lesson_to_skill"]
    block = render_open_gaps_block(findings)
    assert "process_lesson_to_skill" in block


# --------------------------------------------------------------------------
# 4. CLOSE-THE-LOOP: consumer fires => scan empty => ledger self-clears
# --------------------------------------------------------------------------
def test_ledger_self_clears_when_consumer_fires() -> None:
    journal = [{"kind": "self_evolve.process_lesson"} for _ in range(7)]
    events = [{"type": "skill.created"}]  # the wire is now ALIVE
    findings = ConservationProbe().scan(journal, events)
    assert findings == []
    assert render_open_gaps_block(findings) == ""


# --------------------------------------------------------------------------
# 5. run_probe end-to-end (real LifeMemory journal + on-disk events) -> render
# --------------------------------------------------------------------------
def test_run_probe_then_render_end_to_end(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    _append_lessons(mem, 6)
    _write_events(mem, [])  # no skill.created => dead wire
    block = render_open_gaps_block(run_probe(mem))
    assert "process_lesson_to_skill" in block
    assert "6x" in block


# --------------------------------------------------------------------------
# 6. fail-soft: a journal whose all() raises => run_probe [] => render ""
# --------------------------------------------------------------------------
def test_run_probe_fail_soft_on_broken_journal() -> None:
    class _BrokenJournal:
        def all(self):
            raise RuntimeError("disk gone")

    class _Mem:
        journal = _BrokenJournal()
        project_root = None
        root = None

    assert run_probe(_Mem()) == []
    assert render_open_gaps_block(run_probe(_Mem())) == ""


# --------------------------------------------------------------------------
# 7. kill-switch env knob disables the probe cleanly (delegate-side contract)
# --------------------------------------------------------------------------
def test_probe_kill_switch(monkeypatch) -> None:
    from argus_skill.life.supervisor._core import _flow_conservation_probe_enabled

    monkeypatch.setenv("ARGUS_SKILL_FLOW_CONSERVATION_PROBE", "0")
    assert _flow_conservation_probe_enabled() is False
    monkeypatch.setenv("ARGUS_SKILL_FLOW_CONSERVATION_PROBE", "1")
    assert _flow_conservation_probe_enabled() is True


# --------------------------------------------------------------------------
# 8. unknown invariant name renders without the wire bracket (no crash)
# --------------------------------------------------------------------------
def test_render_unknown_invariant_drops_bracket() -> None:
    finding = GapFinding(
        invariant_name="not_in_registry",
        producer_count=5,
        consumer_count=0,
        sample_sites=[],
    )
    block = render_open_gaps_block([finding])
    assert "not_in_registry" in block
    assert "->" not in block.split("not_in_registry", 1)[1].split("\n", 1)[0]
    assert "sample producers: (none)" in block


# keep the imported test symbols referenced so linters see the reuse
_ = (JournalEntry, LifeMemory)
