"""SkillRouter — the single front door to the skill library.

In the new architecture no role mutates skills directly. The Reviewer (or any
role) only PROPOSES changes via ``skill_ops``; SkillRouter owns:

  * selection — "which skill fits this task?" (delegates to the existing role
    matcher so this adds no new matching logic);
  * the write path — create / update / delete / archive, behind cheap validation
    plus the provisional effectiveness lifecycle.

Validation before a create/update is STORED (in order, cheap-first):

  1. mechanical — the proposal parses to a well-formed playbook (name,
     description, real body with the expected sections);
  2. independence — it is not a near-duplicate of an existing skill. Judged
     ENTIRELY by an LLM over COMPACT SUMMARIES (name/description/category —
     progressive disclosure, same shape the matcher already uses, never full
     skill bodies) — this catches paraphrased duplicates a lexical
     comparison would miss (e.g. "Debug CUDA OOM" vs "Fix GPU memory
     overflow"). There is no lexical/scored fallback, and no silent
     fail-open: when a non-empty library needs the duplicate judge but no
     usable judge verdict is available, the proposal is rejected explicitly
     so the harness never guesses or silently waves it through;
  3. provisional proof — create/update candidates are stored as provisional and
     must later prove effective under Reviewer supervision.

``delete`` / ``archive`` skip validation — retiring a wrong/harmful skill is
applied directly on the reviewer's request.

This is a COMPONENT (not a standalone process); it reuses the engineer/reviewer
runner like the rest of the loop, and keeps a restartable internal session id so
its agentic calls can share context and be reset between projects.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ..core.models import RunnerOptions
from .skill_prompts import Prompts

log = logging.getLogger(__name__)

EventSink = Callable[[dict], None]

# A create/update proposal must look like a real playbook, not a one-liner.
_MIN_CONTENT_CHARS = 120
_REQUIRED_SECTION_HINTS = ("when to use", "how to solve")

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
        judge_runner: Any = None,
        judge_model: str = "",
        judge_reasoning_effort: str = "high",
    ) -> None:
        self.skill_store = skill_store
        self.matcher = matcher
        # Ordinary skill create/update is Scientist/Reviewer-owned: Scientist
        # or Reviewer authors the candidate, and the Reviewer proves usefulness
        # via the provisional lifecycle — there is no separate APPROVAL judge
        # (no Manager gate). ``judge_runner`` below is a DIFFERENT thing: an
        # LLM call that judges DUPLICATION (semantic independence), same
        # backend the reviewer/matcher already run on — never a content
        # approval/rejection authority over well-formed, non-duplicate work.
        self.judge_runner = judge_runner
        self.judge_model = judge_model
        self.judge_reasoning_effort = judge_reasoning_effort
        # Restartable internal session: the router resumes this for its agentic
        # calls and clears it between projects via ``restart_session``.
        self._thread_id: str | None = None

    def restart_session(self) -> None:
        self._thread_id = None

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

    # ------------------------------------------------------------------
    def _handle_proposal(
        self,
        op: dict,
        task: str,
        on_event: EventSink | None,
    ) -> bool:
        content = (op.get("content") or "").strip()
        kind = op.get("op")

        # 1. mechanical — well-formed playbook?
        reason = self._mechanical_reject_reason(content)
        if reason:
            self._reject(on_event, kind, reason)
            return False

        name, description, category, _ = Prompts.parse_skill_output(content)
        # Self-governance: a CREATE must not SHADOW a protected skill by reusing
        # its name. A top-level shadow with an identical display name wins the
        # matcher's last-wins name resolution and would neutralize the protected
        # playbook WITHOUT ever touching it (so the archive/update gates never
        # fire). Block it here; genuine improvements must go through an update.
        if kind == "create":
            clash = self._find_skill_by_name(name)
            if clash is not None and self._is_protected(clash):
                self._reject(on_event, kind,
                             f"name collides with protected skill '{name}' — "
                             f"revise it via an update, do not shadow it")
                return False
        # 2. independence — not a near-duplicate (an update is allowed to resemble
        # the very skill it revises, so exclude that one from the comparison).
        # LLM judge ONLY (progressive disclosure over summaries) — catches
        # paraphrased duplicates a lexical comparison would miss. No judge
        # runner configured, or the call fails -> the check is skipped
        # (fail-open); there is no scored/lexical fallback.
        exclude = op.get("name") if kind == "update" else None
        try:
            verdict = self._llm_duplicate_check(
                name=name, description=description, category=category,
                exclude_name=exclude,
            )
        except RuntimeError as exc:
            self._reject(on_event, kind, str(exc))
            return False
        if verdict is not None:
            is_dup, near, why = verdict
            if is_dup:
                self._reject(on_event, kind,
                             f"too similar to '{near}' (llm judge: {why})")
                return False

        # 3. store (born provisional — must still prove effective on later reuse).
        if kind == "update":
            target = self._find_skill_by_name(op.get("name", ""))
            if target is None:
                self._emit(on_event, {"type": "skill.op.error",
                                      "text": f"update: skill '{op.get('name')}' not found"})
                return False
            # A PROTECTED (governing) skill is not updated by the ordinary
            # Scientist/Reviewer skill-generation path. Strengthening protected
            # guardrails needs an explicit source-code change, not a runtime skill
            # candidate.
            if self._is_protected(target):
                self._reject(on_event, kind, "protected skill updates require explicit source review")
                return False
            updated = self.skill_store.update_skill_content(
                target, content, task_desc=task, on_event=on_event)
            if updated is not None:
                self._emit(on_event, {"type": "skill.updated",
                                      "text": f"updated {updated.name} -> v{updated.version} "
                                              f"(candidate)"})
                return True
            return False

        new_skill = self.skill_store.save_distilled(
            task_description=task, raw_distill_output=content,
            on_event=on_event, provisional=True,
        )
        if new_skill is not None:
            self._emit(on_event, {"type": "skill.created",
                                  "text": f"created candidate skill {new_skill.name}"})
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
        archived = self.skill_store.archive(target)
        if archived is None:
            return False
        why = (op.get("why") or "").strip()
        self._emit(on_event, {"type": "skill.archived",
                              "text": f"archived {name}" + (f": {why}" if why else "")})
        return True

    def _refuse_self_governance(self, on_event: EventSink | None, kind: str,
                                name: str, reason: str) -> None:
        """Refuse (and audit) a destructive op that targets a protected skill or
        the skill governing the current mission."""
        self._emit(on_event, {"type": "skill.op.refused",
                              "text": f"{kind} '{name}' refused: {reason}"})

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

    def _llm_duplicate_check(
        self, *, name: str, description: str, category: str,
        exclude_name: str | None = None,
    ) -> tuple[bool, str, str] | None:
        """Ask the judge runner whether the proposal duplicates an existing
        skill, over COMPACT SUMMARIES only (progressive disclosure — cheap
        even against a large library). Returns ``(is_duplicate, matched_name,
        why)``, or ``None`` when there is nothing to compare against. Missing
        or unusable judge infrastructure RAISES so the caller can reject the
        proposal explicitly; there is no scored/lexical fallback and no silent
        fail-open."""
        summaries = [
            s for s in self.skill_store.list_summaries()
            if not (exclude_name and s.get("name") == exclude_name)
        ]
        if not summaries:
            return False, "", "library is empty"
        if self.judge_runner is None:
            raise RuntimeError(
                "duplicate judge unavailable: configure a healthy judge_runner "
                "before create/update against a non-empty skill library"
            )
        prompt = Prompts.skill_duplicate_check(
            name=name, description=description, category=category,
            summaries=summaries,
        )
        try:
            result = self.judge_runner.run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=self.judge_model or None,
                    reasoning_effort=self.judge_reasoning_effort,
                    skip_git_repo_check=True,
                    full_auto=True,
                ),
                run_label="skill.duplicate_check",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("skill duplicate judge failed (%s: %s)", type(exc).__name__, exc)
            raise RuntimeError(
                f"duplicate judge failed: {type(exc).__name__}: {exc}"
            ) from exc
        text = (getattr(result, "last_agent_message", "") or "").strip()
        if not text:
            raise RuntimeError("duplicate judge returned no text")
        try:
            left, right = text.find("{"), text.rfind("}")
            parsed = json.loads(text[left:right + 1]) if left >= 0 < right else json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError("duplicate judge returned malformed JSON") from None
        if not isinstance(parsed, dict):
            raise RuntimeError("duplicate judge returned a non-object verdict")
        is_dup = bool(parsed.get("duplicate"))
        of_name = str(parsed.get("of", "") or "").strip()
        why = str(parsed.get("why", "") or "").strip()[:300]
        if is_dup and not of_name:
            raise RuntimeError(
                "duplicate judge returned duplicate=true without naming the "
                "existing conflicting skill"
            )
        return is_dup, of_name, why

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
