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
    "chip_design": ("digital_circuit",),
    # The three Recursive "First Steps" benchmarks are concrete instances of
    # the generic speedrun mission shape (a fixed budget, a single scalar to
    # move), so they inherit its methodology skills. SOL work additionally
    # needs the general GPU-kernel priors.
    "kernelbench": ("speedrun", "kernel_engineering"),
    "nanochat": ("speedrun",),
    "nanogpt_speedrun": ("speedrun",),
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


# Factory bodies we no longer ship at these paths — either retired outright or
# moved into the vertical that owns them. A runtime layer seeded by an older
# release still holds a copy, and because seeding only ever ADDS, that copy
# stays a matcher candidate forever: every project pays its summary tokens on
# every match, including projects whose vertical will never want it.
#
# A copy is removed only when it is byte-identical to what we shipped. An agent
# that edited the file has a different digest and keeps its work; a moved skill
# is still delivered through its vertical's own seeding path, so this retires a
# stale location, never a capability.
_RETIRED_BUILTIN_DIGESTS: dict[str, set[str]] = {
    "engineer/agent-research-benchmark-runner.md": {
        "151a3ef862a408a3f8d1db7a1f9a76dc8977fc3b00d182476cb7c9ee04f8ef22",
    },
    "engineer/ale-last-exam-execution.md": {
        "7577992b6add15c3281c272544067df96524bb694b5766d80295d2ff85dc56d9",
    },
    "engineer/b200-kernelbench-runtime.md": {
        "1367b78b707d829758b3dadbf6eb8f2ce84645da32a344ba4a45f2d8c32ab8f1",
    },
    "engineer/kernel-benchmark-measurement-integrity.md": {
        "324fe112ff4fef9da9458f871550a7610bab51e90c035697b5ac3f8e4a843ad7",
    },
    "engineer/kernel-optimization-knowledge.md": {
        "8d88378eb6f6629386a6274384dfaf9cdf61eb861b63cc8f5d67e2cbf8c4a7dd",
    },
    "engineer/kernel-optimization-process-trace.md": {
        "d5046c85561e8edafd0188f9c0123b992b383639902f40e6c915f972fe1623de",
    },
    "engineer/kline-chart.md": {
        "956fafe74471f604f55960ffe078f01b8e9a1eeb516d5d4111e7d3f844a2b4df",
    },
    "engineer/model-selection-loop.md": {
        "8e7d21f6e4ea8b75a854ecb76af3275d0d90e06fdcde96e36504ad17c401dad8",
    },
    "engineer/modern-gpu-blackwell-kernel-techniques.md": {
        "d7320f39de10bf9c2742d295a022f12b5f2865b11b8e19129acbcfdeb883f458",
    },
    "engineer/nanochat-autoresearch-hands-on-trace.md": {
        "5e21060a1674314e0d48d9c38d37fcfcf9614764405160e0d2d66eee94dc5c69",
        "453a1fda46d46f40b24f7e21ba828514ab9f81f984737d20eb9ad4b8e92735fa",
    },
    "engineer/nanochat-autoresearch-sota-optimization.md": {
        "98a4b6735a6f017ad0d2887409729e02827c1a5637a39c08abb0c0675a4604af",
    },
    "engineer/nanochat-pretrain-runner.md": {
        "b3006cc40d2e4aead8ad99fa7187297a1e5e9d790a24e6de7671931f7c51c890",
    },
    "engineer/nanogpt-speedrun-h100-sota.md": {
        "1feaf35ee286df3c371db1478edecb2485682a355cf13fb37cfc51166a019fa5",
    },
    "engineer/official-sol-execbench-env.md": {
        "b89bede3c3f2a9beb70d666b6629432d75ed090c03aec29df6b5b49c78fc9895",
    },
    "engineer/quant-factor-loop.md": {
        "4f86b586c18f3726bcea7d62d650d6b438d4c1dd21fa59da336dbaf19163b9ee",
    },
    "engineer/sol-kernel-hands-on-trace.md": {
        "0f34d98fd42031d66f152f88c80ad77633d2804fa678620cd5bb5a12435b929b",
    },
    "engineer/sol-kernel-sota-optimization.md": {
        "3a5e9933002e8621fe6e570a416562e78bd5d1b46c98b4b3d3cd3e34a83b90e8",
    },
    "engineer/speedrun-hands-on-trace.md": {
        "4d311fd17722ec9f8b3a206b8f47aa0cfaa7ab70965e594d4c7545126e4eb032",
    },
    "engineer/speedrun-sota-optimization.md": {
        "be87798fc1fdb8dbc6bac7ebab25f44dc7560da73c4215333339ebcf85df091e",
    },
    "reviewer/ale-last-exam-delivery-review.md": {
        "3842eebdd80b2db48919bd672e1d00f379c46053656df56c17237aa9a97cf909",
    },
    "reviewer/quant-factor-report-review.md": {
        "e3dfe8ea03b319bfbfc7fe023d00c69708abdc03ca3e3c553378bf33c8802e22",
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

    The skill-layering convention: ``builtin_skills/`` holds cross-workflow
    skills, while each vertical ships workflow-specific skills under
    ``argus_skill/verticals/<vertical>/skills/{engineer,reviewer}/``. This is the
    version-controlled read-only SOURCE for that vertical's skills.
    """
    if not vertical or "/" in vertical or "\\" in vertical or vertical.startswith("."):
        raise ValueError(f"invalid vertical name: {vertical!r}")
    return Path(__file__).resolve().parents[1] / "verticals" / vertical / "skills"


def domain_skill_source_path(domain: str) -> Path:
    """Filesystem path of a built-in domain's matchable Skills."""
    if not domain or "/" in domain or "\\" in domain or domain.startswith("."):
        raise ValueError(f"invalid domain name: {domain!r}")
    return Path(__file__).resolve().parents[1] / "domains" / domain / "skills"


def iter_vertical_skill_texts(vertical: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for a vertical's own skills.

    Relative names are rooted at the vertical's ``skills/`` dir (e.g.
    ``reviewer/quant-factor-report-review.md``) so they match the
    ``<role>/<name>.md`` layout the vertical's checklist prose and
    ``role_banner`` reference verbatim, and overlay the same layout as the
    bundled builtins. Fail-open: an unknown vertical or one with no
    ``skills/`` dir yields nothing.
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


def iter_domain_skill_texts(domain: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for one built-in domain."""
    root = domain_skill_source_path(domain)
    if root.is_dir():
        yield from _iter_builtin_skill_resources(root)


def iter_context_skill_texts(
    vertical: str,
    domain: str | None = None,
) -> Iterable[tuple[str, str]]:
    """Yield workflow Skills plus optional domain Skills, with domain overrides."""
    merged = dict(iter_vertical_skill_texts(vertical))
    if domain:
        merged.update(dict(iter_domain_skill_texts(domain)))
    yield from merged.items()


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


def retire_orphaned_builtin_seeds(skills_dir: Path) -> list[str]:
    """Delete unmodified copies of builtins this release no longer ships.

    Seeding only ever adds, so a skill removed or relocated upstream lingers in
    every already-initialised runtime layer and keeps costing matcher tokens.
    This removes such a copy only when it is byte-identical to the body we
    shipped (see :data:`_RETIRED_BUILTIN_DIGESTS`); anything an agent edited has
    a different digest and survives untouched.

    Returns the relative filenames that were removed.
    """
    root = Path(skills_dir)
    current = {name for name, _text in iter_builtin_skill_texts()}
    removed: list[str] = []
    for filename, digests in _RETIRED_BUILTIN_DIGESTS.items():
        if filename in current:
            continue
        path = root / filename
        try:
            if not path.is_file():
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() not in digests:
                continue
            path.unlink()
        except OSError:
            continue
        removed.append(filename)
    return sorted(removed)


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
    """Compatibility wrapper for a workflow without a domain overlay."""
    return seed_builtin_skills_for_context(
        skills_dir,
        vertical,
        overwrite=overwrite,
    )


def seed_builtin_skills_for_context(
    skills_dir: Path,
    vertical: str,
    *,
    domain: str | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Seed COMMON builtins + a vertical's own skills into ``skills_dir``.

    Used to populate a mission's project workspace (``argus_builtin_skills/``) or
    the runtime shared-scope layer so the agent sees common Skills plus the active
    workflow and optional domain Skills. Context-specific real bodies
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

    # Workflow/domain Skills (real bodies) always win over a builtin
    # stub of the same relative path.
    vertical_texts = dict(iter_context_skill_texts(vertical, domain))

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

    # 2. Context-specific real bodies are always written, never pointer stubs.
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
    overwrite_unidentified: bool = False,
) -> dict[str, bool]:
    """Compatibility wrapper for a vertical-only runtime layer."""
    return seed_context_skills(
        skills_dir,
        vertical,
        overwrite=overwrite,
        overwrite_unidentified=overwrite_unidentified,
    )


def seed_context_skills(
    skills_dir: Path,
    vertical: str,
    *,
    domain: str | None = None,
    overwrite: bool = False,
    overwrite_unidentified: bool = False,
) -> dict[str, bool]:
    """Seed only the active workflow/domain context into one runtime layer."""
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}
    for filename, text in iter_context_skill_texts(vertical, domain):
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            if overwrite_unidentified:
                should_refresh = not filename.endswith(".md")
                if filename.endswith(".md"):
                    try:
                        should_refresh = not Skill.parse(
                            dest.read_text(encoding="utf-8"),
                            str(dest),
                        ).skill_id
                    except OSError:
                        should_refresh = False
                if should_refresh:
                    _atomic_write_text(dest, text)
                    created[filename] = True
                    continue
            created[filename] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True
    return created


def remove_unmodified_vertical_skill_seeds(
    skills_dir: Path,
    vertical: str,
) -> list[str]:
    """Remove legacy project-layer factory copies without touching learned edits."""
    root = Path(skills_dir)
    removed: list[str] = []
    for filename, source_text in iter_vertical_skill_texts(vertical):
        path = root / filename
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == source_text:
                path.unlink()
                removed.append(filename)
        except OSError:
            continue
    return removed


def remove_unmodified_inactive_vertical_skill_seeds(
    skills_dir: Path,
    active_vertical: str | None,
) -> list[str]:
    """Compatibility wrapper for a workflow without a domain overlay."""
    return remove_unmodified_inactive_context_skill_seeds(
        skills_dir,
        active_vertical,
    )


def remove_unmodified_inactive_context_skill_seeds(
    skills_dir: Path,
    active_vertical: str | None,
    *,
    active_domain: str | None = None,
) -> list[str]:
    """Remove unedited factory copies outside the active workflow/domain context."""
    from ..domains import BUILTIN_DOMAINS
    from .vertical_select import VERTICALS

    root = Path(skills_dir)
    active_filenames = (
        {
            filename
            for filename, _text in iter_context_skill_texts(
                active_vertical,
                active_domain,
            )
        }
        if active_vertical
        else set()
    )
    removed: set[str] = set()
    for vertical in VERTICALS:
        if vertical == active_vertical:
            continue
        for filename, source_text in iter_vertical_skill_texts(vertical):
            if filename in active_filenames or filename in removed:
                continue
            path = root / filename
            try:
                if path.is_file() and path.read_text(encoding="utf-8") == source_text:
                    path.unlink()
                    removed.add(filename)
            except OSError:
                continue
    for domain in BUILTIN_DOMAINS:
        if domain == active_domain:
            continue
        for filename, source_text in iter_domain_skill_texts(domain):
            if filename in active_filenames or filename in removed:
                continue
            path = root / filename
            try:
                if path.is_file() and path.read_text(encoding="utf-8") == source_text:
                    path.unlink()
                    removed.add(filename)
            except OSError:
                continue
    return sorted(removed)


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
