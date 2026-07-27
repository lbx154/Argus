"""Shared protocols, memory helpers, and scratch state for the lifetime-agent
runtime.

Split out of ``_runtime.py`` so that module stays under the maintainability
line-count target. Every name here is re-exported from ``_runtime.py`` (see
its module docstring and ``__all__``) so external imports are unaffected.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, ClassVar, Protocol

from ._env import env_flag as _env_flag
from ._target_paths import resolve_life_root


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

    def render_prelude(self) -> str: ...


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


def _project_state_dir_for(args: argparse.Namespace, workdir: Path) -> Path | None:
    """Resolve the existing per-project runtime state directory."""
    if not _env_flag("ARGUS_SKILL_CHECKPOINT_PERSIST", True):
        return None
    try:
        explicit_state_dir = getattr(args, "project_state_dir", None)
        if explicit_state_dir:
            state_dir = Path(explicit_state_dir).expanduser()
            state_dir.mkdir(parents=True, exist_ok=True)
            return state_dir

        from ..core.paths import session_state_root
        from ..core.project import project_fingerprint

        global_root = _resolve_global_root(args)
        fingerprint = project_fingerprint(workdir).fingerprint
        state_dir = session_state_root(fingerprint, root=global_root)
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir
    except Exception:  # noqa: BLE001 — never let path resolution break a mission
        return None


def _checkpoint_path_for(args: argparse.Namespace, workdir: Path) -> Path | None:
    """Shared checkpoint in internal project state, never the output workdir."""
    if not _env_flag("ARGUS_SKILL_CHECKPOINT_PERSIST", True):
        return None
    try:
        state_dir = _project_state_dir_for(args, workdir)
        return state_dir / "CHECKPOINT.md" if state_dir is not None else None
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
            from ..cli import default_theme, render_event_for_terminal

            self._render = render_event_for_terminal
            self._theme = default_theme()
        except Exception:  # noqa: BLE001
            pass

    # Events that life.mission.started/completed already cover; we silence
    # them in life mode to avoid duplicate noise around mission boundaries.
    # Also drop a few protocol/skill-machinery events that the user can't
    # act on and that just clutter the chat scroll (matcher/author
    # banter, internal "distill done" weight reports).
    _SILENCED_IN_LIFE: ClassVar[frozenset[str]] = frozenset(
        {
            "loop.start",
            "loop.done",
            "match.info",  # "skill store empty - will distill a new playbook"
            "distill.done",  # "distilled (4009 chars, 0 tok)"
        }
    )

    def handle_event(self, event: dict[str, Any]) -> None:
        if self.quiet:
            return
        et = str(event.get("type", ""))
        if et in self._SILENCED_IN_LIFE:
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


def _should_run_stage_transition(
    status: object,
    *,
    mission_scope: str = "",
    require_independent_review: bool = False,
    review_source: str = "",
    skip_stage_transition: bool = False,
) -> bool:
    normalized = str(status or "")
    normalized_scope = str(mission_scope or "").strip().lower().replace("-", "_")
    if (
        skip_stage_transition
        and require_independent_review
        and normalized_scope == "bounded"
    ):
        return False
    if normalized == "replan_requested":
        return False
    if normalized.startswith("paused_"):
        return False
    return bool(
        require_independent_review
        or str(mission_scope or "").strip().lower() == "final_submission"
        or str(review_source or "").strip().lower()
        in {"engineer_self_review", "reviewer"}
    )


class _ExecuteState:
    """Mutable scratch state threaded through ``_SkillLoopRunner.execute``'s
    lifecycle phases. One instance per ``execute()`` call; never persisted.
    """

    def __init__(self) -> None:
        # Set by ``_build_execute_config``.
        self.workdir: Path = Path.cwd()
        self.effective_require_independent_review: bool = False
        self.config: Any = None
        self.maintenance_checkpoint_dir: Path | None = None

        # Set by ``_prepare_execute_mission_context``.
        self.full_task: str = ""
        self.seed: str | None = None
        self.mission_scope: str = ""

        # Set by ``_build_execute_skill_store_and_loop``.
        self.loop: Any = None

        # Set by ``_invoke_execute_loop``.
        self.outcome: Any = None
        self.trusted_playground_workflow: bool = False
        self.playground_workflow_guarded: bool = False
        self.protected_playground_source_violation: bool = False

        # Set by ``_extract_execute_outcome_fields``.
        self.new_tid: str | None = None
        self.auth_fail: Any = None
        self.rounds_list: list = []
        self.operator_question: str = ""
        self.final_review_status: str = ""
        self.review_source: str = ""
        self.final_submission_certified: bool = False
        self.completion_evidence: str = ""

        # Set by ``_maybe_decide_stage_transition``.
        self.effective_status: str = ""
        self.effective_stop_kind: Any = None
        self.effective_recoverable: bool = False
        self.effective_reason: str = ""
        self.stage_transition: dict = {}
        self.stage_transition_skipped: bool = False
