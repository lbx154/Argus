"""Bundled default skills for new argus-skill homes.

The files under :mod:`argus_skill.builtin_skills` are argus-native
research/paper playbooks adapted from ARIS workflow concepts. They are
seeded into ``~/.argus-skill/skills`` on initialization so the agent can
start research and paper-writing missions before it has distilled its own
local skills.
"""
from __future__ import annotations

import hashlib
import os
import threading
import uuid
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Iterable

from .store import Skill

_BUILTIN_PACKAGE = "argus_skill.builtin_skills"
DEFAULT_PROJECT_BUILTIN_SKILLS_DIR = "argus_builtin_skills"
_VERTICAL_SKILL_INHERITANCE = {
    "digital_circuit_benchmark": ("digital_circuit",),
}
_SAFE_BUILTIN_UPGRADE_DIGESTS = {
    "agent-md-existing-project-optimization-template.md": {
        "99a442f23f397d712568e9ac16db6ec5c5c8d3639001083680561a397192d166",
    },
    "agent-md-new-project-template.md": {
        "34f4a486c681e3bd42e360b2d13ae64db3251380cd307e7a64268a12cf605e02",
        "7da2456956d5e6a99f0af6a64b9413ff926744806a376ae76d476418006f0a36",
    },
    "engineer/aaai-format-preflight.md": {
        "7a7ca6c7b65ec06bd2dc52527122a9321bf79195c92c1d51ddeb223c5b675a80",
    },
    "engineer/aaai-paper-drafting.md": {
        "bfc8f5ac876ddacbf298666b718cfd80364a0403c9befde4f583888ab97cfd96",
    },
    "engineer/aaai-paper-skill-router.md": {
        "1c007009d5edd8666b492ba7b3092c447cf030fef3069a8e8c9343291772ee14",
    },
    "engineer/argus-engineer-role.md": {
        "13480bb0c54ebea266197f8f78dcfe0e0802bedd5bb4fca56e8295fbd2a840ad",
        "8225b379f4e0069117e5666e6610dcefd0e3478b4ba3a207ad5feb8d43c52a96",
    },
    "engineer/auto-research-pipeline.md": {
        "2d480ab8e64a451201631a0bda7fe0a90f05d4e1183c74c4bba6fc7c22393a02",
    },
    "engineer/benchmark-paper-figure-checklist.md": {
        "26cf35588546954b0410d7000d588d8e0e29551907c25ad17847f06230d69779",
    },
    "engineer/emnlp-format-preflight.md": {
        "f0950f6be9f7c1b0467ee1203e6abbfe5fb9717a6bcf60213c85fa5c881cfd36",
    },
    "engineer/emnlp-paper-drafting.md": {
        "c78734508a03955d3a7fe26465b47f669c49947a43b02e1bc1fff14d3f2d0580",
    },
    "engineer/emnlp-paper-skill-router.md": {
        "8b5fe2aa69301225535893d5bb5f350bac0666b9b91d309321c1e28a88814b8f",
    },
    "engineer/figure-spec.md": {
        "3261a0c5f71d318bf212e0b485480503ccb1f30b278b9e07db756f5f2a942398",
        "e46107aac72e6e7b23ecf645492f786262a3e32f679f62500b8142e7e58b5629",
    },
    "engineer/paper-chart-styling.md": {
        "dccbf77624b4c5ca3fe3e5c39a1c9ac30274cbbcb7a65bf073e905da8ea5b9bb",
    },
    "engineer/paper-framework-figure-studio-pro.md": {
        "580e6b0d723413c2257e3fb0337d5174344d8d986598ee9d48adedd333d0c40b",
        "f5f6185ce3f4861f70f368ae89f3ff459fbe904bf4151747278401c53440ec9d",
    },
    "engineer/paper-illustration-image2.md": {
        "fdc43b56ffc47e49e8dc3b575948726b4fa2a44a8e881d4606bd5e9706d168ba",
    },
    "engineer/paper-review-revision-loop.md": {
        "e7cde5309b287eb75f91fd8f0c9400341910fb1eeb556a2d3ec78d6accd3a2c5",
    },
    "engineer/presentation-master.md": {
        "3b70d2fd3ec0bd00d6a6090238d44b20c4cbcf239b8e2290acdea65c84f47847",
        "a78fab7703be6727a6cbf6e27ba8b397630908268135b89e6a88c34dda16662e",
    },
    "engineer/research-results-analysis-and-figures.md": {
        "749e2dccdca0fe72b51cf658dfd389c9b47f73a63fb4a512226fbef3d91cba62",
        "41c046a4a4c6e89eaa063bdd1804a114792d4ae54051c0ac16f950e47eb8af9c",
        "d2529cf7bf29486dcc3e8ae5baca15d04e4e0a1368c2c99d2e4dda4d7bf481af",
        "4f0baf6ce7b0de2da3790fd51ef04d957e210f525b4761c12e498d893baf0186",
    },
    "engineer/research-submission-assurance-gate.md": {
        "89b29cad54f8b790997a3374c273a028953acd909dcb51a9170addfd3bfb4a97",
    },
    "reviewer/academic-paper-peer-review-benchmark.md": {
        "ecf4983184c6f86d557f91b606c832739de33e22112b438f3fe0e240233f81d3",
    },
    "reviewer/argus-reviewer-role.md": {
        "c581e43666de71a3af7274523fde0317c22fa315f612bbbcb41c8f41dca265f9",
    },
    "reviewer/reviewer-engineer-handoff.md": {
        "451a98884ad675eace245b2974ea4b13b62a3caa83179c025481ab4e36c8ad7d",
    },
}


