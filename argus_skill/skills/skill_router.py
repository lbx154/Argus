"""SkillRouter — the single front door to the skill library.

In the new architecture no role mutates skills directly. The Reviewer (or any
role) only PROPOSES changes via ``skill_ops``; SkillRouter owns:

  * selection — "which skill fits this task?" (delegates to the existing role
    matcher so this adds no new matching logic);
  * the write path — create / update / delete / archive, behind a validation
    pipeline so a bad skill never enters the shared library.

Validation before a create/update is STORED (in order, cheap-first):

  1. mechanical — the proposal parses to a well-formed playbook (name,
     description, real body with the expected sections);
  2. independence — it is not a near-duplicate of an existing skill (cosine
     similarity over the same structure ``compaction`` uses for de-duping);
  3. Manager approval — the generality + logical-correctness judgement, owned by
     the top-level Manager (it sees the most context). See
     ``manager.skill_review.approve_skill``.

``delete`` / ``archive`` skip validation — retiring a wrong/harmful skill is
applied directly on the reviewer's request.

This is a COMPONENT (not a standalone process); it reuses the engineer/reviewer
runner like the rest of the loop, and keeps a restartable internal session id so
its agentic calls can share context and be reset between projects.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Callable

from .compaction import DEFAULT_SIM_THRESHOLD, _pair_similarity, _skill_profile
from .skill_prompts import Prompts

log = logging.getLogger(__name__)

EventSink = Callable[[dict], None]

# A create/update proposal must look like a real playbook, not a one-liner.
_MIN_CONTENT_CHARS = 120
_REQUIRED_SECTION_HINTS = ("when to use", "how to solve")


class SkillRouter:
    """Owns skill selection + validated CRUD for one skill store."""

    def __init__(
        self,
        *,
        skill_store: Any,
        matcher: Any = None,
        judge_runner: Any = None,
        judge_model: str = "",
        sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    ) -> None:
        self.skill_store = skill_store
        self.matcher = matcher
        self.judge_runner = judge_runner
        self.judge_model = judge_model
        self.sim_threshold = sim_threshold
        # Restartable internal session: the router resumes this for its agentic
        # calls and clears it between projects via ``restart_session``.
        self._thread_id: str | None = None

    def restart_session(self) -> None:
        self._thread_id = None

    # -- selection (delegates to the role matcher; no new matching logic) --
    def select(self, task: str, **kwargs: Any) -> Any:
        if self.matcher is None:
            return None
        return self.matcher.match(task, **kwargs)

    # -- the write path --
    def apply_ops(
        self,
        ops: list[dict],
        *,
        task: str,
        on_event: EventSink | None = None,
    ) -> dict[str, int]:
        """Apply reviewer-proposed ops. Best-effort: one failing op never breaks
        the others. Returns a small counts summary."""
        counts = {"created": 0, "updated": 0, "archived": 0, "rejected": 0}
        for op in ops or []:
            kind = (op.get("op") or "").strip().lower()
            try:
                if kind in ("create", "update"):
                    if self._handle_proposal(op, task, on_event):
                        counts["created" if kind == "create" else "updated"] += 1
                    else:
                        counts["rejected"] += 1
                elif kind in ("archive", "delete"):
                    if self._handle_retire(op, on_event):
                        counts["archived"] += 1
            except Exception as exc:  # noqa: BLE001 — one bad op never breaks the rest
                log.warning("skill_op %s failed (%s: %s)", kind, type(exc).__name__, exc)
                self._emit(on_event, {"type": "skill.op.error",
                                      "text": f"{kind} failed: {type(exc).__name__}"})
        return counts

    # ------------------------------------------------------------------
    def _handle_proposal(self, op: dict, task: str, on_event: EventSink | None) -> bool:
        from ..manager.skill_review import approve_skill

        content = (op.get("content") or "").strip()
        kind = op.get("op")

        # 1. mechanical — well-formed playbook?
        reason = self._mechanical_reject_reason(content)
        if reason:
            self._reject(on_event, kind, reason)
            return False

        name, description, category, _ = Prompts.parse_skill_output(content)
        # 2. independence — not a near-duplicate (an update is allowed to resemble
        # the very skill it revises, so exclude that one from the comparison).
        exclude = op.get("name") if kind == "update" else None
        sim, near = self._max_similarity(
            name=name, description=description, category=category,
            content=content, exclude_name=exclude,
        )
        if sim >= self.sim_threshold:
            self._reject(on_event, kind,
                         f"too similar to '{near}' (sim={sim:.2f} ≥ {self.sim_threshold:.2f})")
            return False

        # 3. Manager approval — generality + logical correctness.
        verdict = approve_skill(
            content=content, task=task, op=kind,
            runner=self.judge_runner, model=self.judge_model,
        )
        if not verdict.approved:
            self._reject(on_event, kind, f"manager: {verdict.why}")
            return False

        # 4. store (born provisional — must still prove effective on later reuse).
        if kind == "update":
            target = self._find_skill_by_name(op.get("name", ""))
            if target is None:
                self._emit(on_event, {"type": "skill.op.error",
                                      "text": f"update: skill '{op.get('name')}' not found"})
                return False
            updated = self.skill_store.update_skill_content(
                target, content, task_desc=task, on_event=on_event)
            if updated is not None:
                self._emit(on_event, {"type": "skill.updated",
                                      "text": f"updated {updated.name} -> v{updated.version} "
                                              f"(candidate, manager-approved)"})
                return True
            return False

        new_skill = self.skill_store.save_distilled(
            task_description=task, raw_distill_output=content,
            on_event=on_event, provisional=True,
        )
        if new_skill is not None:
            self._emit(on_event, {"type": "skill.created",
                                  "text": f"created candidate skill {new_skill.name} "
                                          f"(manager-approved)"})
            return True
        return False

    def _handle_retire(self, op: dict, on_event: EventSink | None) -> bool:
        name = (op.get("name") or "").strip()
        target = self._find_skill_by_name(name)
        if target is None:
            self._emit(on_event, {"type": "skill.op.error",
                                  "text": f"archive: skill '{name}' not found"})
            return False
        archived = self.skill_store.archive(target)
        if archived is None:
            return False
        why = (op.get("why") or "").strip()
        self._emit(on_event, {"type": "skill.archived",
                              "text": f"archived {name}" + (f": {why}" if why else "")})
        return True

    # ------------------------------------------------------------------
    @staticmethod
    def _mechanical_reject_reason(content: str) -> str:
        if len(content) < _MIN_CONTENT_CHARS:
            return "too short to be a real playbook"
        name, description, _category, _ = Prompts.parse_skill_output(content)
        if not name.strip():
            return "missing a skill title"
        if not description.strip():
            return "missing a description"
        low = content.lower()
        if not any(h in low for h in _REQUIRED_SECTION_HINTS):
            return "missing a 'When to use' / 'How to solve' section"
        return ""

    def _max_similarity(
        self, *, name: str, description: str, category: str, content: str,
        exclude_name: str | None = None,
    ) -> tuple[float, str]:
        proposed = _skill_profile(SimpleNamespace(
            name=name, description=description, category=category, content=content))
        best, best_name = 0.0, ""
        for s in self.skill_store.list_summaries():
            if exclude_name and s.get("name") == exclude_name:
                continue
            try:
                existing = self.skill_store.load(s["path"])
            except Exception:  # noqa: BLE001
                continue
            sim = _pair_similarity(proposed, _skill_profile(existing))
            if sim > best:
                best, best_name = sim, s.get("name", "")
        return best, best_name

    def _find_skill_by_name(self, name: str) -> Any | None:
        name = (name or "").strip()
        if not name:
            return None
        for s in self.skill_store.list_summaries():
            if s.get("name") == name:
                try:
                    return self.skill_store.load(s["path"])
                except Exception:  # noqa: BLE001
                    return None
        return None

    def _reject(self, on_event: EventSink | None, kind: str, why: str) -> None:
        self._emit(on_event, {"type": "skill.proposal.rejected",
                              "text": f"rejected {kind}: {why}"})

    @staticmethod
    def _emit(on_event: EventSink | None, event: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 — telemetry must never break the loop
            log.debug("skill router emit failed", exc_info=True)


__all__ = ["SkillRouter"]
