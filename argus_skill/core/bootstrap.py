"""Deterministic project-bootstrap preflight helpers.

These helpers classify a project root before the daemon/planner starts
working. The check is intentionally narrow: it only flags a root as
uninitialized when the top-level tree lacks a git repo marker, a build
manifest, any README, any source files, and any seeded research
bootstrap artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..life.research_profile import load_research_profile

__all__ = [
    "BootstrapPreflight",
    "inspect_project_bootstrap",
    "structured_research_bootstrap_requested",
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

_RESEARCH_BOOTSTRAP_ARTIFACTS = (
    "research/PIPELINE_STATE.json",
    "research/RESEARCH_BRIEF.md",
    "research/EXPERIMENT_PLAN.md",
    "research/CLAIMS_TO_TEST.md",
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


def structured_research_bootstrap_requested(project_root: Path) -> bool:
    """Whether explicit project state calls for the research scaffold."""
    if load_research_profile() is not None:
        return True
    try:
        from ..manager import Manager
        from ..skills.vertical_select import _persisted_vertical

        vertical = _persisted_vertical(Path(project_root).expanduser())
        return bool(vertical and Manager._kind_for(vertical) == "research")
    except Exception:  # noqa: BLE001 — absence/corruption means no bootstrap
        return False


def inspect_project_bootstrap(
    project_root: Path,
    *,
    objective_hint: str = "",
    research_requested: bool | None = None,
) -> BootstrapPreflight:
    """Classify ``project_root`` for an empty-project bootstrap task.

    ``objective_hint`` is accepted for caller compatibility but intentionally
    ignored: the harness must not infer mission type from objective prose.
    Automatic bootstrap is limited to a structured research signal: either an
    explicitly configured research profile or ``research_requested=True`` from
    a caller that will gate the candidate on the Manager's persisted vertical.
    All other empty workspaces belong to the selected agent vertical; the
    harness must not force them into a Python-package shape.
    """
    del objective_hint
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
    wants_research = (
        _should_bootstrap_research(research_profile)
        if research_requested is None
        else bool(research_requested)
    )
    generic_empty = not has_git and not build_manifests and not readmes and not source_files
    research_incomplete = bool(research_missing_artifacts)
    should_bootstrap = wants_research and research_incomplete
    if should_bootstrap:
        bootstrap_objective = _research_bootstrap_objective(
            root,
            profile_name=research_profile.name if research_profile is not None else "",
            missing_artifacts=research_missing_artifacts,
        )
        missing_artifacts.extend(research_missing_artifacts)
        prefix = (
            "uninitialized project root detected; missing .git, build manifest, "
            "README*, and source files; "
            if generic_empty
            else "research scaffold incomplete; "
        )
        event_text = (
            prefix
            + "research bootstrap requested; missing research artifacts: "
            + ", ".join(research_missing_artifacts)
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


def _should_bootstrap_research(profile: object | None) -> bool:
    """Whether to seed a research scaffold instead of a generic project.

    Driven SOLELY by the structured research profile (``load_research_profile``).
    The harness must not sniff the objective text for keywords like "emnlp" /
    "auto-research" to guess the mission type — that is the agent's domain, and
    keyword-matching the objective is exactly the harness-overreach this project
    has been removing. If an operator wants a research scaffold, they configure a
    research profile; otherwise an empty root gets the generic bootstrap.
    """
    return profile is not None


def _research_bootstrap_objective(
    root: Path,
    *,
    profile_name: str = "",
    missing_artifacts: tuple[str, ...] = _RESEARCH_BOOTSTRAP_ARTIFACTS,
) -> str:
    profile_note = (
        f" for the active research profile `{profile_name}`"
        if profile_name
        else ""
    )
    missing = ", ".join(f"`{path}`" for path in missing_artifacts) or "(none)"
    git_step = (
        "initialize git with `git init`, then "
        if not (root / ".git").exists()
        else ""
    )
    state_rule = (
        " Before touching `research/PIPELINE_STATE.json`, re-check it at execution "
        "time because the Manager may create it after this mission is queued. If it "
        "exists, treat it as read-only and never rewrite `vertical`, "
        "`workflow_mode`, `target_venue`, `current_stage`, or any stage status."
    )
    return (
        "Seed a research bootstrap mission"
        f"{profile_note} for this project root: {git_step}create only these "
        f"missing starting-ledger artifacts: {missing}.{state_rule} Do not "
        "regenerate or normalize existing artifacts."
    )
