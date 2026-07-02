"""The missing process_lesson → skill wire: recurring reviewer lessons get
synthesized into a candidate skill and routed through the EXISTING gate.

This is the fix for the observed self-evolution gap: argus produced 9 genuine
process_lessons over a 2-day run but distilled 0 skills, because the reviewer's
process_lesson channel was journaled + shown to the Planner but never fed to
skill creation. These tests pin that the wire now collects lessons, synthesizes,
and hands candidates to the gate — and no-ops safely when there's nothing to do.
"""
from __future__ import annotations

import tempfile

from argus_skill.life import MemoryBundle
from argus_skill.life.memory import JournalEntry
from argus_skill.skills.lesson_distill import (
    collect_process_lessons,
    distill_process_lessons,
    synthesize_skills,
)

_SKILL_MD = (
    "# Neutralize ambient mission env in state-creating tests\n"
    "Applies when a test writes temporary project state that argus reads back.\n"
    "## When to use\n- a test creates PIPELINE_STATE / markers under a tmp dir\n"
    "## When NOT to use\n- pure unit tests with no ambient env\n"
    "## How to solve\n- clear ARGUS_SKILL_* mission vars before the test writes state\n"
)


class _FakeRunner:
    def __init__(self, message: str) -> None:
        self._m = message

    def run_exec(self, *, prompt, options, run_label, resume_thread_id):  # noqa: ANN001
        class _R:
            last_agent_message = self._m
        return _R()


class _RecordingRouter:
    def __init__(self) -> None:
        self.applied: list[dict] = []

    def apply_ops(self, ops, *, task, on_event=None):  # noqa: ANN001
        self.applied.extend(ops)
        return {"created": len(ops), "rejected": 0}


def _journal_with_lessons(lessons: list[str]):
    m = MemoryBundle.for_cwd(tempfile.mkdtemp())
    m.init()
    for les in lessons:
        m.project.memory.append(JournalEntry.new(
            kind="self_evolve.process_lesson", title="process lesson",
            summary=les, tags=["self_evolve"], extra={"lesson": les}))
    return m.project.memory


def test_collect_process_lessons_dedups_newest_first() -> None:
    j = _journal_with_lessons(["measure before claiming", "measure before claiming", "freeze the floor"])
    got = collect_process_lessons(j)
    assert got == ["freeze the floor", "measure before claiming"]


def test_synthesize_parses_skill_blocks() -> None:
    r = _FakeRunner(_SKILL_MD)
    out = synthesize_skills(r, ["clear ambient env in tests"], max_skills=3)
    assert len(out) == 1 and out[0].startswith("# Neutralize")


def test_synthesize_none_when_nothing_generalizable() -> None:
    assert synthesize_skills(_FakeRunner("NONE"), ["x"]) == []
    assert synthesize_skills(_FakeRunner(""), ["x"]) == []


def test_synthesize_splits_multiple_skills() -> None:
    two = _SKILL_MD + "\n=====SKILL=====\n# Second skill\ndesc\n## When to use\n- y\n## How to solve\n- z\n"
    out = synthesize_skills(_FakeRunner(two), ["a", "b"], max_skills=3)
    assert len(out) == 2


def test_distill_routes_candidates_through_the_gate() -> None:
    # THE WIRE end to end (with a real journal + fake synth + recording router).
    j = _journal_with_lessons([
        "clear ambient ARGUS_SKILL_* env in state-creating tests",
        "put the path-confinement guard at the first trust boundary",
        "record pre-fix escape path and post-fix no-escape proof",
    ])
    router = _RecordingRouter()
    res = distill_process_lessons(
        journal=j, router=router, synth_runner=_FakeRunner(_SKILL_MD),
        min_lessons=3, max_skills=3,
    )
    assert res["created"] == 1                       # the gate was handed 1 candidate
    assert res["lessons"] == 3 and res["candidates"] == 1
    assert router.applied and router.applied[0]["op"] == "create"
    assert "How to solve" in router.applied[0]["content"]


def test_distill_noop_when_too_few_lessons() -> None:
    j = _journal_with_lessons(["only one lesson"])
    router = _RecordingRouter()
    res = distill_process_lessons(journal=j, router=router, synth_runner=_FakeRunner(_SKILL_MD), min_lessons=3)
    assert res["created"] == 0 and res["reason"] == "insufficient lessons"
    assert router.applied == []                      # gate never touched


def test_distill_noop_when_nothing_generalizable() -> None:
    j = _journal_with_lessons(["a", "b", "c", "d"])
    router = _RecordingRouter()
    res = distill_process_lessons(journal=j, router=router, synth_runner=_FakeRunner("NONE"), min_lessons=3)
    assert res["created"] == 0 and res["reason"] == "nothing generalizable"
    assert router.applied == []


def test_distill_fail_soft_on_router_error() -> None:
    class _BoomRouter:
        def apply_ops(self, ops, *, task, on_event=None):  # noqa: ANN001
            raise RuntimeError("gate down")
    j = _journal_with_lessons(["a", "b", "c"])
    res = distill_process_lessons(journal=j, router=_BoomRouter(), synth_runner=_FakeRunner(_SKILL_MD), min_lessons=3)
    assert res["created"] == 0 and res["reason"].startswith("error")