def builtin_skill_source_path() -> Path:
    """Return the filesystem path for bundled skill markdown when available."""
    return Path(__file__).resolve().parents[1] / "builtin_skills"


def iter_builtin_skill_texts() -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for every bundled default skill."""
    root = resources.files(_BUILTIN_PACKAGE)
    yield from _iter_builtin_skill_resources(root)


def iter_common_builtin_skill_texts() -> Iterable[tuple[str, str]]:
    """Yield top-level common skills, excluding domain-pack subdirectories."""
    root = resources.files(_BUILTIN_PACKAGE)
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")) or not entry.name.endswith(".md"):
            continue
        yield entry.name, entry.read_text(encoding="utf-8")


def vertical_skill_source_path(vertical: str) -> Path:
    """Filesystem path of a vertical's own skills: ``verticals/<v>/skills``.

    The skill-layering convention: ``builtin_skills/`` holds only cross-vertical
    (general) skills, while each vertical ships its own domain skills under
    ``argus_skill/verticals/<vertical>/skills/{engineer,reviewer}/``. This is the
    version-controlled read-only SOURCE for that vertical's skills.
    """
    if not vertical or "/" in vertical or "\\" in vertical or vertical.startswith("."):
        raise ValueError(f"invalid vertical name: {vertical!r}")
    return Path(__file__).resolve().parents[1] / "verticals" / vertical / "skills"


def iter_vertical_skill_texts(vertical: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for a vertical's own skills.

    Relative names are rooted at the vertical's ``skills/`` dir (e.g.
    ``reviewer/quant-factor-report-review.md``) so they match the skill paths a
    vertical's ``REVIEWER_CHECKLISTS`` names verbatim and overlay the same
    ``<role>/<name>.md`` layout as the bundled builtins. Fail-open: an unknown
    vertical or one with no ``skills/`` dir yields nothing.
    """
    emitted: set[str] = set()
    for source_vertical in (*_VERTICAL_SKILL_INHERITANCE.get(vertical, ()), vertical):
        root = vertical_skill_source_path(source_vertical)
        if not root.is_dir():
            continue
        for filename, text in _iter_builtin_skill_resources(root):
            if filename in emitted:
                continue
            emitted.add(filename)
            yield filename, text


