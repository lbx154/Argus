from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Protocol

from .._target_paths import resolve_life_root


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class _CommonMemory(Protocol):
    @property
    def identity(self) -> Any: ...

    @property
    def journal(self) -> Any: ...

    @property
    def backlog(self) -> Any: ...


class _SplitMemory(_CommonMemory, Protocol):
    @property
    def global_mem(self) -> Any: ...

    @property
    def project(self) -> Any: ...

    @property
    def global_root(self) -> Any: ...

    def render_prelude(self, *, objective: str) -> str: ...


def _memory_project_root(mem: Any) -> Path:
    project = getattr(mem, "project", None)
    root = getattr(project, "root", None)
    if root is not None:
        return Path(root)
    return Path(getattr(mem, "root"))


def _memory_global_root(mem: Any) -> Path:
    root = getattr(mem, "global_root", None)
    if root is not None:
        return Path(root)
    return _memory_project_root(mem)


def _resolve_global_root(args: argparse.Namespace) -> Path:
    return resolve_life_root(getattr(args, "life_dir", None))


def _checkpoint_path_for(args: argparse.Namespace, workdir: Path) -> Path | None:
    """Per-project curated-checkpoint file in the project state dir.

    Lives next to ``events.jsonl`` / ``memory.jsonl`` under
    ``<global_root>/projects/<fingerprint>/checkpoint.json`` so the reviewer's
    per-round handoff survives across missions and daemon restarts, never the
    git work-tree (which the agent might commit). Set
    ``ARGUS_SKILL_CHECKPOINT_PERSIST=0`` to opt back into in-memory-only.
    """
    if not _env_flag("ARGUS_SKILL_CHECKPOINT_PERSIST", True):
        return None
    try:
        from ...core.project import project_fingerprint

        global_root = _resolve_global_root(args)
        fingerprint = project_fingerprint(workdir).fingerprint
        state_dir = global_root / "projects" / fingerprint
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir / "checkpoint.json"
    except Exception:  # noqa: BLE001 — never let path resolution break a mission
        return None


class LifeStderrSink:
    """Forward events to stderr using chat's renderer.

    Always-verbose: every event type the engine emits (except a small
    in-life silence-list below) is shown. The product positioning is a
    7×24 lifetime agent — operators want full visibility of what the
    daemon is doing, always. The earlier ``verbose``/``quiet`` toggles
    have been removed (kept ``quiet`` only for in-process tests that
    pump events without wanting stderr noise).
    """

    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet
        self._render: Callable[..., str] | None = None
        self._theme: Any = None
        try:
            from ...cli import default_theme, render_event_for_terminal
            self._render = render_event_for_terminal
            self._theme = default_theme()
        except Exception:  # noqa: BLE001
            pass

    def _allowed(self, event_type: str) -> bool:  # noqa: ARG002
        return True

    # Events that life.mission.started/completed already cover; we silence
    # them in life mode to avoid duplicate noise around mission boundaries.
    # Also drop a few protocol/skill-machinery events that the user can't
    # act on and that just clutter the chat scroll (matcher/scientist
    # banter, internal "distill done" weight reports).
    _SILENCED_IN_LIFE: ClassVar[frozenset[str]] = frozenset({
        "loop.start",
        "loop.done",
        "match.info",         # "skill store empty - will distill a new playbook"
        "scientist.start",    # "no high-fit skill — distilling"
        "distill.done",       # "distilled (4009 chars, 0 tok)"
    })

    def handle_event(self, event: dict[str, Any]) -> None:
        if self.quiet:
            return
        et = str(event.get("type", ""))
        if et in self._SILENCED_IN_LIFE:
            return
        if not self._allowed(et):
            return
        if self._render is not None:
            try:
                line = self._render(event, theme=self._theme)
                if line:  # empty string = renderer chose to swallow event
                    sys.stderr.write(line + "\n")
                    sys.stderr.flush()
                return
            except Exception:  # noqa: BLE001
                pass
        text = event.get("text") or event.get("title") or ""
        sys.stderr.write(f"[{et}] {text}\n")
        sys.stderr.flush()

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Required by ``make_stream_progress_callback``.

        Life mode has no JSONL outbox to keep an audit trail in — the
        cooked ``engineer.progress`` events that ``stream_progress``
        synthesises from the same raw lines are what we render. The raw
        lines themselves are intentionally discarded here; ``codex
        --output-format stream-json`` produces dozens per second and
        echoing them all would defeat the point of having a renderer.
        """
        return

    def close(self) -> None:
        return

@dataclass
class _Outcome:
    """Duck-typed outcome the supervisor reads via ``getattr``."""
    success: bool
    status: str
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str | None = None
    skill_distilled: bool = False
    had_follow_up: bool = False
    last_thread_id: str | None = None
    # Chat fast-path: when True, the supervisor skips iteration / critic
    # because the operator's input was a conversational message (greeting,
    # capability question, ack) that doesn't warrant a polish cycle.
    chat_mode: bool = False
    # Set when the codex backend reports auth-related stderr (expired
    # token, missing API key, etc.). The supervisor uses this to stop
    # early instead of looping over failing missions.
    auth_failure: bool = False
    # Reviewer completion contract (replaces the retired EMNLP validator
    # gate). Set True only when the mission scope was ``final_submission``
    # AND the final reviewer verdict certified the whole project complete
    # (status=done, scope=final_submission, every checklist item satisfied
    # with evidence). The supervisor uses this — never raw ``success`` — to
    # decide whole-project completion. ``completion_evidence`` carries the
    # reviewer's completion summary for the journal.
    final_submission_certified: bool = False
    completion_evidence: str = ""
    # Reviewer-authored structured briefing for the project planner. Shape:
    # ``{"forward_progress": bool, "headline": str, "blocker": str,
    # "recommended_next": str}``. Empty dict when no reviewer verdict exists.
    planner_report: dict = field(default_factory=dict)

