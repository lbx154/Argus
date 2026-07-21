"""On-disk skill store and LLM matcher.

Provenance: vendored from ``skill-agent/skill_agent/skill_store.py``. The
key refactor: ``find_relevant`` no longer imports ``codex_exec`` directly.
It now takes a ``RunnerBackend`` (and the model name to use) explicitly,
so the same store works against codex, claude-code, or the test stub.

Skills are markdown files with a YAML-style frontmatter block. ``SkillStore``
persists version/use metadata, retains prior versions under ``_history/``, and
asks a small model to pick the best match for a task. There is NO
lexical/keyword fallback: on matcher failure or an unusable matcher response we
surface the failure and return ``None`` so the caller takes the full expensive
path rather than silently guessing a match.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..core.event_catalog import EventType
from ..core.models import RunnerOptions, RunnerResult
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from .skill_prompts import Prompts

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
    "manager": frozenset({"manager"}),
}
# Cross-role *reference* pools. A role's matcher ALSO considers these
# subdirs, but their skills are surfaced as read-only "other-role
# perspective" references — never as the role's own primary playbook, and
# never eligible for skill writeback. This lets the engineer anticipate the
# reviewer's rubric, the reviewer understand the engineer's playbook, and
# the planner see both, without blurring role identity. The manager, which
# divides the task and owns stage/skill-approval decisions, sees every other
# role's standards as references. See ``role_match.partition_by_role``.
ROLE_CROSS_READ_POOLS: dict[str, frozenset[str]] = {
    "engineer": frozenset({"reviewer"}),
    "reviewer": frozenset({"engineer"}),
    "planner": frozenset({"engineer", "reviewer"}),
    "manager": frozenset({"engineer", "reviewer", "planner"}),
}
_ROLE_SUBDIRS = frozenset({"engineer", "reviewer", "planner", "manager"})


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
_INLINE_BODY_MAX_CHARS_ENV = "ARGUS_SKILL_INLINE_BODY_MAX_CHARS"
_DEFAULT_INLINE_BODY_MAX_CHARS = 12_000
_DEFAULT_INLINE_EXCERPT_CHARS = 5_000


def task_fingerprint(task_desc: str) -> str:
    normalized = " ".join((task_desc or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]

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
    created_at: str = ""
    task_history: list[str] = field(default_factory=list)
    path: str = ""
    skill_id: str = ""
    created_for_task: str = ""
    successful_reuses: int = 0
    failed_reuses: int = 0
    reuse_fingerprints: list[str] = field(default_factory=list)
    # Protected = a GOVERNING skill (a vertical's seed skill, an anti-cheat /
    # guardrail playbook, a role-identity skill) that a self-modifying mission
    # must not be able to remove or blindly overwrite. SkillRouter refuses to
    # archive/delete OR update a protected skill at runtime — strengthening one
    # requires an explicit, out-of-band source-code change instead (never the
    # cheap overwrite path). Absent in legacy frontmatter -> ``False`` (an
    # ordinary, freely-editable skill).
    protected: bool = False
    shared_base_digest: str = ""
    shared_base_version: int = 0

    def render(self) -> str:
        history = ""
        if self.task_history:
            items = "\n".join(
                f"  - {json.dumps(t, ensure_ascii=False)}"
                for t in self.task_history[-10:]
            )
            history = f"task_history:\n{items}\n"
        protected_lines = "protected: true\n" if self.protected else ""
        reuse_history = ""
        if self.reuse_fingerprints:
            reuse_history = "reuse_fingerprints:\n" + "".join(
                f"  - {json.dumps(value)}\n"
                for value in self.reuse_fingerprints[-TASK_HISTORY_MAX_ITEMS:]
            )
        return (
            f"---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"category: {self.category}\n"
            f"version: {self.version}\n"
            f"created_at: {self.created_at}\n"
            f"skill_id: {self.skill_id}\n"
            f"created_for_task: {self.created_for_task}\n"
            f"successful_reuses: {int(self.successful_reuses)}\n"
            f"failed_reuses: {int(self.failed_reuses)}\n"
            f"shared_base_digest: {self.shared_base_digest}\n"
            f"shared_base_version: {int(self.shared_base_version)}\n"
            f"{protected_lines}"
            f"{history}"
            f"{reuse_history}"
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
            m = re.search(
                rf"^{re.escape(key)}:[ \t]*(.*)$",
                fm,
                re.MULTILINE,
            )
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

        reuse_fingerprints: list[str] = []
        reuse_match = re.search(
            r"reuse_fingerprints:\s*\n((?:\s+-\s+.+\n?)+)", fm
        )
        if reuse_match:
            for match in re.finditer(r"-\s+(.+)", reuse_match.group(1)):
                raw_item = match.group(1).strip()
                try:
                    reuse_fingerprints.append(str(json.loads(raw_item)))
                except json.JSONDecodeError:
                    reuse_fingerprints.append(raw_item.strip('"'))

        def _get_int(key: str) -> int:
            try:
                return max(0, int(_get(key) or 0))
            except ValueError:
                return 0

        return cls(
            name=_get("name"),
            description=_get("description"),
            category=_get("category"),
            content=content,
            version=_parse_skill_version(_get("version")),
            created_at=_get("created_at"),
            task_history=history,
            path=path,
            skill_id=_get("skill_id"),
            created_for_task=_get("created_for_task"),
            successful_reuses=_get_int("successful_reuses"),
            failed_reuses=_get_int("failed_reuses"),
            shared_base_digest=_get("shared_base_digest"),
            shared_base_version=_get_int("shared_base_version"),
            reuse_fingerprints=reuse_fingerprints,
            protected=_get("protected").strip().strip('"').strip("'").lower()
            in {"true", "yes", "1"},
        )


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _render_progressive_skill(skill: Skill, content: str, *, limit: int) -> str:
    headings = [
        line.strip()
        for line in content.splitlines()
        if re.match(r"^#{1,4}\s+\S", line)
    ]
    heading_index = "\n".join(f"- {heading}" for heading in headings[:40])
    if len(headings) > 40:
        heading_index += f"\n- ... ({len(headings) - 40} more sections)"
    excerpt_limit = min(_DEFAULT_INLINE_EXCERPT_CHARS, max(1_500, limit // 2))
    excerpt = content[:excerpt_limit]
    if len(content) > excerpt_limit:
        boundary = excerpt.rfind("\n")
        if boundary >= excerpt_limit // 2:
            excerpt = excerpt[:boundary]
        excerpt = excerpt.rstrip() + "\n\n[core excerpt truncated]"
    source = str(skill.path or "").strip() or "<skill source unavailable>"
    return (
        "## Progressive skill disclosure\n"
        f"Matched skill: **{skill.name}**\n"
        f"Source: `{source}`\n"
        f"Full body: {len(content)} chars; inline budget: {limit} chars.\n\n"
        "This prompt contains only the entrypoint. Before applying detailed "
        "procedures, read the source file and load only the sections relevant to "
        "the current task. Do not infer omitted rules from this excerpt.\n\n"
        "### Section index\n"
        f"{heading_index or '- (no Markdown headings found)'}\n\n"
        "### Core excerpt\n"
        f"{excerpt}"
    )


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
        self._last_match_premium_requests = 0.0
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
                part.startswith(".")
                or part in {"_archive", "_history", "_shared_verticals"}
                for part in rel.parts
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
                "protected": skill.protected,
                "version": skill.version,
                "skill_id": skill.skill_id,
                "candidate_id": (
                    skill.skill_id
                    or f"{skill_role}:{skill.name.casefold()}"
                ),
                "successful_reuses": skill.successful_reuses,
                "failed_reuses": skill.failed_reuses,
                "shared_base_digest": skill.shared_base_digest,
                "shared_base_version": skill.shared_base_version,
            }
            self._summary_cache[key] = (st.st_mtime_ns, st.st_size, summary)
            summaries.append(summary)
        if len(self._summary_cache) != len(seen):
            for stale in list(self._summary_cache.keys() - seen):
                self._summary_cache.pop(stale, None)
        # Legacy/source races created numbered files with identical display
        # names. Never hand an ambiguous name set to the matcher (whose JSON
        # response names skills, not paths). Keep one deterministic,
        # evidence-favoured representative per role+name until cleanup archives
        # the redundant files.
        selected: dict[tuple[str, str], dict] = {}

        def _rank(summary: dict) -> tuple[int, int, int, int, int, str]:
            filename = Path(str(summary.get("path") or "")).stem
            numbered = bool(re.search(r"-\d+$", filename))
            return (
                int(bool(summary.get("protected"))),
                int(summary.get("successful_reuses") or 0),
                len(summary.get("task_history") or []),
                int(summary.get("version") or 1),
                int(not numbered),
                str(summary.get("path") or ""),
            )

        for summary in summaries:
            key = (
                str(summary.get("role") or "general"),
                str(summary.get("name") or "").casefold(),
            )
            current = selected.get(key)
            if current is None or _rank(summary) > _rank(current):
                selected[key] = summary
        return sorted(selected.values(), key=lambda item: str(item.get("path") or ""))

    def load(self, path: str) -> Skill:
        text = Path(path).read_text(encoding="utf-8")
        return Skill.parse(text, path)

    def save(self, skill: Skill) -> Path:
        import os
        import threading
        import uuid
        if not skill.skill_id:
            skill.skill_id = uuid.uuid4().hex
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

    def render_skill(self, skill: Skill, *, full: bool = False) -> str:
        """Render a skill for prompt injection.

        Small skills are injected in full. Large skills use progressive
        disclosure: the prompt receives a compact entrypoint, source path,
        section index, and core excerpt; the agent reads only relevant source
        sections with file tools. ``full=True`` is reserved for internal
        operations that genuinely require the whole playbook.
        """
        content = skill.content.strip()
        if full:
            return content
        try:
            limit = int(
                os.environ.get(
                    _INLINE_BODY_MAX_CHARS_ENV,
                    str(_DEFAULT_INLINE_BODY_MAX_CHARS),
                )
                or _DEFAULT_INLINE_BODY_MAX_CHARS
            )
        except ValueError:
            limit = _DEFAULT_INLINE_BODY_MAX_CHARS
        if limit <= 0 or len(content) <= limit:
            return content
        return _render_progressive_skill(skill, content, limit=limit)

    def save_distilled(
        self,
        *,
        task_description: str,
        raw_distill_output: str,
        on_event: "Callable[[dict], None] | None" = None,
    ) -> "Skill | None":
        """Parse the reviewer-authored skill markdown and persist it.

        A structurally valid skill is immediately active in this store. Quality
        evolves from real task trajectories and reviewer-authored update/archive
        ops; there is no candidate or promotion state.
        """
        name, description, category, content = Prompts.parse_skill_output(raw_distill_output)
        if not (content or "").strip():
            if on_event is not None:
                try:
                    on_event({
                        "type": EventType.SKILL_DISTILL_REJECTED,
                        "reason": "empty_content",
                        "text": "skill proposal had empty content",
                    })
                except Exception:  # noqa: BLE001
                    log.debug("skill.distill.rejected emit failed", exc_info=True)
            return None
        skill = Skill(
            name=name or "unnamed-skill",
            description=description,
            category=category,
            content=content,
            version=1,
            created_at=datetime.now(timezone.utc).isoformat(),
            task_history=[],
            skill_id=uuid.uuid4().hex,
            created_for_task=task_fingerprint(task_description),
        )
        append_task_history(skill, task_description)
        if any(
            str(summary.get("name") or "").casefold() == skill.name.casefold()
            for summary in self.list_summaries()
        ):
            if on_event is not None:
                on_event({
                    "type": EventType.SKILL_DISTILL_REJECTED,
                    "name": skill.name,
                    "reason": "duplicate_name",
                    "text": f"skill name already exists: {skill.name}",
                })
            return None
        self.save(skill)
        return skill

    def update_skill_content(
        self,
        skill: Skill,
        new_markdown: str,
        *,
        task_desc: str = "",
        on_event: "Callable[[dict], None] | None" = None,
    ) -> "Skill | None":
        """Replace a skill body and retain the previous version for rollback."""
        if not skill.path:
            return None
        _name, description, _category, content = Prompts.parse_skill_output(new_markdown)
        content = content if (content or "").strip() else (new_markdown or "")
        if not content.strip():
            return None
        snapshot = self._version_snapshot_path(skill)
        if snapshot is None:
            return None
        try:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if not snapshot.exists():
                snapshot.write_text(skill.render(), encoding="utf-8")
        except OSError as snap_exc:
            log.warning(
                "update: cannot preserve skill version (%s); aborting", snap_exc
            )
            return None
        self.update_skill(skill, content, task_desc)
        if description.strip():
            skill.description = description.strip()
        self.save(skill)
        if on_event is not None:
            on_event({
                "type": EventType.SKILL_REVISED,
                "skill_id": skill.skill_id,
                "skill": skill.name,
                "version": skill.version,
                "path": str(skill.path or ""),
                "previous_version_path": str(snapshot),
                "text": f"{skill.name} → v{skill.version} (reviewer update)",
            })
        return skill

    def archive(self, skill: Skill) -> "Path | None":
        """Retire a skill (move it to ``skills/_archive/``) and clear caches.
        The reviewer's direct authority to remove a wrong/harmful playbook.
        Returns the archived path, or ``None`` when nothing was moved."""
        if not skill.path:
            return None
        from .lifecycle import archive_skill  # local import: avoid cycle
        archived = archive_skill(skill.path)
        self._summary_cache.pop(str(skill.path), None)
        self._match_cache.clear()
        return archived

    # ------------------------------------------------------------------
    # Version/use history — skills are active immediately; real reviewed uses
    # supply evidence for later update/archive decisions.
    # ------------------------------------------------------------------

    def _version_snapshot_path(self, skill: Skill) -> "Path | None":
        if not skill.path:
            return None
        p = Path(skill.path)
        stable_id = skill.skill_id or _slugify(skill.name) or p.stem
        return p.parent / "_history" / stable_id / f"v{skill.version}.md"

    def record_reuse(
        self,
        skill: Skill,
        *,
        task_desc: str,
        success: bool,
        on_event: "Callable[[dict], None] | None" = None,
    ) -> str:
        """Record one distinct reviewed use of ``skill`` for evolution evidence."""
        if not skill.path:
            return "noop"
        fingerprint = task_fingerprint(task_desc)
        if not fingerprint:
            return "noop"
        if fingerprint in skill.reuse_fingerprints:
            return "duplicate"
        skill.reuse_fingerprints.append(fingerprint)
        skill.reuse_fingerprints = skill.reuse_fingerprints[-TASK_HISTORY_MAX_ITEMS:]
        append_task_history(skill, task_desc)
        if success:
            skill.successful_reuses += 1
        else:
            skill.failed_reuses += 1
        self.save(skill)
        if on_event is not None:
            on_event({
                "type": EventType.SKILL_USE_RECORDED,
                "skill_id": skill.skill_id,
                "skill_name": skill.name,
                "task_fingerprint": fingerprint,
                "success": bool(success),
                "successful_uses": skill.successful_reuses,
                "failed_uses": skill.failed_reuses,
                "text": f"recorded {'successful' if success else 'ineffective'} use of {skill.name}",
            })
        return "recorded"


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
        force_empty_match: bool = False,
    ) -> tuple[list[Skill] | None, int]:
        """Use small model to judge which skills are relevant to the task.

        Returns ``(matched_skills | None, tokens_used)``.

        ``None`` means "matcher said no high-fit skill, OR something
        broke and the caller should distill instead".

        ``role`` scopes the candidate pool to that role's skills (see
        :data:`ROLE_SKILL_POOLS`); ``None`` matches the whole corpus.
        ``exclude_files`` drops skills by on-disk filename (e.g. long role
        policies already covered by a compact fixed prompt).
        """
        summaries = self._scope_summaries(
            self.list_summaries(), role=role, exclude_files=exclude_files
        )
        if not summaries:
            self._last_match_input_tokens = 0
            self._last_match_cached_input_tokens = 0
            self._last_match_output_tokens = 0
            self._last_match_premium_requests = 0.0
            if on_event:
                msg = (
                    "skill store empty - will distill a new playbook"
                    if role is None
                    else f"no skills in scope for role={role}"
                )
                on_event({"type": "match.info", "text": msg})
            if not force_empty_match:
                return None, 0

        cache_key = (
            " ".join(task_description.lower().split()),
            role or "",
            self._fingerprint_summaries(summaries),
        )
        cached = self._match_cache.get(cache_key)
        if cached is not None or cache_key in self._match_cache:
            # Refresh LRU order and report zero new usage for a cache hit.
            self._match_cache.pop(cache_key, None)
            self._match_cache[cache_key] = cached
            self._last_match_input_tokens = 0
            self._last_match_cached_input_tokens = 0
            self._last_match_output_tokens = 0
            self._last_match_premium_requests = 0.0
            if on_event:
                label = (
                    ", ".join(Path(path).stem for path in cached)
                    if cached
                    else "no match"
                )
                on_event({
                    "type": "match.info",
                    "text": f"matcher cache hit: {label} (0 tok)",
                })
            if cached is None:
                return None, 0
            try:
                return [self.load(path) for path in cached], 0
            except OSError:
                # A skill changed or disappeared after caching; rematch safely.
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
            self._last_match_premium_requests = 0.0
            return None, 0

        # Pure-LLM matching: the model judges EVERY in-scope candidate (no
        # keyword pre-filter). Large pools are split into deterministic
        # batches and the matches unioned, so cost is bounded without ever
        # silently dropping a candidate.
        #
        # Optional BM25 prefilter: when the pool is large enough that the
        # matcher prompt would crowd the small-router context window, we
        # cheaply prune to top-K candidates first. Threshold is env-tunable
        # via ``ARGUS_SKILL_BM25_PREFILTER_THRESHOLD`` (default 40), so it is
        # now ACTIVE for the real role pools (~75-80) — narrowing to top-K=30
        # before the LLM matcher, which still makes the final relevance call —
        # while small bootstrap/test stores (<40) stay LLM-only. See
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
        batches = self._candidate_batches(summaries) or [[]]
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
        premium_requests = 0.0
        for batch in batches:
            prompt = Prompts.skill_match(
                task_description,
                batch,
                requesting_role=role,
                primary_pool=(
                    ROLE_SKILL_POOLS.get(role, frozenset()) if role else frozenset()
                ),
            )
            try:
                result: RunnerResult = gateway_run_exec(
                    self.runner,
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
            except Exception as exc:  # noqa: BLE001 — matcher failure must not guess
                log.error("skill matcher subprocess raised: %s", exc)
                if on_event:
                    on_event(
                        {
                            "type": "match.error",
                            "text": (
                                f"matcher subprocess raised: {exc} — "
                                "treating as no match"
                            ),
                        }
                    )
                self._last_match_input_tokens = in_tok
                self._last_match_cached_input_tokens = cached_tok
                self._last_match_output_tokens = out_tok
                self._last_match_premium_requests = premium_requests
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
                    on_event(
                        {
                            "type": "match.error",
                            "text": (
                                f"matcher subprocess failed: {err}"
                                + (f" — {stderr_tail}" if stderr_tail else "")
                                + " — treating as no match"
                            ),
                        }
                    )
                self._last_match_input_tokens = in_tok
                self._last_match_cached_input_tokens = cached_tok
                self._last_match_output_tokens = out_tok
                self._last_match_premium_requests = premium_requests
                self._cache_match(cache_key, [])
                return None, 0
            in_tok += int(getattr(result, "input_tokens", 0) or 0)
            cached_tok += int(getattr(result, "cached_input_tokens", 0) or 0)
            out_tok += int(getattr(result, "output_tokens", 0) or 0)
            premium_requests += float(
                getattr(result, "premium_requests", 0.0) or 0.0
            )
            for sk in self._parse_match_response(result.message, batch):
                matched_by_path.setdefault(sk.path, sk)

        total_tokens = in_tok + out_tok
        self._last_match_input_tokens = in_tok
        self._last_match_cached_input_tokens = cached_tok
        self._last_match_output_tokens = out_tok
        self._last_match_premium_requests = premium_requests
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

    @property
    def last_match_premium_requests(self) -> float:
        return self._last_match_premium_requests

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
        id_to_path = {
            str(
                s.get("candidate_id")
                or s.get("skill_id")
                or f"{s.get('role', 'general')}:{s['name'].casefold()}"
            ): s["path"]
            for s in summaries
        }
        name_to_paths: dict[str, list[str]] = {}
        for summary in summaries:
            name_to_paths.setdefault(summary["name"].casefold(), []).append(
                summary["path"]
            )
        response_lower = response.lower()

        if "none" in response_lower and "no relevant" in response_lower:
            return []

        parsed_names, json_parsed = self._extract_matched_names(response)
        for parsed_name in parsed_names:
            path = id_to_path.get(parsed_name)
            if path is None:
                paths = name_to_paths.get(parsed_name.casefold(), [])
                path = paths[0] if len(paths) == 1 else None
            if path:
                matched.append(self.load(path))

        if matched:
            return matched

        if json_parsed:
            return matched

        unique_names = {
            name for name, paths in name_to_paths.items() if len(paths) == 1
        }
        for summary in summaries:
            if summary["name"].casefold() not in unique_names:
                continue
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
                    name = str(
                        item.get("id") or item.get("name") or ""
                    ).strip()
                    if not name:
                        continue
                    if fit == "high" or not fit:
                        high_names.append(name)
            return high_names, True
        return [], False


def shared_skill_digest(skill: Skill) -> str:
    """Stable digest of the canonical shared Skill payload."""
    payload = {
        "skill_id": str(skill.skill_id or ""),
        "name": str(skill.name or ""),
        "description": str(skill.description or ""),
        "category": str(skill.category or ""),
        "content": str(skill.content or ""),
        "version": int(skill.version or 1),
        "protected": bool(skill.protected),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def deterministic_skill_id(skill: Skill, *, role: str = "general") -> str:
    """Stable migration identity for a legacy shared Skill without an ID."""
    payload = "\0".join([
        role,
        str(skill.name or "").casefold(),
        str(skill.category or "").casefold(),
        str(skill.created_at or ""),
        str(skill.content or ""),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
