"""Path-only layered Skill libraries.

Project, workflow/domain, and global libraries remain separate filesystem roots.
Agents receive those roots and search them directly; the runtime does not parse,
match, rank, copy, or rewrite Skill documents.
"""
from __future__ import annotations

from pathlib import Path

from .store import Skill, SkillStore, role_of_path

LAYER_PROJECT = "project"
LAYER_VERTICAL = "vertical"
LAYER_GLOBAL = "global"
_SHARED_VERTICALS_DIR = "_shared_verticals"


def shared_skill_scope_dir(global_dir: Path, scope: str) -> Path | None:
    """Resolve an explicitly authored semantic scope below the shared root."""
    value = str(scope or "").strip().strip("/")
    if not value or value.startswith(".") or ".." in Path(value).parts:
        return None
    return Path(global_dir) / _SHARED_VERTICALS_DIR / value


class LayeredSkillStore:
    """Expose ordered Skill-library roots without interpreting their files."""

    def __init__(
        self,
        *,
        project_dir: Path,
        global_dir: Path,
        vertical_dir: Path | None = None,
        native_project_dir: Path | None = None,
    ) -> None:
        self.project = SkillStore(Path(project_dir))
        self.global_ = SkillStore(Path(global_dir))
        self.vertical = SkillStore(Path(vertical_dir)) if vertical_dir else None
        native_project_path = (
            Path(native_project_dir) if native_project_dir is not None else None
        )
        # Unlike managed stores, an Agent Skills root is discovery-only: never
        # create it, and preserve the old root set when the directory is absent.
        self._native_project_root = (
            native_project_path.resolve()
            if native_project_path is not None and native_project_path.is_dir()
            else None
        )
        self._project_root = self.project.skills_dir.resolve()
        self._global_root = self.global_.skills_dir.resolve()
        self._vertical_root = (
            self.vertical.skills_dir.resolve() if self.vertical is not None else None
        )

    @property
    def skills_dir(self) -> Path:
        return self.project.skills_dir

    def native_project_roots(self) -> list[Path]:
        """Return unscoped project roots for an Agent Skills native loader."""
        return (
            [self._native_project_root]
            if self._native_project_root is not None
            else []
        )

    def library_roots(self) -> list[Path]:
        roots = self.native_project_roots()
        for root in (self._project_root, self._vertical_root, self._global_root):
            if root is not None and root not in roots:
                roots.append(root)
        return roots

    def layer_for_path(self, path: str | Path) -> str | None:
        candidate = Path(path).resolve()
        for layer, root in (
            (LAYER_PROJECT, self._project_root),
            (LAYER_VERTICAL, self._vertical_root),
            (LAYER_GLOBAL, self._global_root),
        ):
            if root is None:
                continue
            try:
                candidate.relative_to(root)
                return layer
            except ValueError:
                continue
        return None

    def layer_for_skill(self, skill: Skill) -> str:
        return self.layer_for_path(skill.path) or LAYER_PROJECT

    def store_for_layer(self, layer: str) -> SkillStore:
        if layer == LAYER_PROJECT:
            return self.project
        if layer == LAYER_VERTICAL and self.vertical is not None:
            return self.vertical
        if layer == LAYER_GLOBAL:
            return self.global_
        raise ValueError(f"unknown Skill layer: {layer}")

    def role_for(self, skill: Skill) -> str:
        layer = self.layer_for_skill(skill)
        return role_of_path(skill.path, self.store_for_layer(layer).skills_dir)

    def list_paths(self) -> list[str]:
        paths: list[str] = []
        for root in self.library_roots():
            paths.extend(SkillStore(root).list_paths())
        return paths

    def list_summaries(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for layer in (LAYER_PROJECT, LAYER_VERTICAL, LAYER_GLOBAL):
            if layer == LAYER_VERTICAL and self.vertical is None:
                continue
            for row in self.store_for_layer(layer).list_summaries():
                rows.append({**row, "layer": layer})
        return rows

    def _is_native_project_path(self, path: str | Path) -> bool:
        if self._native_project_root is None:
            return False
        try:
            Path(path).resolve().relative_to(self._native_project_root)
            return True
        except ValueError:
            return False

    def save(self, skill: Skill) -> Path:
        # New writes are project-local unless their explicit path already points
        # into another configured root. Agent Skills roots remain read-only.
        if skill.path and self._is_native_project_path(skill.path):
            raise PermissionError("native project Skills are read-only")
        layer = self.layer_for_path(skill.path) if skill.path else LAYER_PROJECT
        return self.store_for_layer(layer or LAYER_PROJECT).save(skill)

    def archive_path(self, path: str | Path) -> Path:
        if self._is_native_project_path(path):
            raise PermissionError("native project Skills are read-only")
        layer = self.layer_for_path(path)
        if layer != LAYER_PROJECT:
            raise PermissionError("only project-local Skills may be archived here")
        return self.project.archive_path(path)

    # Old matcher/mutation entry points intentionally do not exist.  Agents edit
    # the project library directly using the paths supplied in their prompts.


__all__ = [
    "LAYER_GLOBAL",
    "LAYER_PROJECT",
    "LAYER_VERTICAL",
    "LayeredSkillStore",
    "shared_skill_scope_dir",
]
