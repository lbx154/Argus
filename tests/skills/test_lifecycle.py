"""Tests for argus_skill.skills.lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from argus_skill.skills.lifecycle import (
    ACTION_DISTILL,
    ACTION_NOOP,
    ACTION_REINFORCE,
    ACTION_RETIRE,
    ACTION_REVISE,
    LifecycleOutcome,
    apply_action,
    archive_skill,
    decide_action,
)


@dataclass
class _FakeSkill:
    name: str
    path: str


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.save_distilled_return = _FakeSkill(name="freshly-baked", path="")
        self.promote_lesson_return = True

    def writeback_from_trajectory(self, **kwargs: Any) -> None:
        self.calls.append(("writeback", kwargs))

    def save_distilled(self, **kwargs: Any) -> _FakeSkill:
        self.calls.append(("save_distilled", kwargs))
        return self.save_distilled_return

    def promote_lesson(self, **kwargs: Any) -> bool:
        self.calls.append(("promote_lesson", kwargs))
        return self.promote_lesson_return


def test_decide_done_with_skill_is_reinforce() -> None:
    out = LifecycleOutcome(status="done", success=True, successful_trajectory="x")
    assert decide_action(outcome=out, has_active_skill=True) == ACTION_REINFORCE


def test_decide_done_no_skill_with_distill_is_distill() -> None:
    out = LifecycleOutcome(status="done", success=True, raw_distill_output="# new")
    assert decide_action(outcome=out, has_active_skill=False) == ACTION_DISTILL


def test_decide_done_no_skill_no_distill_is_noop() -> None:
    out = LifecycleOutcome(status="done", success=True)
    assert decide_action(outcome=out, has_active_skill=False) == ACTION_NOOP


def test_decide_failure_with_lesson_is_revise() -> None:
    out = LifecycleOutcome(status="continue", success=False, mission_lesson="x")
    assert decide_action(outcome=out, has_active_skill=True) == ACTION_REVISE


def test_decide_failure_no_lesson_is_noop() -> None:
    out = LifecycleOutcome(status="continue", success=False)
    assert decide_action(outcome=out, has_active_skill=True) == ACTION_NOOP


def test_decide_repeated_failure_retires() -> None:
    out = LifecycleOutcome(status="continue", success=False,
                           mission_lesson="meh", consecutive_failures=3)
    assert decide_action(outcome=out, has_active_skill=True) == ACTION_RETIRE


def test_archive_skill_moves_file(tmp_path: Path) -> None:
    skill = tmp_path / "useless.md"
    skill.write_text("body")
    archive = tmp_path / "_archive"
    out = archive_skill(skill, archive_root=archive)
    assert out is not None
    assert out.exists()
    assert out.read_text() == "body"
    assert not skill.exists()


def test_archive_skill_missing_returns_none(tmp_path: Path) -> None:
    assert archive_skill(tmp_path / "ghost.md",
                         archive_root=tmp_path / "_a") is None


def test_apply_reinforce_calls_writeback() -> None:
    store = _FakeStore()
    skill = _FakeSkill(name="s", path="/tmp/s.md")
    out = LifecycleOutcome(status="done", success=True, successful_trajectory="ok")
    res = apply_action(action=ACTION_REINFORCE, skill=skill, skill_store=store,
                       outcome=out, task_description="t", distiller=object())
    assert res["ok"] is True
    assert store.calls[0][0] == "writeback"


def test_apply_distill_calls_save_distilled() -> None:
    store = _FakeStore()
    out = LifecycleOutcome(status="done", success=True, raw_distill_output="# new")
    res = apply_action(action=ACTION_DISTILL, skill=None, skill_store=store,
                       outcome=out, task_description="t")
    assert res["ok"] is True
    assert store.calls[0][0] == "save_distilled"


def test_apply_distill_empty_text_fails() -> None:
    store = _FakeStore()
    out = LifecycleOutcome(status="done", success=True)
    res = apply_action(action=ACTION_DISTILL, skill=None, skill_store=store,
                       outcome=out, task_description="t")
    assert res["ok"] is False


def test_apply_revise_calls_promote_lesson() -> None:
    store = _FakeStore()
    skill = _FakeSkill(name="s", path="/tmp/s.md")
    out = LifecycleOutcome(status="continue", success=False, mission_lesson="x")
    res = apply_action(action=ACTION_REVISE, skill=skill, skill_store=store,
                       outcome=out, task_description="t", distiller=object())
    assert res["ok"] is True
    assert store.calls[0][0] == "promote_lesson"


def test_apply_revise_without_distiller_fails() -> None:
    store = _FakeStore()
    skill = _FakeSkill(name="s", path="/tmp/s.md")
    out = LifecycleOutcome(status="continue", success=False, mission_lesson="x")
    res = apply_action(action=ACTION_REVISE, skill=skill, skill_store=store,
                       outcome=out, task_description="t", distiller=None)
    assert res["ok"] is False


def test_apply_retire_archives_skill(tmp_path: Path) -> None:
    skill_file = tmp_path / "doomed.md"
    skill_file.write_text("# doomed")
    skill = _FakeSkill(name="doomed", path=str(skill_file))
    archive = tmp_path / "_archive"
    from argus_skill.skills import lifecycle as lc
    orig = lc.archive_skill
    try:
        def _archive_skill(p: Path, archive_root: Path = archive) -> Path | None:
            return orig(p, archive_root=archive_root)

        cast(Any, lc).archive_skill = _archive_skill
        res = apply_action(action=ACTION_RETIRE, skill=skill, skill_store=_FakeStore(),
                           outcome=LifecycleOutcome(status="continue", success=False),
                           task_description="t")
    finally:
        cast(Any, lc).archive_skill = orig
    assert res["ok"] is True
    assert not skill_file.exists()


def test_apply_emits_event_to_sink() -> None:
    seen: list[dict[str, Any]] = []
    apply_action(action=ACTION_NOOP, skill=None, skill_store=_FakeStore(),
                 outcome=LifecycleOutcome(status="done", success=True),
                 task_description="t", sink=seen.append)
    assert seen[0]["type"] == "skill.lifecycle.noop"


def test_apply_swallows_exception() -> None:
    class BadStore:
        def writeback_from_trajectory(self, **_: Any) -> None:
            raise RuntimeError("boom")
    skill = _FakeSkill(name="s", path="/x")
    out = LifecycleOutcome(status="done", success=True, successful_trajectory="z")
    res = apply_action(action=ACTION_REINFORCE, skill=skill, skill_store=BadStore(),
                       outcome=out, task_description="t", distiller=object())
    assert res["ok"] is False
    assert "boom" in res["details"]


def test_apply_unknown_action_fails() -> None:
    res = apply_action(action="frobnicate", skill=None, skill_store=_FakeStore(),
                       outcome=LifecycleOutcome(status="done", success=True),
                       task_description="t")
    assert res["ok"] is False
