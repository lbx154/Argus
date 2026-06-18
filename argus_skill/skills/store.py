"""On-disk skill store, LLM matcher and keyword fallback.

Provenance: vendored from ``skill-agent/skill_agent/skill_store.py``. The
key refactor: ``find_relevant`` no longer imports ``codex_exec`` directly.
It now takes a ``RunnerBackend`` (and the model name to use) explicitly,
so the same store works against codex, claude-code, or the test stub.

Skills are markdown files with a YAML-style frontmatter block. The
``Skill`` dataclass + parse/render helpers are unchanged. ``SkillStore``
indexes the directory, asks a small model to pick the best match for a
task, and falls back to keyword overlap if the matcher errors out.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.models import RunnerOptions, RunnerResult
from ..core.ports import RunnerBackend
from ..scientist.prompts import Prompts

log = logging.getLogger(__name__)

# Role-scoped matcher pools. A role's matcher only considers skills whose
# on-disk subdir maps to one of these buckets, so e.g. the engineer never
# matches a reviewer-only skill. ``role=None`` (the default) disables
# scoping and matches the whole corpus (back-compat). Distilled/user skills
# live at the top level and are bucketed as ``general`` (engineer-domain).
ROLE_SKILL_POOLS: dict[str, frozenset[str]] = {
    "engineer": frozenset({"engineer", "general"}),
    "reviewer": frozenset({"reviewer"}),
    "planner": frozenset({"planner"}),
}
# Cross-role *reference* pools. A role's matcher ALSO considers these
# subdirs, but their skills are surfaced as read-only "other-role
# perspective" references — never as the role's own primary playbook, and
# never eligible for skill writeback. This lets the engineer anticipate the
# reviewer's rubric, the reviewer understand the engineer's playbook, and
# the planner see both, without blurring role identity. See
# ``role_match.partition_by_role``.
ROLE_CROSS_READ_POOLS: dict[str, frozenset[str]] = {
    "engineer": frozenset({"reviewer"}),
    "reviewer": frozenset({"engineer"}),
    "planner": frozenset({"engineer", "reviewer"}),
}
_ROLE_SUBDIRS = frozenset({"engineer", "reviewer", "planner"})


def role_of_path(path: str, skills_dir: Path) -> str:
    """Return the role bucket a skill file belongs to (its first subdir).

    Top-level files (distilled/user skills) bucket as ``general``. Mirrors
    the logic in :meth:`SkillStore.list_summaries` so callers can classify a
    loaded :class:`Skill` without re-listing.
    """
    try:
        rel_parts = Path(path).resolve().relative_to(Path(skills_dir).resolve()).parts
    except ValueError:
        rel_parts = Path(path).parts
    return (
        rel_parts[0]
        if len(rel_parts) > 1 and rel_parts[0] in _ROLE_SUBDIRS
        else "general"
    )

TASK_HISTORY_MAX_ITEMS = 32
TASK_HISTORY_MAX_ITEM_LEN = 200

# Boilerplate prefixes that historically polluted ``task_history``.
# These come from the supervisor's prelude (memory/identity context) and
# are NOT meaningful task descriptors. ``append_task_history`` strips
# them; the migration helper ``cleanse_task_history`` retroactively
# removes them from existing skill files.
_BOILERPLATE_PREFIXES = (
    "### Memory context",
    "## Memory context",
    "# Memory context",
    "Memory context (non-authoritative)",
)


def _looks_like_boilerplate(text: str) -> bool:
    head = text.lstrip()[:200]
    return any(head.startswith(p) for p in _BOILERPLATE_PREFIXES)


def append_task_history(skill: "Skill", task_desc: str) -> None:
    if not task_desc:
        return
    cleaned = task_desc.strip()
    if _looks_like_boilerplate(cleaned):
        # Don't store prelude boilerplate. Future matcher uses
        # ``task_history`` as a cheap recall signal, and identical
        # prelude headers across every skill destroy that signal.
        return
    if cleaned in skill.task_history:
        return
    skill.task_history.append(cleaned[:TASK_HISTORY_MAX_ITEM_LEN])
    if len(skill.task_history) > TASK_HISTORY_MAX_ITEMS:
        skill.task_history = skill.task_history[-TASK_HISTORY_MAX_ITEMS:]


def cleanse_task_history(skill: "Skill") -> int:
    """Drop boilerplate entries from an existing skill. Returns count removed."""
    before = len(skill.task_history)
    skill.task_history = [
        t for t in skill.task_history if not _looks_like_boilerplate(t)
    ]
    return before - len(skill.task_history)


def _parse_skill_version(raw: str) -> int:
    value = raw.strip().strip('"').strip("'")
    if not value:
        return 1
    try:
        return int(value)
    except ValueError:
        match = re.match(r"^(\d+)(?:\.\d+)*$", value)
        if match:
            return int(match.group(1))
        return 1


@dataclass
class Skill:
    name: str
    description: str
    category: str
    content: str
    version: int = 1
    scientist_model: str = ""
    created_at: str = ""
    task_history: list[str] = field(default_factory=list)
    path: str = ""
    # Provisional = a CANDIDATE skill change (a newly-created skill, or a fresh
    # revision of an existing one) that is NOT yet proven. It is kept ("入库")
    # only when a LATER round that carries it gets an effective reviewer verdict
    # (``confirm_provisional``); an ineffective one is discarded — a fresh skill
    # deleted, a revision reverted to its last-confirmed snapshot. The judgment
    # is the reviewer's verdict on the ROUND, never a judge of the skill text.
    # Absent in legacy frontmatter -> ``False`` (confirmed).
    provisional: bool = False

    def render(self) -> str:
        history = ""
        if self.task_history:
            items = "\n".join(
                f"  - {json.dumps(t, ensure_ascii=False)}"
                for t in self.task_history[-10:]
            )
            history = f"task_history:\n{items}\n"
        provisional_lines = "provisional: true\n" if self.provisional else ""
        return (
            f"---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"category: {self.category}\n"
            f"version: {self.version}\n"
            f"scientist_model: {self.scientist_model}\n"
            f"created_at: {self.created_at}\n"
            f"{provisional_lines}"
            f"{history}"
            f"---\n\n"
            f"{self.content}"
        )

    @classmethod
    def parse(cls, text: str, path: str = "") -> Skill:
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not fm_match:
            return cls(name="", description="", category="", content=text, path=path)
        fm = fm_match.group(1)
        content = text[fm_match.end():].strip()

        def _get(key: str) -> str:
            m = re.search(rf"^{key}:\s*(.+)$", fm, re.MULTILINE)
            return m.group(1).strip() if m else ""

        history: list[str] = []
        hist_match = re.search(r"task_history:\s*\n((?:\s+-\s+.+\n?)+)", fm)
        if hist_match:
            history = []
            for match in re.finditer(r"-\s+(.+)", hist_match.group(1)):
                raw_item = match.group(1).strip()
                try:
                    history.append(str(json.loads(raw_item)))
                except json.JSONDecodeError:
                    history.append(raw_item.strip('"'))

        return cls(
            name=_get("name"),
            description=_get("description"),
            category=_get("category"),
            content=content,
            version=_parse_skill_version(_get("version")),
            scientist_model=_get("scientist_model"),
            created_at=_get("created_at"),
            task_history=history,
            path=path,
            provisional=_get("provisional").strip().strip('"').strip("'").lower()
            in {"true", "yes", "1"},
        )


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class SkillStore:
    """Markdown-backed skill cache with LLM matcher.

    Construction signature changed from skill-agent: pass an explicit
    ``runner`` (RunnerBackend) and ``matcher_model``. The runner is
    only invoked from ``find_relevant``; storage operations (load/save
    /list_summaries) are pure I/O.
    """
    _FRONTMATTER_MAX_BYTES = 8192

    def __init__(
        self,
        skills_dir: Path,
        *,
        runner: RunnerBackend | None = None,
        matcher_model: str = "",
        matcher_reasoning_effort: str | None = None,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.matcher_model = matcher_model
        self.matcher_reasoning_effort = matcher_reasoning_effort
        self._summary_cache: dict[str, tuple[int, int, dict]] = {}
        self._match_cache: dict[tuple, list[str] | None] = {}
        self._match_cache_max = 64
        self._last_match_input_tokens = 0
        self._last_match_cached_input_tokens = 0
        self._last_match_output_tokens = 0
        # Non-semantic safety valve for the pure-LLM matcher: the model sees
        # EVERY in-scope candidate (no keyword pre-filtering). For very large
        # pools we split into deterministic batches of this size and union
        # the matches, so cost stays bounded without ever silently dropping a
        # candidate. The common case (pool <= cap) is a single matcher call.
        self._matcher_max_candidates = max(
            1, int(os.environ.get("ARGUS_SKILL_MATCHER_MAX_CANDIDATES", "80") or "80")
        )

    # ------------------------------------------------------------------
    # Listing / loading / saving
    # ------------------------------------------------------------------

    def _fingerprint_summaries(self, summaries: list[dict]) -> tuple:
        parts: list[tuple[str, int, int]] = []
        for s in summaries:
            entry = self._summary_cache.get(s["path"])
            if entry is not None:
                parts.append((s["path"], entry[0], entry[1]))
            else:
                parts.append((s["path"], 0, 0))
        return tuple(parts)

    @classmethod
    def _read_frontmatter(cls, path: Path) -> str:
        try:
            with path.open("rb") as f:
                chunk = f.read(cls._FRONTMATTER_MAX_BYTES)
        except OSError:
            return ""
        return chunk.decode("utf-8", errors="replace")

    def list_summaries(self) -> list[dict]:
        summaries: list[dict] = []
        seen: set[str] = set()
        for p in sorted(self.skills_dir.rglob("*.md")):
            rel = p.relative_to(self.skills_dir)
            # Skip dotfiles AND the archive tree — archived (pruned/retired)
            # skills must never re-enter the matcher candidate pool.
            if any(
                part.startswith(".") or part == "_archive" for part in rel.parts
            ):
                continue
            key = str(p)
            seen.add(key)
            try:
                st = p.stat()
            except OSError:
                continue
            cached = self._summary_cache.get(key)
            if (
                cached is not None
                and cached[0] == st.st_mtime_ns
                and cached[1] == st.st_size
            ):
                summaries.append(cached[2])
                continue
            text = self._read_frontmatter(p)
            skill = Skill.parse(text, str(p))
            if not skill.name.strip():
                log.warning(
                    "skipping skill file with missing/invalid frontmatter: %s", p
                )
                continue
            rel_parts = p.relative_to(self.skills_dir).parts
            skill_role = (
                rel_parts[0]
                if len(rel_parts) > 1 and rel_parts[0] in _ROLE_SUBDIRS
                else "general"
            )
            summary = {
                "name": skill.name,
                "description": skill.description,
                "category": skill.category,
                "task_history": skill.task_history[:5],
                "path": str(p),
                "role": skill_role,
                "provisional": skill.provisional,
            }
            self._summary_cache[key] = (st.st_mtime_ns, st.st_size, summary)
            summaries.append(summary)
        if len(self._summary_cache) != len(seen):
            for stale in list(self._summary_cache.keys() - seen):
                self._summary_cache.pop(stale, None)
        return summaries

    def load(self, path: str) -> Skill:
        text = Path(path).read_text(encoding="utf-8")
        return Skill.parse(text, path)

    def save(self, skill: Skill) -> Path:
        import os
        import threading
        import uuid
        path = Path(skill.path) if skill.path else self._build_skill_path(skill)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}."
            f"{uuid.uuid4().hex[:8]}"
        )
        try:
            tmp.write_text(skill.render(), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        skill.path = str(path)
        return path

    # ------------------------------------------------------------------
    # SkillSource protocol — additions for argus-skill loop
    # ------------------------------------------------------------------

    def render_skill(self, skill: Skill) -> str:
        """Markdown rendering for prompt injection. Skips frontmatter."""
        return skill.content.strip()

    def save_distilled(
        self,
        *,
        task_description: str,
        raw_distill_output: str,
        scientist_model: str,
        on_event: "Callable[[dict], None] | None" = None,
        enforce_quality_gate: bool = True,
        provisional: bool = False,
    ) -> "Skill | None":
        """Parse the raw scientist output and persist it.

        We do NOT gate on the skill TEXT: judging a skill's prose is worse than
        chance (SkillLens), so quality is proven by EFFECT instead — a freshly
        distilled skill is born ``provisional`` and is only confirmed (kept) when
        a later round that carries it gets an effective reviewer verdict; an
        ineffective one is discarded. ``enforce_quality_gate`` is accepted for
        backward compatibility but ignored.
        """
        name, description, category, content = Prompts.parse_skill_output(raw_distill_output)
        if not (content or "").strip():
            if on_event is not None:
                try:
                    on_event({"type": "skill.distill.rejected",
                              "text": "distiller returned empty content"})
                except Exception:  # noqa: BLE001
                    log.debug("skill.distill.rejected emit failed", exc_info=True)
            return None
        skill = Skill(
            name=name or "unnamed-skill",
            description=description,
            category=category,
            content=content,
            version=1,
            scientist_model=scientist_model,
            created_at=datetime.now(timezone.utc).isoformat(),
            task_history=[],
            provisional=bool(provisional),
        )
        append_task_history(skill, task_description)
        self.save(skill)
        return skill

    def writeback_from_trajectory(
        self,
        *,
        skill: Skill,
        task_description: str,
        successful_trajectory: str,
        distiller: "Any | None" = None,
        scientist_model: str = "",
        revise: bool = False,
        on_event: "Callable[[dict], None] | None" = None,
    ) -> None:
        """Append task to history; optionally have the scientist revise.

        Default behavior (``revise=False``) is the legacy v0.1 path:
        history-append + timestamp refresh, no markdown edits.

        When ``revise=True`` and ``distiller`` is provided, the scientist
        is asked (via :meth:`Distiller.revise`) to produce a revised
        playbook that integrates the successful trajectory. On a
        successful parse the new content replaces ``skill.content`` and
        ``skill.version`` is bumped. On any failure (LLM error, parse
        miss, oversize) we fall back to the legacy history-only path so
        the writeback never crashes the hot path.
        """
        if not skill.path:
            return

        revised_ok = False
        if revise and distiller is not None:
            revised_ok = self._revise_via_distiller(
                skill=skill,
                task_description=task_description,
                change_kind="success_trajectory",
                evidence=successful_trajectory or "",
                distiller=distiller,
                scientist_model=scientist_model,
                on_event=on_event,
            )

        if not revised_ok:
            append_task_history(skill, task_description)
            skill.created_at = datetime.now(timezone.utc).isoformat()
            self.save(skill)

    def promote_lesson(
        self,
        *,
        skill: Skill,
        lesson_text: str,
        task_description: str,
        distiller: "Any",
        scientist_model: str = "",
        on_event: "Callable[[dict], None] | None" = None,
    ) -> bool:
        """Auto-merge a reviewer-emitted lesson into the skill markdown.

        Returns ``True`` if the markdown was updated and version bumped,
        ``False`` if the revise call failed and the skill was left
        untouched. Best-effort: never raises.
        """
        if not skill.path or not (lesson_text or "").strip():
            return False
        return self._revise_via_distiller(
            skill=skill,
            task_description=task_description,
            change_kind="failure_lesson",
            evidence=lesson_text,
            distiller=distiller,
            scientist_model=scientist_model,
            on_event=on_event,
        )

    # ------------------------------------------------------------------
    # Provisional (candidate) lifecycle — a skill change is proven by EFFECT:
    # confirmed (入库) when a round carrying it is effective, else discarded.
    # ------------------------------------------------------------------

    def _prev_snapshot_path(self, skill: Skill) -> "Path | None":
        if not skill.path:
            return None
        p = Path(skill.path)
        return p.parent / f".{p.stem}.prev.md"

    def confirm_provisional(self, skill: Skill) -> bool:
        """Promote a candidate to confirmed (入库) after it proved effective.
        Drops the revert snapshot. No-op for already-confirmed skills."""
        if not skill.path or not skill.provisional:
            return False
        skill.provisional = False
        self.save(skill)
        snap = self._prev_snapshot_path(skill)
        if snap is not None:
            try:
                snap.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                log.debug("confirm: prev-snapshot unlink failed", exc_info=True)
        return True

    def discard_provisional(
        self,
        skill: Skill,
        *,
        on_event: "Callable[[dict], None] | None" = None,
    ) -> str:
        """An unproven candidate that was carried into a round and still did NOT
        produce an effective result is dropped. If a revert snapshot exists (the
        candidate was a REVISION of a confirmed skill), restore that last-confirmed
        version; otherwise the candidate was a fresh skill, so archive it. Returns
        ``"reverted"``, ``"discarded"`` or ``"noop"``. Best-effort; never raises."""
        if not skill.path or not skill.provisional:
            return "noop"
        snap = self._prev_snapshot_path(skill)
        try:
            if snap is not None and snap.exists():
                prior = Skill.parse(snap.read_text(encoding="utf-8"), path=skill.path)
                skill.content = prior.content
                skill.version = prior.version
                skill.description = prior.description or skill.description
                skill.provisional = False
                self.save(skill)
                try:
                    snap.unlink()
                except OSError:
                    pass
                self._summary_cache.pop(str(skill.path), None)
                self._match_cache.clear()
                if on_event is not None:
                    on_event({"type": "skill.reverted", "skill_name": skill.name,
                              "text": f"reverted unproven revision of {skill.name} "
                                      f"to its last confirmed version"})
                return "reverted"
            from .lifecycle import archive_skill  # local import: avoid cycle
            archived = archive_skill(skill.path)
            self._summary_cache.pop(str(skill.path), None)
            self._match_cache.clear()
            if on_event is not None:
                on_event({"type": "skill.discarded", "skill_name": skill.name,
                          "text": f"discarded unproven skill {skill.name} (never "
                                  f"effective)" + (f" -> {archived}" if archived else "")})
            return "discarded"
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.warning("discard_provisional failed (%s: %s)", type(exc).__name__, exc)
            return "noop"


    def _revise_via_distiller(
        self,
        *,
        skill: Skill,
        task_description: str,
        change_kind: str,
        evidence: str,
        distiller: "Any",
        scientist_model: str,
        on_event: "Callable[[dict], None] | None",
    ) -> bool:
        from ..scientist.distiller import DistillerConfig  # local import: avoid cycle
        try:
            cfg = DistillerConfig(
                model=scientist_model or skill.scientist_model or "gpt-5.5",
                reasoning_effort="high",
                skip_git_repo_check=True,
                full_auto=True,
            )
            result = distiller.revise(
                old_skill_md=skill.render(),
                task_description=task_description,
                change_kind=change_kind,
                evidence=evidence,
                config=cfg,
                on_event=on_event,
            )
            raw = (result.last_agent_message or "").strip()
            if not raw:
                return False
            name, description, category, content = Prompts.parse_skill_output(raw)
            # Sanity gate: refuse pathological revisions that would
            # destroy matchability.
            if not content.strip() or len(content) < 200:
                log.warning("revise produced empty/tiny content (%d chars); rejecting.",
                            len(content))
                return False
            new_content = content if content.lstrip().startswith("#") else raw
            # The revision is a CANDIDATE: snapshot the last-confirmed version so
            # an ineffective revision can be reverted, then mark provisional so it
            # must prove effective (reviewer verdict next round) before it sticks.
            snap = self._prev_snapshot_path(skill)
            if snap is not None and not skill.provisional and not snap.exists():
                try:
                    snap.write_text(skill.render(), encoding="utf-8")
                except OSError as snap_exc:
                    # Without a revert snapshot we must NOT overwrite a confirmed
                    # skill — a later failed revision would be archived with nothing
                    # to restore. Abort the revision; the confirmed skill stays
                    # intact (writeback falls back to history-only append).
                    log.warning("revise: cannot write revert snapshot (%s); aborting "
                                "revision to protect the confirmed skill", snap_exc)
                    return False
            # Preserve identity unless the scientist explicitly proposed
            # rename + we accept it (we do not, in v0.2).
            self.update_skill(skill, new_content, task_description)
            if scientist_model:
                skill.scientist_model = scientist_model
            skill.provisional = True
            self.save(skill)
            if on_event:
                on_event({
                    "type": "skill.revised",
                    "text": f"{skill.name} → v{skill.version} ({change_kind})",
                    "skill": skill.name,
                    "version": skill.version,
                    "change_kind": change_kind,
                })
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.warning("skill revise (%s) failed: %s: %s",
                        change_kind, type(exc).__name__, exc)
            if on_event:
                on_event({
                    "type": "skill.revise.error",
                    "text": f"{change_kind}: {type(exc).__name__}",
                })
            return False

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def find_relevant(
        self,
        task_description: str,
        on_event: Callable[[dict], None] | None = None,
        *,
        role: str | None = None,
        exclude_files: set[str] | None = None,
    ) -> tuple[list[Skill] | None, int]:
        """Use small model to judge which skills are relevant to the task.

        Returns ``(matched_skills | None, tokens_used)``.

        ``None`` means "matcher said no high-fit skill, OR something
        broke and the caller should distill instead".

        ``role`` scopes the candidate pool to that role's skills (see
        :data:`ROLE_SKILL_POOLS`); ``None`` matches the whole corpus.
        ``exclude_files`` drops skills by on-disk filename (e.g. skills a
        role already injects verbatim, so the matcher never re-surfaces
        them).
        """
        summaries = self._scope_summaries(
            self.list_summaries(), role=role, exclude_files=exclude_files
        )
        if not summaries:
            self._last_match_input_tokens = 0
            self._last_match_cached_input_tokens = 0
            self._last_match_output_tokens = 0
            if on_event:
                msg = (
                    "skill store empty - will distill a new playbook"
                    if role is None
                    else f"no skills in scope for role={role}"
                )
                on_event({"type": "match.info", "text": msg})
            return None, 0

        cache_key = (
            " ".join(task_description.lower().split()),
            role or "",
            self._fingerprint_summaries(summaries),
        )
        cached = self._match_cache.get(cache_key)
        if cached is not None or cache_key in self._match_cache:
            self._match_cache.pop(cache_key, None)
            self._match_cache[cache_key] = cached
            self._last_match_input_tokens = 0
            self._last_match_cached_input_tokens = 0
            self._last_match_output_tokens = 0
            if on_event:
                label = (
                    ", ".join(Path(p).stem for p in cached) if cached else "no match"
                )
                on_event({"type": "match.info",
                          "text": f"matcher cache hit: {label} (0 tok)"})
            if cached is None:
                return None, 0
            try:
                return [self.load(p) for p in cached], 0
            except OSError:
                self._match_cache.pop(cache_key, None)

        if self.runner is None:
            log.warning("SkillStore.find_relevant called with no runner; "
                        "cannot run matcher")
            if on_event:
                on_event({"type": "match.info",
                          "text": "no runner configured — will distill"})
            self._last_match_input_tokens = 0
            self._last_match_cached_input_tokens = 0
            self._last_match_output_tokens = 0
            return None, 0

        # Pure-LLM matching: the model judges EVERY in-scope candidate (no
        # keyword pre-filter). Large pools are split into deterministic
        # batches and the matches unioned, so cost is bounded without ever
        # silently dropping a candidate.
        #
        # Optional BM25 prefilter: when the pool is large enough that the
        # matcher prompt would crowd the small-router context window, we
        # cheaply prune to top-K candidates first. Threshold is env-tunable
        # via ``ARGUS_SKILL_BM25_PREFILTER_THRESHOLD`` (default 200), so the
        # default behaviour at our current N=50 is unchanged. See
        # ``argus_skill/skills/bm25_prefilter.py`` for the rationale.
        from .bm25_prefilter import bm25_prefilter, is_prefilter_enabled
        pre_n = len(summaries)
        if is_prefilter_enabled(pre_n):
            summaries = bm25_prefilter(task_description, summaries)
            if on_event and len(summaries) < pre_n:
                on_event({
                    "type": "match.info",
                    "text": (
                        f"BM25 prefilter narrowed pool {pre_n}→{len(summaries)} "
                        "candidates before LLM matcher"
                    ),
                })
        batches = self._candidate_batches(summaries)
        if on_event:
            names = ", ".join(s.get("name", "?") for s in summaries[:5])
            more = f" (+{len(summaries) - 5} more)" if len(summaries) > 5 else ""
            batch_note = f" in {len(batches)} batches" if len(batches) > 1 else ""
            on_event({
                "type": "match.info",
                "text": (
                    f"querying matcher ({self.matcher_model}) against "
                    f"{len(summaries)} candidates{batch_note}: {names}{more}"
                ),
            })

        matched_by_path: dict[str, Skill] = {}
        in_tok = cached_tok = out_tok = 0
        for batch in batches:
            prompt = Prompts.skill_match(
                task_description, batch, requesting_role=role
            )
            try:
                result: RunnerResult = self.runner.run_exec(
                    prompt=prompt,
                    options=RunnerOptions(
                        model=self.matcher_model,
                        reasoning_effort=self.matcher_reasoning_effort,
                        # The matcher is a pure-LLM call — no tool use, no
                        # workspace reads. Codex CLI refuses to run outside a
                        # trusted git repo by default, which silently breaks
                        # the matcher when the daemon's workdir is just a
                        # scratch dir. Always opt out for the matcher.
                        skip_git_repo_check=True,
                    ),
                    run_label="matcher",
                )
            except Exception as exc:  # noqa: BLE001 — best-effort, keyword fallback
                log.error("skill matcher subprocess raised: %s", exc)
                if on_event:
                    on_event({"type": "match.error",
                              "text": f"matcher subprocess raised: {exc} — falling back to keyword overlap"})
                kw = self._keyword_fallback(task_description, summaries=summaries)
                self._last_match_input_tokens = 0
                self._last_match_cached_input_tokens = 0
                self._last_match_output_tokens = 0
                if kw:
                    self._cache_match(cache_key, kw)
                    return kw, 0
                self._cache_match(cache_key, [])
                return None, 0

            # The matcher is an optional recall helper. Treat a subprocess
            # fatal result the same as a backend exception so a bad matcher
            # route cannot prevent the actual role mission from starting.
            if result.fatal_error or result.exit_code != 0:
                err = result.fatal_error or f"exit_code={result.exit_code}"
                stderr_tail = (
                    " | ".join(result.stderr_lines[-3:])
                    if result.stderr_lines else ""
                )
                log.error("skill matcher subprocess failed: %s ; stderr: %s",
                          err, stderr_tail)
                if on_event:
                    on_event({"type": "match.error",
                              "text": f"matcher subprocess failed: {err}"
                                      + (f" — {stderr_tail}" if stderr_tail else "")
                                      + " — falling back to keyword overlap"})
                kw = self._keyword_fallback(task_description, summaries=summaries)
                self._last_match_input_tokens = 0
                self._last_match_cached_input_tokens = 0
                self._last_match_output_tokens = 0
                if kw:
                    self._cache_match(cache_key, kw)
                    return kw, 0
                self._cache_match(cache_key, [])
                return None, 0
            in_tok += int(getattr(result, "input_tokens", 0) or 0)
            cached_tok += int(getattr(result, "cached_input_tokens", 0) or 0)
            out_tok += int(getattr(result, "output_tokens", 0) or 0)
            for sk in self._parse_match_response(result.message, batch):
                matched_by_path.setdefault(sk.path, sk)

        total_tokens = in_tok + out_tok
        self._last_match_input_tokens = in_tok
        self._last_match_cached_input_tokens = cached_tok
        self._last_match_output_tokens = out_tok
        matched = list(matched_by_path.values())
        if matched:
            if on_event:
                on_event({"type": "match.info",
                          "text": f"matcher picked: {matched[0].name}  "
                                  f"({total_tokens:,} tok)"})
            self._cache_match(cache_key, matched)
            return matched, total_tokens

        if on_event:
            on_event({"type": "match.info",
                      "text": f"matcher: no high-fit match  ({total_tokens:,} tok) - will distill"})
        self._cache_match(cache_key, [])
        return None, total_tokens

    @property
    def last_match_input_tokens(self) -> int:
        return self._last_match_input_tokens

    @property
    def last_match_cached_input_tokens(self) -> int:
        return self._last_match_cached_input_tokens

    @property
    def last_match_output_tokens(self) -> int:
        return self._last_match_output_tokens

    def _cache_match(
        self, key: tuple, matched: list[Skill]
    ) -> None:
        paths = [s.path for s in matched] if matched else None
        if key in self._match_cache:
            self._match_cache.pop(key)
        self._match_cache[key] = paths
        while len(self._match_cache) > self._match_cache_max:
            self._match_cache.pop(next(iter(self._match_cache)))

    def _parse_match_response(
        self, response: str, summaries: list[dict]
    ) -> list[Skill]:
        matched = []
        name_to_path = {s["name"].casefold(): s["path"] for s in summaries}
        response_lower = response.lower()

        if "none" in response_lower and "no relevant" in response_lower:
            return []

        parsed_names, json_parsed = self._extract_matched_names(response)
        for parsed_name in parsed_names:
            path = name_to_path.get(parsed_name.casefold())
            if path:
                matched.append(self.load(path))

        if matched:
            return matched

        if json_parsed:
            return matched

        for summary in summaries:
            if re.search(rf"(^|[^a-z0-9]){re.escape(summary['name'])}([^a-z0-9]|$)", response, re.IGNORECASE):
                matched.append(self.load(summary["path"]))

        return matched

    def update_skill(self, skill: Skill, new_content: str, task_desc: str) -> Skill:
        skill.content = new_content
        skill.version += 1
        skill.created_at = datetime.now(timezone.utc).isoformat()
        append_task_history(skill, task_desc)
        self.save(skill)
        return skill

    def _build_skill_path(self, skill: Skill) -> Path:
        base = _slugify(skill.name) or "skill"
        candidate = self.skills_dir / f"{base}.md"
        if not candidate.exists():
            return candidate
        for idx in range(2, 1000):
            next_candidate = self.skills_dir / f"{base}-{idx}.md"
            if not next_candidate.exists():
                return next_candidate
        raise RuntimeError(f"unable to allocate skill path for {skill.name!r}")

    @staticmethod
    def _normalize_tokens(text: str) -> set[str]:
        return {
            token
            for token in re.findall(
                r"[a-z0-9]+", text.lower().replace("_", " ").replace("-", " ")
            )
            if len(token) >= 3
        }

    def _score_summary(self, task_description: str, summary: dict) -> int:
        task_lower = task_description.lower()
        task_tokens = self._normalize_tokens(task_description)
        summary_text = " ".join(
            [
                summary.get("name", ""),
                summary.get("description", ""),
                summary.get("category", ""),
                *summary.get("task_history", []),
            ]
        )
        summary_tokens = self._normalize_tokens(summary_text)
        overlap = len(task_tokens & summary_tokens)
        score = overlap
        if summary.get("category") and summary["category"].lower() in task_lower:
            score += 4
        if summary.get("name") and summary["name"].lower() in task_lower:
            score += 6
        return score

    @staticmethod
    def _scope_summaries(
        summaries: list[dict],
        *,
        role: str | None,
        exclude_files: set[str] | None,
    ) -> list[dict]:
        """Filter summaries to a role's matchable pool and drop excluded files.

        The matchable pool is the role's ``primary`` subdirs
        (:data:`ROLE_SKILL_POOLS`) UNION its ``cross-read`` reference subdirs
        (:data:`ROLE_CROSS_READ_POOLS`). Primary-vs-reference partitioning of
        the *matched* results happens later (``role_match.partition_by_role``)
        so the matcher gets recall across both while callers keep the roles
        distinct. ``role=None`` disables scoping (whole corpus).
        """
        out = summaries
        if role is not None:
            primary = ROLE_SKILL_POOLS.get(role, frozenset({role, "general"}))
            cross = ROLE_CROSS_READ_POOLS.get(role, frozenset())
            pool = primary | cross
            out = [s for s in out if s.get("role", "general") in pool]
        if exclude_files:
            excl = {f.casefold() for f in exclude_files}
            out = [s for s in out if Path(s["path"]).name.casefold() not in excl]
        return out

    def _candidate_batches(self, summaries: list[dict]) -> list[list[dict]]:
        """Split candidates into deterministic, non-semantic batches.

        Pure-LLM matching means the model judges every candidate; we never
        keyword-prefilter. To bound per-call cost for very large pools we
        chunk by :attr:`_matcher_max_candidates` (preserving on-disk order)
        and union the matches. The common case (pool <= cap) is one batch.
        """
        cap = self._matcher_max_candidates
        if len(summaries) <= cap:
            return [summaries]
        return [summaries[i:i + cap] for i in range(0, len(summaries), cap)]

    def role_for(self, skill: "Skill") -> str:
        """Role bucket of a loaded skill (its on-disk subdir, else general)."""
        return role_of_path(skill.path, self.skills_dir)

    def _keyword_fallback(
        self,
        task_description: str,
        *,
        summaries: list[dict] | None = None,
        min_score: int = 2,
        limit: int = 3,
    ) -> list[Skill]:
        """Best-effort keyword-overlap match. Used when the matcher
        subprocess raises (e.g., codex not installed in tests). Returns
        skills with strong token overlap; empty list if nothing scores
        above ``min_score``. ``summaries`` is the already role-scoped pool
        when called from :meth:`find_relevant`, so the fallback respects
        the same scoping as the LLM matcher."""
        if summaries is None:
            summaries = self.list_summaries()
        if not summaries:
            return []
        scored = [
            (self._score_summary(task_description, s), idx, s)
            for idx, s in enumerate(summaries)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        picks: list[Skill] = []
        for score, _, summary in scored[:limit]:
            if score < min_score:
                break
            try:
                picks.append(self.load(str(Path(summary["path"]))))
            except (OSError, KeyError):
                continue
        return picks

    @staticmethod
    def _extract_matched_names(response: str) -> tuple[list[str], bool]:
        """Parse the matcher LLM's JSON output. Accepts only ``high`` fits.

        ``low`` and ``medium`` are dropped: a borderline match steers the
        engineer down the wrong sub-domain, which is worse than no match
        (the latter just triggers a distill). Bare-string entries are
        treated as ``high`` for back-compat.
        """
        candidates = [response.strip()]
        if response.strip().startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.DOTALL)
            candidates.append(stripped.strip())
        candidates.extend(re.findall(r"\{[\s\S]*?\}", response))

        for candidate in candidates:
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            matched = payload.get("matched")
            if not isinstance(matched, list):
                continue
            high_names: list[str] = []
            for item in matched:
                if isinstance(item, str):
                    name = item.strip()
                    if name:
                        high_names.append(name)
                elif isinstance(item, dict):
                    fit = str(item.get("fit", "high")).strip().lower()
                    name = str(item.get("name", "")).strip()
                    if not name:
                        continue
                    if fit == "high" or not fit:
                        high_names.append(name)
            return high_names, True
        return [], False
