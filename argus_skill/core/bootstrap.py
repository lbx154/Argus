"""Deterministic project-bootstrap preflight helpers.

These helpers classify a project root before the daemon/planner starts
working. The check is intentionally narrow: it only flags a root as
uninitialized when the top-level tree lacks a git repo marker, a build
manifest, any README, any source files, and any seeded research
bootstrap artifacts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..life.research_profile import load_research_profile

__all__ = [
    "BootstrapPreflight",
    "inspect_project_bootstrap",
]

_BUILD_MANIFESTS = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
)

_SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".hh",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
}

_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
}

_PACKAGE_SLUG_RE = re.compile(r"[^a-z0-9]+")
_RESEARCH_BOOTSTRAP_ARTIFACTS = (
    "research/PIPELINE_STATE.json",
    "research/RESEARCH_BRIEF.md",
    "research/EXPERIMENT_PLAN.md",
    "research/CLAIMS_TO_TEST.md",
    "research/GO_NO_GO.md",
    "experiments/BENCHMARK_PROVENANCE.md",
)


@dataclass(frozen=True)
class BootstrapPreflight:
    """Result of a project-root bootstrap scan."""

    project_root: Path
    has_git: bool
    build_manifests: tuple[str, ...]
    readmes: tuple[str, ...]
    source_files: tuple[str, ...]
    missing_artifacts: tuple[str, ...]
    should_bootstrap: bool
    bootstrap_objective: str
    event_text: str


def inspect_project_bootstrap(
    project_root: Path,
    *,
    objective_hint: str = "",
) -> BootstrapPreflight:
    """Classify ``project_root`` for an empty-project bootstrap task."""
    root = Path(project_root).expanduser()
    has_git = (root / ".git").exists()
    build_manifests = tuple(
        name for name in _BUILD_MANIFESTS if (root / name).exists()
    )
    readmes = tuple(
        path.name
        for path in sorted(root.glob("README*"))
        if path.is_file()
    )
    source_files = _find_source_files(root)
    research_artifacts = _find_research_artifacts(root)
    research_missing_artifacts = _find_missing_research_artifacts(root)
    missing_artifacts: list[str] = []
    if not has_git:
        missing_artifacts.append(".git")
    if not build_manifests:
        missing_artifacts.append("build manifest")
    if not readmes:
        missing_artifacts.append("README*")
    if not source_files:
        missing_artifacts.append("source files")

    bootstrap_objective = ""
    event_text = ""
    research_profile = load_research_profile()
    research_requested = _should_bootstrap_research(
        research_profile,
        objective_hint=objective_hint,
    )
    generic_empty = not has_git and not build_manifests and not readmes and not source_files
    research_incomplete = bool(research_missing_artifacts)
    should_bootstrap = (
        generic_empty
        and (
            (research_requested and research_incomplete)
            or (not research_requested and not research_artifacts)
        )
    )
    if should_bootstrap and research_requested and research_incomplete:
        bootstrap_objective = _research_bootstrap_objective(
            root,
            profile_name=research_profile.name if research_profile is not None else "",
        )
        missing_artifacts.extend(research_missing_artifacts)
        event_text = (
            "uninitialized project root detected; missing .git, build manifest, "
            "README*, and source files; research bootstrap requested; missing "
            "research artifacts: "
            + ", ".join(research_missing_artifacts)
        )
    elif should_bootstrap:
        package_slug = _package_slug(root.name)
        bootstrap_objective = (
            "Bootstrap this empty project root: initialize git with `git init`, "
            "create `pyproject.toml`, `README.md`, `tests/test_smoke.py`, and "
            f"`src/{package_slug}/__init__.py`, then add a minimal package entry "
            "pointing at that module."
        )
        event_text = (
            "uninitialized project root detected; missing .git, build manifest, "
            "README*, and source files"
        )

    return BootstrapPreflight(
        project_root=root,
        has_git=has_git,
        build_manifests=build_manifests,
        readmes=readmes,
        source_files=source_files,
        missing_artifacts=tuple(missing_artifacts),
        should_bootstrap=should_bootstrap,
        bootstrap_objective=bootstrap_objective,
        event_text=event_text,
    )


def _find_source_files(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _should_ignore(path, root):
            continue
        if path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        found.append(str(path.relative_to(root)))
    return tuple(found)


def _find_research_artifacts(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    if not root.exists():
        return ()
    for rel_path in _RESEARCH_BOOTSTRAP_ARTIFACTS:
        if (root / rel_path).exists():
            found.append(rel_path)
    return tuple(found)


def _find_missing_research_artifacts(root: Path) -> tuple[str, ...]:
    present = set(_find_research_artifacts(root))
    return tuple(rel_path for rel_path in _RESEARCH_BOOTSTRAP_ARTIFACTS if rel_path not in present)


def _should_ignore(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in _IGNORE_DIRS or part.startswith(".") for part in rel_parts[:-1])


def _package_slug(name: str) -> str:
    slug = _PACKAGE_SLUG_RE.sub("_", name.strip().lower()).strip("_")
    return slug or "project"


def _should_bootstrap_research(
    profile: object | None,
    *,
    objective_hint: str,
) -> bool:
    low = objective_hint.casefold()
    research_keywords = (
        "auto-research",
        "emnlp",
        "acl",
        "research bootstrap",
        "research mission",
        "research profile",
        "pipeline state",
        "experiment plan",
        "claims to test",
        "go/no-go",
    )
    if profile is not None:
        return True
    return any(keyword in low for keyword in research_keywords)


def _research_bootstrap_objective(root: Path, *, profile_name: str = "") -> str:
    profile_note = (
        f" for the active research profile `{profile_name}`"
        if profile_name
        else ""
    )
    return (
        "Seed a research bootstrap mission"
        f"{profile_note} for this empty project root: initialize git with "
        "`git init`, then create `research/PIPELINE_STATE.json`, "
        "`research/RESEARCH_BRIEF.md`, `research/EXPERIMENT_PLAN.md`, "
        "`research/CLAIMS_TO_TEST.md`, `research/GO_NO_GO.md`, and "
        "`experiments/BENCHMARK_PROVENANCE.md` as the starting auto-research "
        f"ledger for `{root.name}`."
    )
