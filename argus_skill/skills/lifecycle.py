"""Mission-completion → skill-lifecycle dispatcher (Phase 2 closing).

After a mission ends the supervisor must decide what to do with
the active skill: reinforce it (success → bump task history),
distill a new one (no skill matched → birth one from the
trajectory), revise/promote-lesson (a non-fatal failure produced
useful learnings), or retire it (repeated catastrophic failures).

This module collects that policy in ONE place so callers
(``MissionExecutor`` completion callback, future replay tools)
can dispatch with a single function call. The actual writes are
delegated to :class:`SkillStore` / :class:`LayeredSkillStore`
methods that already exist; we only own the decision table here.

Decision table (status, success, has_skill) → action
-----------------------------------------------------
* (done,   True,  yes) → ``reinforce``  (writeback_from_trajectory)
* (done,   True,  no)  → ``distill``    (save_distilled)
* (continue or blocked, *, yes) with a non-empty mission_lesson
                                → ``revise``    (promote_lesson)
* repeated failure (≥3 fatal in a row) → ``retire`` (archive)
* anything else                    → ``noop``

The dispatcher records ``skill.lifecycle.*`` events on the supplied
sink so REPL UIs can show "promoted lesson into add-two-numbers" or
"archived buggy-flake-detector after 3 consecutive failures".
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None]

__all__ = [
    "LifecycleAction",
    "LifecycleOutcome",
    "decide_action",
    "apply_action",
    "archive_skill",
]


@dataclass
class LifecycleOutcome:
    """Inputs the dispatcher reasons over.

    * ``status`` — last reviewer verdict ("done", "continue", "blocked").
    * ``success`` — whether the mission as a whole completed.
    * ``mission_lesson`` — reviewer-emitted lesson text (may be empty).
    * ``successful_trajectory`` — the engineer's final trajectory text
      that produced ``done`` (only meaningful when ``success`` is True).
    * ``consecutive_failures`` — for the *active skill*, how many of
      its most recent uses ended in a fatal failure (used to decide
      retirement).
    * ``raw_distill_output`` — when no skill matched, the scientist's
      raw output to feed to ``save_distilled``.
    """

    status: str
    success: bool
    mission_lesson: str = ""
    successful_trajectory: str = ""
    consecutive_failures: int = 0
    raw_distill_output: str = ""


# Lifecycle action labels — kept as plain strings (not Enum) so they
# round-trip cleanly through JSONL events.
LifecycleAction = str

ACTION_NOOP = "noop"
ACTION_REINFORCE = "reinforce"
ACTION_DISTILL = "distill"
ACTION_REVISE = "revise"
ACTION_RETIRE = "retire"

_RETIRE_THRESHOLD = 3


def decide_action(
    *,
    outcome: LifecycleOutcome,
    has_active_skill: bool,
) -> LifecycleAction:
    """Pure decision: which lifecycle action does this outcome warrant?"""
    if outcome.consecutive_failures >= _RETIRE_THRESHOLD and has_active_skill:
        return ACTION_RETIRE
    if outcome.success and outcome.status == "done":
        if has_active_skill:
            return ACTION_REINFORCE
        if outcome.raw_distill_output.strip():
            return ACTION_DISTILL
        return ACTION_NOOP
    # Failure path
    if has_active_skill and outcome.mission_lesson.strip():
        return ACTION_REVISE
    return ACTION_NOOP


def archive_skill(
    skill_path: str | os.PathLike[str],
    *,
    archive_root: Path | None = None,
) -> Path | None:
    """Move a skill markdown into ``skills/_archive/``. Returns the
    archived path, or ``None`` if the source didn't exist.

    Names collide on the day-of-archive prefix — we add a short uuid
    suffix to keep the archive append-only.
    """
    src = Path(skill_path)
    if not src.exists():
        return None
    if archive_root is None:
        from ..core import paths as core_paths
        archive_root = core_paths.skills_archive_root()
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = archive_root / f"{src.stem}.{stamp}.{uuid.uuid4().hex[:6]}{src.suffix}"
    # Use shutil.move so atomic-rename inside one filesystem still
    # works, but we also handle cross-fs by falling back to copy+unlink.
    try:
        shutil.move(str(src), target)
    except OSError:
        shutil.copy2(str(src), target)
        try:
            src.unlink()
        except OSError:  # pragma: no cover — best-effort
            pass
    return target


def apply_action(
    *,
    action: LifecycleAction,
    skill: Any | None,
    skill_store: Any,
    outcome: LifecycleOutcome,
    task_description: str,
    distiller: Any | None = None,
    scientist_model: str = "",
    sink: EventSink | None = None,
) -> dict[str, Any]:
    """Run the chosen lifecycle action against the skill store.

    Returns a result dict like ``{"action": "...", "ok": True/False,
    "details": "..."}``. Never raises — failures are reported via
    the result dict and the optional ``sink``.
    """
    result: dict[str, Any] = {"action": action, "ok": False, "details": ""}

    def _emit(text: str, *, ok: bool) -> None:
        result["ok"] = ok
        result["details"] = text
        if sink is not None:
            try:
                sink({
                    "type": f"skill.lifecycle.{action}",
                    "ok": ok,
                    "skill_name": getattr(skill, "name", "") if skill else "",
                    "skill_path": getattr(skill, "path", "") if skill else "",
                    "text": text,
                })
            except Exception:  # noqa: BLE001
                log.exception("skill lifecycle: sink raised")

    try:
        if action == ACTION_NOOP:
            _emit("no action", ok=True)
            return result

        if action == ACTION_REINFORCE:
            if skill is None:
                _emit("no active skill to reinforce", ok=False)
                return result
            skill_store.writeback_from_trajectory(
                skill=skill,
                task_description=task_description,
                successful_trajectory=outcome.successful_trajectory,
                distiller=distiller,
                scientist_model=scientist_model,
                revise=False,
            )
            _emit(f"reinforced {getattr(skill, 'name', '')}", ok=True)
            return result

        if action == ACTION_DISTILL:
            if not outcome.raw_distill_output.strip():
                _emit("no distill output to write", ok=False)
                return result
            new_skill = skill_store.save_distilled(
                task_description=task_description,
                raw_distill_output=outcome.raw_distill_output,
                scientist_model=scientist_model or "scientist",
            )
            if new_skill is None:
                _emit("distilled output rejected by quality gate", ok=False)
                return result
            _emit(f"distilled new skill {getattr(new_skill, 'name', '')}",
                  ok=True)
            return result

        if action == ACTION_REVISE:
            if skill is None:
                _emit("no active skill to revise", ok=False)
                return result
            if distiller is None:
                _emit("no distiller — cannot revise", ok=False)
                return result
            ok = skill_store.promote_lesson(
                skill=skill,
                lesson_text=outcome.mission_lesson,
                task_description=task_description,
                distiller=distiller,
                scientist_model=scientist_model,
            )
            _emit(
                f"promoted lesson into {getattr(skill, 'name', '')}"
                if ok else "promote_lesson refused",
                ok=ok,
            )
            return result

        if action == ACTION_RETIRE:
            if skill is None or not getattr(skill, "path", ""):
                _emit("no skill path to retire", ok=False)
                return result
            archived = archive_skill(skill.path)
            if archived is None:
                _emit("source already gone", ok=False)
                return result
            _emit(f"archived to {archived}", ok=True)
            return result

        _emit(f"unknown action: {action!r}", ok=False)
        return result

    except Exception as exc:  # noqa: BLE001
        log.exception("apply_action failed")
        _emit(f"exception: {exc}", ok=False)
        return result


# Concurrency note: ``apply_action`` can be invoked from a daemon
# worker thread that itself is fanning out events. We don't need a
# global lock — SkillStore.save uses an atomic-replace temp file
# (see :meth:`SkillStore.save`) and ``archive_skill`` operates on a
# single source path. A per-call internal lock is enough to keep
# event ordering stable when the sink is shared across threads.
_emit_lock = threading.Lock()  # reserved for future use


def _stamp_lifecycle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Add a ``ts`` field if missing — useful for callers that
    forward events to JSONL buses with delayed timestamps."""
    event = dict(event)
    event.setdefault("ts", time.time())
    return event
