"""Two-layer skill store: project (writable default) + global (read mostly).

Phase 2 (p2-layered-skills) of the unified-argus-skill refactor: the
single-directory :class:`~argus_skill.skills.store.SkillStore` is no
longer enough. Each project gets its own
``~/.argus-skill/projects/<fp>/skills/`` directory that holds its
proven playbooks; the user-global library lives at
``~/.argus-skill/skills/`` and is shared across all projects.

Design choices
--------------
* **Composition over inheritance.** A :class:`LayeredSkillStore`
  wraps two real :class:`SkillStore` instances. All on-disk concerns
  (atomic writes, frontmatter caching, slugging) stay in the
  underlying store — we only add merge / dispatch logic.
* **Project shadows global.** When a project skill and a global
  skill share the same name, the project version wins for matching
  purposes. (We never silently delete the global copy; the user can
  still see it via ``layer_summaries("global")``.)
* **Writes default to project.** :meth:`save`,
  :meth:`save_distilled`, and :meth:`update_skill` write into the
  project layer unless the skill's ``path`` already lives inside the
  global tree. This matches the unified UX: most lessons are
  project-specific; promotion to global is an explicit act.
* **Promotion is explicit.** :meth:`promote_to_global` copies a
  project skill into the global layer (rewriting ``skill.path``)
  and removes the project copy if requested. The opposite direction
  is not supported — global is never auto-demoted.
* **Backwards-compatible surface.** LayeredSkillStore implements the
  same public methods used by :class:`~argus_skill.loop.SkillLoop`
  and :mod:`argus_skill.adapters.skill_loop_runner`, plus a small
  number of layer-aware additions (``promote_to_global``,
  ``layer_summaries``, ``layer_for_skill``).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ..core.ports import RunnerBackend
from .store import Skill, SkillStore, _slugify

log = logging.getLogger(__name__)

LAYER_PROJECT = "project"
LAYER_GLOBAL = "global"


class LayeredSkillStore:
    """Two-layer view over a project skills dir + a global skills dir.

    Implements the :class:`~argus_skill.core.ports.SkillSource`
    protocol so it can drop into ``SkillLoop`` / mission engine
    without further plumbing.
    """

    def __init__(
        self,
        *,
        project_dir: Path,
        global_dir: Path,
        runner: RunnerBackend | None = None,
        matcher_model: str = "",
        matcher_reasoning_effort: str | None = None,
    ) -> None:
        self.project = SkillStore(
            Path(project_dir),
            runner=runner,
            matcher_model=matcher_model,
            matcher_reasoning_effort=matcher_reasoning_effort,
        )
        self.global_ = SkillStore(
            Path(global_dir),
            runner=runner,
            matcher_model=matcher_model,
            matcher_reasoning_effort=matcher_reasoning_effort,
        )
        # Resolve once so layer dispatch never gets confused by relative
        # vs absolute paths or by symlinks introduced after init.
        self._project_root = self.project.skills_dir.resolve()
        self._global_root = self.global_.skills_dir.resolve()

    # ------------------------------------------------------------------
    # Layer awareness
    # ------------------------------------------------------------------

    def layer_for_path(self, path: str | os.PathLike[str]) -> str | None:
        """Return ``"project"`` / ``"global"`` / ``None`` for ``path``.

        Resolution is path-prefix based on the resolved skills_dir of
        each layer, which is robust to ``../`` and symlinks.
        """
        try:
            p = Path(path).resolve()
        except OSError:
            return None
        try:
            p.relative_to(self._project_root)
            return LAYER_PROJECT
        except ValueError:
            pass
        try:
            p.relative_to(self._global_root)
            return LAYER_GLOBAL
        except ValueError:
            return None

    def layer_for_skill(self, skill: Skill) -> str:
        """Return the layer a skill currently belongs to.

        Defaults to ``"project"`` when a skill has no path yet — the
        write side will create it under the project tree.
        """
        return self.layer_for_path(skill.path) or LAYER_PROJECT

    def store_for_layer(self, layer: str) -> SkillStore:
        if layer == LAYER_PROJECT:
            return self.project
        if layer == LAYER_GLOBAL:
            return self.global_
        raise ValueError(f"unknown skill layer: {layer!r}")

    def store_for_skill(self, skill: Skill) -> SkillStore:
        return self.store_for_layer(self.layer_for_skill(skill))

    # ------------------------------------------------------------------
    # Listing — merged view, project shadows global by skill name
    # ------------------------------------------------------------------

    def layer_summaries(self, layer: str) -> list[dict]:
        store = self.store_for_layer(layer)
        return [{**s, "layer": layer} for s in store.list_summaries()]

    def list_summaries(self) -> list[dict]:
        """Return the merged summary list visible to the matcher.

        Project entries shadow global entries with the same
        case-insensitive ``name``. Each summary is annotated with a
        ``layer`` field so callers can distinguish them.
        """
        project_summaries = [
            {**s, "layer": LAYER_PROJECT} for s in self.project.list_summaries()
        ]
        global_summaries = [
            {**s, "layer": LAYER_GLOBAL} for s in self.global_.list_summaries()
        ]
        seen_names: set[str] = {
            (s.get("name") or "").casefold() for s in project_summaries
        }
        merged: list[dict] = list(project_summaries)
        for summary in global_summaries:
            if (summary.get("name") or "").casefold() in seen_names:
                continue
            merged.append(summary)
        return merged

    # ------------------------------------------------------------------
    # Loading — figure out which layer owns a path
    # ------------------------------------------------------------------

    def load(self, path: str) -> Skill:
        layer = self.layer_for_path(path)
        if layer is None:
            # Fall back to project; SkillStore.load just reads the
            # file regardless of where it lives.
            return self.project.load(path)
        return self.store_for_layer(layer).load(path)

    # ------------------------------------------------------------------
    # Writes — default to project, dispatch when layer is known
    # ------------------------------------------------------------------

    def save(self, skill: Skill) -> Path:
        return self.store_for_skill(skill).save(skill)

    def render_skill(self, skill: Skill) -> str:
        return self.project.render_skill(skill)

    def save_distilled(
        self,
        *,
        task_description: str,
        raw_distill_output: str,
        author_model: str,
        on_event: Callable[[dict], None] | None = None,
        enforce_quality_gate: bool = True,
    ) -> Skill | None:
        # ``enforce_quality_gate`` is accepted for backward compatibility but the
        # underlying store ignores it — skill quality is proven by EFFECT (a
        # candidate is kept only when a later round carrying it is effective), not
        # by judging the skill text. New skills always land in the project layer
        # first; promotion to global is an explicit operator action.
        return self.project.save_distilled(
            task_description=task_description,
            raw_distill_output=raw_distill_output,
            author_model=author_model,
            on_event=on_event,
            enforce_quality_gate=enforce_quality_gate,
        )

    def writeback_from_trajectory(
        self,
        *,
        skill: Skill,
        task_description: str,
        successful_trajectory: str,
        distiller: Any | None = None,
        author_model: str = "",
        revise: bool = False,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.store_for_skill(skill).writeback_from_trajectory(
            skill=skill,
            task_description=task_description,
            successful_trajectory=successful_trajectory,
            distiller=distiller,
            author_model=author_model,
            revise=revise,
            on_event=on_event,
        )

    def promote_lesson(
        self,
        *,
        skill: Skill,
        lesson_text: str,
        task_description: str,
        distiller: Any,
        author_model: str = "",
        on_event: Callable[[dict], None] | None = None,
    ) -> bool:
        return self.store_for_skill(skill).promote_lesson(
            skill=skill,
            lesson_text=lesson_text,
            task_description=task_description,
            distiller=distiller,
            author_model=author_model,
            on_event=on_event,
        )

    def update_skill(
        self, skill: Skill, new_content: str, task_desc: str
    ) -> Skill:
        return self.store_for_skill(skill).update_skill(
            skill, new_content, task_desc
        )

    # ------------------------------------------------------------------
    # Cross-layer promotion (project -> global)
    # ------------------------------------------------------------------

    def promote_to_global(
        self,
        skill: Skill,
        *,
        delete_project_copy: bool = True,
    ) -> Skill:
        """Move a project skill into the global library.

        Returns the same ``Skill`` instance with its ``path`` updated
        to the new global location. If a global skill with the same
        slug already exists we allocate a unique sibling name (handled
        by :meth:`SkillStore._build_skill_path`).
        """
        if self.layer_for_skill(skill) != LAYER_PROJECT:
            raise ValueError(
                f"promote_to_global: skill is not in project layer: {skill.path!r}"
            )
        old_path = Path(skill.path) if skill.path else None
        # Choose target path inside global layer.
        base = _slugify(skill.name) or "skill"
        candidate = self.global_.skills_dir / f"{base}.md"
        if candidate.exists():
            for idx in range(2, 1000):
                next_candidate = self.global_.skills_dir / f"{base}-{idx}.md"
                if not next_candidate.exists():
                    candidate = next_candidate
                    break
            else:  # pragma: no cover — pathological
                raise RuntimeError(
                    f"promote_to_global: cannot allocate global path for {skill.name!r}"
                )
        # Atomic write into global, then optionally drop the project copy.
        candidate.parent.mkdir(parents=True, exist_ok=True)
        tmp = candidate.with_name(
            f"{candidate.name}.tmp.{os.getpid()}.{threading.get_ident():x}."
            f"{uuid.uuid4().hex[:8]}"
        )
        skill.path = str(candidate)
        try:
            tmp.write_text(skill.render(), encoding="utf-8")
            os.replace(tmp, candidate)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if delete_project_copy and old_path is not None and old_path.exists():
            try:
                old_path.unlink()
            except OSError as exc:  # pragma: no cover — best-effort
                log.warning(
                    "promote_to_global: failed to remove project copy %s: %s",
                    old_path, exc,
                )
        return skill

    def import_global_skill_into_project(self, skill: Skill) -> Skill:
        """Copy a global skill into the project layer (project shadow).

        Useful when the user wants to fork a global playbook for
        project-specific tweaks without affecting the original. The
        global copy is left untouched. Returns the new project-layer
        :class:`Skill` (a fresh instance — the caller's ``skill``
        argument is not mutated so global references still work).
        """
        if self.layer_for_skill(skill) != LAYER_GLOBAL:
            raise ValueError(
                f"import_global_skill_into_project: skill is not in global layer: {skill.path!r}"
            )
        # Compute target path with collision avoidance.
        base = _slugify(skill.name) or "skill"
        candidate = self.project.skills_dir / f"{base}.md"
        if candidate.exists():
            for idx in range(2, 1000):
                next_candidate = self.project.skills_dir / f"{base}-{idx}.md"
                if not next_candidate.exists():
                    candidate = next_candidate
                    break
            else:  # pragma: no cover
                raise RuntimeError(
                    f"import_global: cannot allocate project path for {skill.name!r}"
                )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill.path, candidate)
        return self.project.load(str(candidate))

    # ------------------------------------------------------------------
    # Matcher — merged view, with layer-aware load on cache hit
    # ------------------------------------------------------------------

    def find_relevant(
        self,
        task_description: str,
        on_event: Callable[[dict], None] | None = None,
        *,
        role: str | None = None,
        exclude_files: set[str] | None = None,
    ) -> tuple[list[Skill] | None, int]:
        """Run the matcher across the merged (project + global) summaries.

        We delegate the actual matching pipeline to the underlying
        ``project`` store but feed it the merged summary set via a
        small monkey-patch context. That keeps the matcher prompt /
        cache logic in one place; ``LayeredSkillStore`` only owns
        the merge + dispatch.
        """
        # Build the merged view ourselves so we can match across layers.
        merged_summaries = self.list_summaries()
        if not merged_summaries:
            if on_event:
                on_event({
                    "type": "match.info",
                    "text": "skill store empty (project + global) — will distill a new playbook",
                })
            return None, 0

        # The underlying SkillStore.find_relevant uses self.list_summaries
        # internally. We borrow its full pipeline by temporarily swapping
        # in our merged view + a load() that dispatches across layers.
        ps = self.project
        original_list = ps.list_summaries
        original_load = ps.load

        def _layered_load(path: str) -> Skill:
            # Skip layered_load -> store_for_layer().load -> _layered_load
            # recursion by always calling SkillStore.load (the unbound
            # method) on the right concrete store.
            layer = self.layer_for_path(path)
            target = self.store_for_layer(layer) if layer is not None else self.project
            return SkillStore.load(target, path)

        ps_any = cast(Any, ps)
        ps_any.list_summaries = lambda: merged_summaries
        ps_any.load = _layered_load
        try:
            return ps.find_relevant(
                task_description,
                on_event=on_event,
                role=role,
                exclude_files=exclude_files,
            )
        finally:
            ps_any.list_summaries = original_list
            ps_any.load = original_load


__all__ = [
    "LayeredSkillStore",
    "LAYER_PROJECT",
    "LAYER_GLOBAL",
]
