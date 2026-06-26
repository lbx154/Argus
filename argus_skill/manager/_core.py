"""argus.manager — the user-facing Manager that DIVIDES a Task.

When the user hands over a Task, the Manager first decides whether it is a
"regular" task — one that maps to a preset vertical pipeline (a research paper,
or a lean optimize/speedrun loop) — then splits it into that vertical's Stages
and commits the choice. The existing engine (LifeSupervisor → Planner → SkillLoop
→ Engineer ↔ Reviewer) then advances stage-by-stage on its own.

This is a thin ORCHESTRATION layer — it reuses the real machinery, adding only
the user-facing *division* step:

  * classify   → ``skills.vertical_select.classify_vertical`` (LLM if a runner is
                 given, else a keyword heuristic; optimize verticals routed by
                 ``_route_optimize_vertical``)
  * stage list → ``verticals/<v>/stages.py`` ``STAGE_ORDER`` via ``load_vertical``
  * commit     → ``skills.vertical_select.persist_vertical`` — the supervisor then
                 TRUSTS the persisted vertical and does NOT re-classify
                 (see life/supervisor/_core.py:2460).

The Manager never judges the win and never plans loops itself — it only divides
the task and hands the current Stage to the existing Planner.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..skills import vertical_select
from ..skills.vertical_select import (
    classify_vertical,
    normalize_vertical,
    persist_vertical,
    resolve_vertical,
)

# Verticals that run a lean optimize/speedrun loop rather than the paper pipeline.
_OPTIMIZE_VERTICALS = frozenset(
    {"speedrun", "nanochat", "nanogpt_speedrun", "kernelbench"}
)


@dataclass
class Division:
    """The Manager's verdict on how to divide a Task."""
    task: str
    vertical: str            # research | speedrun | nanochat | nanogpt_speedrun | kernelbench
    kind: str                # "research" | "optimize"
    regular: bool            # True = maps to a preset pipeline; False = free-form
    stages: list[str]        # the vertical's Stage template (engine advances current_stage)

    def headline(self) -> str:
        tag = "regular" if self.regular else "free-form"
        return (f"[manager] {self.kind} task ({tag}) → vertical={self.vertical}, "
                f"{len(self.stages)} stage(s): {' → '.join(self.stages)}")


class Manager:
    """User-facing entry: divide a Task, then hand it to the existing engine.

    ``project_root`` is the life project dir (where PIPELINE_STATE.json lives).
    ``runner`` is an optional LLM backend for classification; without it the
    classifier degrades to the deterministic keyword heuristic.
    """

    def __init__(self, project_root: Path | str = ".", runner: Any = None) -> None:
        self.project_root = Path(project_root)
        self.runner = runner

    # ---- triage: is this a regular task, and which vertical/kind? ----
    def triage(self, task: str) -> tuple[str, str, bool]:
        """Return (vertical, kind, regular). Reuses vertical_select — no new classifier."""
        vertical = normalize_vertical(classify_vertical(task, runner=self.runner))
        kind = "optimize" if vertical in _OPTIMIZE_VERTICALS else "research"
        return vertical, kind, self._is_regular(task)

    @staticmethod
    def _is_regular(task: str) -> bool:
        """Regular = the task actually reads as a project (carries at least one
        research/optimize signal), not an empty or throwaway line. The classifier
        always maps to *some* vertical, so we additionally require a real signal."""
        t = (task or "").lower()
        if not t.strip():
            return False
        hits = sum(1 for s in vertical_select._SPEEDRUN_SIGNALS if s in t)
        hits += sum(1 for s in vertical_select._RESEARCH_SIGNALS if s in t)
        return hits >= 1

    # ---- split into the vertical's Stage template ----
    def plan_stages(self, vertical: str) -> list[str]:
        """The vertical's Stage list (research → the 8-stage paper pipeline).
        Reuses verticals/<v>/stages.py; falls back to the canonical 8 stages."""
        try:
            from ..verticals._base import load_vertical

            order = getattr(load_vertical(vertical), "STAGE_ORDER", None)
            if order:
                return list(order)
        except Exception:  # noqa: BLE001 — fall back, never crash division
            pass
        from ..skills.stage_checklists import CANONICAL_STAGE_ORDER

        return list(CANONICAL_STAGE_ORDER)

    # ---- the user-facing division step ----
    def divide(self, task: str) -> Division:
        """Classify → stages → COMMIT the vertical so the existing supervisor trusts
        it (no re-classify). Returns the Division for display/confirmation."""
        vertical, kind, regular = self.triage(task)
        stages = self.plan_stages(vertical)
        persist_vertical(self.project_root, vertical)   # supervisor reads & trusts this
        return Division(task=task, vertical=vertical, kind=kind,
                        regular=regular, stages=stages)

    # ---- conversational-intent decision (the Manager owns this) ----
    def is_conversational(self, text: str, *, run_exec: Any = None) -> bool:
        """The Manager's top-level dialogue call: is this free text a conversation
        (greeting / capability question / ack) rather than a real task?

        The Manager — not the runner — owns this decision. Reuses
        ``life/router.classify_is_conversational`` (conservative: biases hard
        toward TASK, so work is never silently skipped). ``run_exec`` is the LLM
        caller; when omitted one is built from ``self.runner``. With no backend at
        all, treat as a task (safe default — never drop work to a bad classify).
        """
        from ..life.router import classify_is_conversational

        if run_exec is None:
            if self.runner is None:
                return False
            from ..core.models import RunnerOptions

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return self.runner.run_exec(
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort="low", skip_git_repo_check=True
                    ),
                    run_label="manager-converse",
                )

        return classify_is_conversational(text, run_exec=run_exec)

    # ---- skill-library approval (the Manager is the top-level authority) ----
    def approve_skill(
        self,
        *,
        content: str,
        task: str,
        op: str = "create",
        reasoning_effort: str = "low",
    ) -> Any:
        """Judge whether a reviewer-proposed skill may enter the library.

        The Manager owns the generality + correctness gate (it sees the most
        context). Reuses ``skill_review.approve_skill`` but runs it on THIS
        Manager instance's ``runner`` — so "Manager approval" actually uses the
        Manager's backend, not the reviewer's. Returns an ``ApprovalVerdict``.
        """
        from .skill_review import approve_skill as _approve

        return _approve(
            content=content,
            task=task,
            op=op,
            runner=self.runner,
            reasoning_effort=reasoning_effort,
        )

    # ---- progress view ----
    def current_stage(self) -> str:
        """Which Stage the engine is on now (read from PIPELINE_STATE.json)."""
        import json

        try:
            state = json.loads(
                (self.project_root / "research" / "PIPELINE_STATE.json")
                .read_text(encoding="utf-8")
            )
            return str(state.get("current_stage") or "") or self.plan_stages(
                resolve_vertical(self.project_root)
            )[0]
        except Exception:  # noqa: BLE001
            return ""