def _iter_builtin_skill_resources(
    root: Traversable,
    prefix: str = "",
) -> Iterable[tuple[str, str]]:
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")):
            continue
        relative_name = f"{prefix}{entry.name}"
        if entry.is_dir():
            # Reference corpora are package assets consumed by their owning
            # skill, not independently matchable skills.
            if entry.name == "references":
                continue
            yield from _iter_builtin_skill_resources(entry, f"{relative_name}/")
        elif entry.name.endswith(".md"):
            yield relative_name, entry.read_text(encoding="utf-8")
        elif _is_bundled_script(prefix, entry.name):
            # Scripts that ship alongside a skill (e.g.
            # engineer/figure_spec_scripts/figure_renderer.py) live in
            # ``*_scripts/`` subdirs and are seeded verbatim so the
            # skill can invoke them in the project workspace.
            yield relative_name, entry.read_text(encoding="utf-8")


_BUNDLED_SCRIPT_EXTENSIONS = (".py", ".json", ".sh")


def _is_bundled_script(prefix: str, filename: str) -> bool:
    """A file is a bundled-script asset iff it lives under a
    ``*_scripts/`` directory and has a known script extension."""
    if not any(filename.endswith(ext) for ext in _BUNDLED_SCRIPT_EXTENSIONS):
        return False
    # ``prefix`` ends with "/" by construction; split into segments.
    segments = [s for s in prefix.split("/") if s]
    return any(seg.endswith("_scripts") for seg in segments)


def seed_builtin_skills(skills_dir: Path, *, overwrite: bool = False) -> dict[str, bool]:
    """Seed bundled skills into ``skills_dir``.

    Existing files are preserved by default. The return value maps each
    bundled filename to ``True`` when it was created/replaced and ``False``
    when an existing user file was left untouched.
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}
    for filename, text in iter_builtin_skill_texts():
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            if _upgrade_unmodified_builtin(dest, filename, text):
                created[filename] = True
                continue
            created[filename] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True
    return created


def seed_builtin_skills_for_vertical(
    skills_dir: Path,
    vertical: str,
    *,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Seed COMMON builtins + a vertical's own skills into ``skills_dir``.

    Used to populate a mission's project workspace (``argus_builtin_skills/``) or
    the runtime vertical layer so the agent sees the cross-vertical skills PLUS
    the active vertical's domain skills. The vertical's real skill bodies
    OVERWRITE any same-path builtin stub (a moved domain skill leaves a pointer
    stub under ``builtin_skills/``; here the real body wins), so the workspace
    never carries the pointer.

    Note: this uses the FULL bundled set (``iter_builtin_skill_texts``), not
    ``iter_common_builtin_skill_texts`` — the latter skips the ``engineer/`` and
    ``reviewer/`` subdirectories, which is exactly where the cross-vertical
    skills live. Files the vertical will overwrite are skipped on the builtin
    pass so a pointer stub is never written into the workspace at all.

    Returns a map of relative filename → created/replaced (True) or skipped
    (False, an existing file left untouched because ``overwrite`` is False).
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}

    # The vertical's own skills (real bodies) — these always win over a builtin
    # stub of the same relative path.
    vertical_texts = dict(iter_vertical_skill_texts(vertical))

    # 1. Common/bundled builtins, skipping any path the vertical will overwrite
    #    (so a pointer stub is never written into the workspace).
    for filename, text in iter_builtin_skill_texts():
        if filename in vertical_texts:
            continue
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            if _upgrade_unmodified_builtin(dest, filename, text):
                created[filename] = True
                continue
            created[filename] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True

    # 2. The vertical's real skill bodies — always written so the agent gets the
    #    real body, never the stub (this is the point of vertical-aware seeding).
    for filename, text in vertical_texts.items():
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True

    return created


def seed_vertical_skills(
    skills_dir: Path,
    vertical: str,
    *,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Seed only the active vertical's skills into a project runtime layer."""
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}
    for filename, text in iter_vertical_skill_texts(vertical):
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            created[filename] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True
    return created


def _validate_builtin(filename: str, text: str) -> None:
    skill = Skill.parse(text, filename)
    if not skill.name.strip():
        raise ValueError(f"bundled skill has no name: {filename}")
    if not skill.description.strip():
        raise ValueError(f"bundled skill has no description: {filename}")


def _upgrade_unmodified_builtin(dest: Path, filename: str, text: str) -> bool:
    previous_digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if previous_digest not in _SAFE_BUILTIN_UPGRADE_DIGESTS.get(filename, set()):
        return False
    _atomic_write_text(dest, text)
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
