"""SkillRouter — the single front door to the skill library.

In the new architecture no role mutates skills directly. The Reviewer (or any
role) only PROPOSES changes via ``skill_ops``; SkillRouter owns:

  * selection — "which skill fits this task?" (delegates to the existing role
    matcher so this adds no new matching logic);
  * the write path — create / update / delete / archive, behind structural
    validation and protected-skill safeguards.

Validation before a create/update is stored:

  1. structural — the proposal has a name, description, and non-empty body;
  2. identity/self-governance — creates cannot reuse an existing name and
     ordinary runtime ops cannot mutate protected skills.

There is deliberately no text-quality, duplicate-judge, candidate, or promotion
gate. New versions are active immediately. Real task trajectories and Reviewer
feedback drive later update/archive decisions; reversible compaction handles
duplicates asynchronously.

``delete`` / ``archive`` apply directly to ordinary project-layer skills;
protected and shared-global skills require explicit maintenance authority.

This is a storage/routing component, not a standalone process or judge.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .skill_prompts import Prompts
from .store import role_of_path

log = logging.getLogger(__name__)

EventSink = Callable[[dict], None]

# A skill whose CATEGORY marks it as a governing/guardrail playbook is protected
# at the harness level exactly like an explicit ``protected: true`` frontmatter
# flag: a self-modifying mission can never archive/delete/update it at runtime
# (strengthening one requires an explicit, out-of-band source-code change), and
# may not shadow it with a same-named create. This is the one legitimate hard
# rule (anti-cheat / self-governance), enforced mechanically — no judge, no
# bypass. Note ``learning`` is deliberately NOT here: learned skills must stay
# reversible.
_PROTECTED_CATEGORIES = frozenset({"anti-cheat", "guardrail", "role-identity"})


class SkillRouter:
    """Owns skill selection + validated CRUD for one skill store."""

    def __init__(
        self,
        *,
        skill_store: Any,
        matcher: Any = None,
    ) -> None:
        self.skill_store = skill_store
        self.matcher = matcher
        self._last_created_skill: Any | None = None

    @staticmethod
    def _is_protected(skill: Any) -> bool:
        """A skill is protected when its frontmatter carries ``protected: true``
        OR its category names a governing/guardrail class. Both paths are the
        harness's mechanical self-governance floor."""
        if getattr(skill, "protected", False):
            return True
        category = (getattr(skill, "category", "") or "").strip().lower()
        return category in _PROTECTED_CATEGORIES

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
        the others. Returns a small counts summary.

        Self-governance is enforced by the PROTECTED floor (a skill with
        frontmatter ``protected: true`` or a governing category — see
        ``_is_protected``): such a skill is never archived/deleted/updated at
        runtime (strengthening one requires an explicit source-code change).
        Ordinary skills the mission merely USED remain freely retirable —
        retiring a skill you found wrong/harmful is the flywheel working, not
        a self-governance breach."""
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
                    else:
                        counts["rejected"] += 1
            except Exception as exc:  # noqa: BLE001 — one bad op never breaks the rest
                log.warning("skill_op %s failed (%s: %s)", kind, type(exc).__name__, exc)
                self._emit(on_event, {"type": "skill.op.error",
                                      "text": f"{kind} failed: {type(exc).__name__}"})
        return counts

    def create_from_scientist(
        self,
        content: str,
        *,
        task: str,
        on_event: EventSink | None = None,
    ) -> Any | None:
        """Scientist write path (same structural guards as Reviewer create)."""
        self._last_created_skill = None
        if not self._handle_proposal(
            {"op": "create", "content": content, "why": "scientist miss"},
            task,
            on_event,
        ):
            return None
        return self._last_created_skill

    # ------------------------------------------------------------------
    def _handle_proposal(
        self,
        op: dict,
        task: str,
        on_event: EventSink | None,
    ) -> bool:
        content = (op.get("content") or "").strip()
        kind = op.get("op")

        reason = self._structural_reject_reason(content)
        if reason:
            self._reject(on_event, kind, reason)
            return False

        name, _description, _category, _ = Prompts.parse_skill_output(content)
        if kind == "create" and self._find_skill_by_name(name) is not None:
            self._reject(
                on_event,
                kind,
                f"skill name already exists: '{name}' — update it instead of "
                "creating a numbered duplicate",
            )
            return False
        if kind == "update":
            target = self._find_skill_by_name(op.get("name", ""))
            if target is None:
                self._emit(on_event, {"type": "skill.op.error",
                                      "text": f"update: skill '{op.get('name')}' not found"})
                return False
            # A PROTECTED (governing) skill is not updated by the ordinary
            # Scientist/Reviewer skill-generation path. Strengthening protected
            # guardrails needs an explicit source-code change, not a runtime skill
            # version.
            if self._is_protected(target):
                self._reject(on_event, kind, "protected skill updates require explicit source review")
                return False
            updated = self.skill_store.update_skill_content(
                target, content, task_desc=task, on_event=on_event)
            if updated is not None:
                self._emit(on_event, {
                    "type": "skill.updated",
                    "skill_id": updated.skill_id,
                    "name": updated.name,
                    "version": updated.version,
                    "scope": role_of_path(updated.path, self.skill_store.skills_dir),
                    "path": updated.path,
                    "text": f"updated {updated.name} -> v{updated.version} (active)",
                })
                return True
            return False

        new_skill = self.skill_store.save_distilled(
            task_description=task, raw_distill_output=content,
            on_event=on_event,
        )
        if new_skill is not None:
            self._last_created_skill = new_skill
            self._emit(on_event, {
                "type": "skill.created",
                "skill_id": new_skill.skill_id,
                "name": new_skill.name,
                "version": new_skill.version,
                "scope": role_of_path(new_skill.path, self.skill_store.skills_dir),
                "path": new_skill.path,
                "text": f"created active skill {new_skill.name}",
            })
            return True
        return False

    def _handle_retire(
        self,
        op: dict,
        on_event: EventSink | None,
    ) -> bool:
        name = (op.get("name") or "").strip()
        target = self._find_skill_by_name(name)
        if target is None:
            self._emit(on_event, {"type": "skill.op.error",
                                  "text": f"archive: skill '{name}' not found"})
            return False
        # Self-governance floor (mechanical, always on): a governing/protected
        # skill may be strengthened but never removed. Ordinary skills the mission
        # merely used stay retirable (retiring a wrong/harmful skill is the
        # reviewer's direct authority — the flywheel working as designed).
        if self._is_protected(target):
            self._refuse_self_governance(on_event, op.get("op"), name,
                                         "protected — may be strengthened, never removed")
            return False
        layer_for_skill = getattr(self.skill_store, "layer_for_skill", None)
        if callable(layer_for_skill) and layer_for_skill(target) == "global":
            self._emit(on_event, {
                "type": "skill.op.refused",
                "text": (
                    f"archive '{name}' refused: a project reviewer cannot mutate "
                    "the shared global layer; fork/update it locally or use an "
                    "explicit global maintenance operation"
                ),
            })
            return False
        archived = self.skill_store.archive(target)
        if archived is None:
            return False
        why = (op.get("why") or "").strip()
        self._emit(on_event, {
            "type": "skill.archived",
            "skill_id": target.skill_id,
            "name": target.name,
            "version": target.version,
            "scope": role_of_path(target.path, self.skill_store.skills_dir),
            "path": str(archived),
            "reason": why,
            "text": f"archived {name}" + (f": {why}" if why else ""),
        })
        return True

    def _refuse_self_governance(self, on_event: EventSink | None, kind: str,
                                name: str, reason: str) -> None:
        """Refuse (and audit) a destructive op that targets a protected skill or
        the skill governing the current mission."""
        self._emit(on_event, {"type": "skill.op.refused",
                              "text": f"{kind} '{name}' refused: {reason}"})

    # ------------------------------------------------------------------
    @staticmethod
    def _structural_reject_reason(content: str) -> str:
        name, description, _category, body = Prompts.parse_skill_output(content)
        if not name.strip():
            return "missing a skill title"
        if not description.strip():
            return "missing a description"
        if not body.strip():
            return "missing skill body"
        return ""

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
