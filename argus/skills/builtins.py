"""Bundled cross-vertical defaults and vertical-aware Skill seeding.

``argus/builtin_skills`` contains only reusable workflow skills.
Domain/vertical playbooks live under ``verticals/<name>/skills`` and are
seeded only for the active context.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Iterable

from ..core.file_facts import file_facts

log = logging.getLogger(__name__)
_BUILTIN_PACKAGE = "argus.builtin_skills"
DEFAULT_PROJECT_BUILTIN_SKILLS_DIR = "argus_builtin_skills"
_BUILTIN_SEED_STATE = ".argus-builtin-seeds.json"
_MOVED_SKILL_MARKER = ".moved-from-global.json"
_RETIRED_BUILTIN_SEEDS = frozenset({
    # Consolidated into project-venv-package-management.md.
    "engineer/project-environment-management.md",
    # Renamed on 2026-09-06 so that the library speaks like a researcher:
    # reading the evidence, the strongest argument against, claims against
    # evidence, a citation check, an environment readiness check, guiding the
    # engineer. A seeded copy under the old name is removed when it still
    # matches, archived otherwise.
    "reviewer/experiment-audit.md",
    "reviewer/kill-argument.md",
    "engineer/citation-audit.md",
    "engineer/claims-evidence-audit.md",
    "engineer/environment-readiness-gate.md",
    "reviewer/reviewer-engineer-handoff.md",
    "engineer/experiment-audit.md",
    "engineer/paper-claim-audit.md",
    "engineer/singularity-amlt-gpu-ops.md",
    "engineer/nanochat-autoresearch-hands-on-trace.md",
    "engineer/nanochat-autoresearch-sota-optimization.md",
    "engineer/nanochat-pretrain-runner.md",
    # Removed on 2026-10-01 after the skill usage audit (121 projects, 2026-08-28
    # to 09-30): none of these was opened by any role, or the file duplicated a
    # sibling. Seeded copies are removed when unedited, archived otherwise.
    # Research: zero-opened figure and timeline files.
    "engineer/academic-vector-figures.md",
    "engineer/figure-spec.md",
    "engineer/research-timeline.md",
    # Research: the ideation sub-pattern cards, anti-patterns, companion
    # combos and the vendored attribution page (#193). The 15 pattern cards
    # follow further down; only their overview stays.
    "engineer/references/ideation/ideation-sub-patterns/C00.md",
    "engineer/references/ideation/ideation-sub-patterns/C01.md",
    "engineer/references/ideation/ideation-sub-patterns/C02.md",
    "engineer/references/ideation/ideation-sub-patterns/C03.md",
    "engineer/references/ideation/ideation-sub-patterns/C04.md",
    "engineer/references/ideation/ideation-sub-patterns/C05.md",
    "engineer/references/ideation/ideation-sub-patterns/C06.md",
    "engineer/references/ideation/ideation-sub-patterns/C07.md",
    "engineer/references/ideation/ideation-sub-patterns/C08.md",
    "engineer/references/ideation/ideation-sub-patterns/C09.md",
    "engineer/references/ideation/ideation-sub-patterns/C10.md",
    "engineer/references/ideation/ideation-sub-patterns/C11.md",
    "engineer/references/ideation/ideation-sub-patterns/C12.md",
    "engineer/references/ideation/ideation-sub-patterns/C13.md",
    "engineer/references/ideation/ideation-sub-patterns/C14.md",
    "engineer/references/ideation/ideation-sub-patterns/C15.md",
    "engineer/references/ideation/ideation-sub-patterns/C16.md",
    "engineer/references/ideation/ideation-sub-patterns/C17.md",
    "engineer/references/ideation/ideation-sub-patterns/C18.md",
    "engineer/references/ideation/ideation-sub-patterns/C19.md",
    "engineer/references/ideation/ideation-sub-patterns/C20.md",
    "engineer/references/ideation/ideation-sub-patterns/C21.md",
    "engineer/references/ideation/ideation-sub-patterns/C22.md",
    "engineer/references/ideation/ideation-sub-patterns/C23.md",
    "engineer/references/ideation/ideation-sub-patterns/C24.md",
    "engineer/references/ideation/ideation-sub-patterns/C25.md",
    "engineer/references/ideation/ideation-sub-patterns/C26.md",
    "engineer/references/ideation/ideation-sub-patterns/C27.md",
    "engineer/references/ideation/ideation-sub-patterns/C28.md",
    "engineer/references/ideation/ideation-sub-patterns/C29.md",
    "engineer/references/ideation/ideation-sub-patterns/C30.md",
    "engineer/references/ideation/ideation-sub-patterns/overview.md",
    "engineer/references/ideation/anti-patterns.md",
    "engineer/references/ideation/ideation-patterns/companion-combos.md",
    "engineer/references/ideation/ATTRIBUTION.md",
    # Research: the 15 ideation pattern cards (2026-10-07). Zero opens in the
    # audited projects, including the 11 whose agent opened the Idea playbook
    # that points at them; overview.md keeps every pattern's definition,
    # signature and when-to-apply, which is all a route choice needs.
    "engineer/references/ideation/ideation-patterns/adapt_via_conditioning.md",
    "engineer/references/ideation/ideation-patterns/algebraic_equivalence_unification.md",
    "engineer/references/ideation/ideation-patterns/architectural_operator_substitution.md",
    "engineer/references/ideation/ideation-patterns/assumption_audit_and_pivot.md",
    "engineer/references/ideation/ideation-patterns/characterize_limit_then_surpass.md",
    "engineer/references/ideation/ideation-patterns/controlled_diagnostic_design.md",
    "engineer/references/ideation/ideation-patterns/decompose_and_delegate.md",
    "engineer/references/ideation/ideation-patterns/generative_process_redesign.md",
    "engineer/references/ideation/ideation-patterns/heterogeneous_decomposition.md",
    "engineer/references/ideation/ideation-patterns/reframe_as_solvable_object.md",
    "engineer/references/ideation/ideation-patterns/relax_discrete_search_to_continuous.md",
    "engineer/references/ideation/ideation-patterns/self_supervised_signal_engineering.md",
    "engineer/references/ideation/ideation-patterns/structural_prior_encoding.md",
    "engineer/references/ideation/ideation-patterns/targeted_self_supervised_objective.md",
    "engineer/references/ideation/ideation-patterns/unify_into_shared_representation.md",
    # Software: the Verus plugin (kept in git history for the Vertical Store)
    # and the opt-in Manager grounding pass nobody enabled.
    "engineer/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md",
    "engineer/formal-verification/verus-spec-generation-and-repair.md",
    "manager/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md",
    "manager/formal-verification/verus-spec-generation-and-repair.md",
    "planner/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md",
    "planner/formal-verification/verus-spec-generation-and-repair.md",
    "references/verus-spec-generation-and-repair.zh-CN.md",
    "reviewer/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md",
    "reviewer/formal-verification/verus-spec-generation-and-repair.md",
    "verus-spec-generation-and-repair.md",
    "manager/software-project-grounding.md",
    # Global library: identity text the prompt already carries, a diagram
    # route whose renderers are not installed, and a duplicate of
    # performance-profile-ground-truth.md.
    "engineer/drawio-diagram-authoring.md",
    "engineer/mermaid-graphviz-diagrams.md",
    "engineer/argus-engineer-role.md",
    "agent-md-optimize-project-template.md",
    # Kernel engineering: a single B200 kernel's work log and an external
    # reading list.
    "engineer/kernel-optimization-process-trace.md",
    "engineer/modern-gpu-blackwell-kernel-techniques.md",
    # Merged on 2026-10-01: the same audit found the figure, diagnosis-ladder,
    # method-card, infrastructure, source-checking, role-banner and wiki
    # material each written three to five times across sibling files. Each
    # cluster now lives in one file; these are the absorbed originals.
    # Research vertical.
    "engineer/result-to-claim.md",
    "engineer/suspect-the-setup.md",
    "engineer/method_card_template.md",
    "engineer/executable-spec.md",
    "engineer/delta-on-reference.md",
    "engineer/write-for-review.md",
    "engineer/hypothesis-implementation-contract.md",
    "engineer/implementation-brief.md",
    "engineer/research-visualization-router.md",
    "engineer/research-results-analysis-and-figures.md",
    "engineer/paper-illustration-image2.md",
    "engineer/training-infrastructure-guide.md",
    "engineer/infrastructure-landscape-survey.md",
    "engineer/framework-stand-up-pilot.md",
    "engineer/recipe-anchored-tuning.md",
    "engineer/claims-against-evidence.md",
    "engineer/citation-check.md",
    "engineer/semantic-scholar-search.md",
    "engineer/paper-infrastructure-review.md",
    "engineer/paper-exemplar-pdf-learning.md",
    "reviewer/reading-the-evidence.md",
    "reviewer/infrastructure-choice-review.md",
    # Global library.
    "engineer/stale-world-model.md",
    "engineer/environment-readiness.md",
    "engineer/study-and-curate.md",
    "engineer/wiki-collector.md",
    "reviewer/wiki-curator.md",
    "planner/argus-planner-role.md",
    "reviewer/guiding-the-engineer.md",
    "manager/argus-manager-role.md",
    # Kernel engineering, software and learning verticals.
    "engineer/kernel-optimization-knowledge.md",
    "engineer/kernel-environment-first-engineering.md",
    "reviewer/kernel-engineering-review.md",
    "planner/software-project-grounding.md",
    "engineer/learning-curation.md",
    "reviewer/curation-review.md",
    # Agent Teams removed on 2026-10-20.
    "agent-team-lead.md",
    # Retired on 2026-10-20 with its PDF CLI: the backend reads PDFs itself.
    "engineer/pdf-chat.md",
})


def builtin_skill_source_path() -> Path:
    """Return the filesystem path for bundled skill markdown when available."""
    return Path(__file__).resolve().parents[1] / "builtin_skills"


def iter_builtin_skill_texts() -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for every bundled default skill."""
    root = resources.files(_BUILTIN_PACKAGE)
    yield from _iter_builtin_skill_resources(root)


def vertical_skill_source_path(vertical: str) -> Path:
    """Filesystem path of a vertical's own skills: ``verticals/<v>/skills``.

    The skill-layering convention: ``builtin_skills/`` holds cross-workflow
    skills, while each vertical ships workflow-specific skills under
    ``argus/verticals/<vertical>/skills/{engineer,reviewer}/``. This is the
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


def vertical_skill_parents(vertical: str) -> tuple[str, ...]:
    """Verticals whose skill trees are seeded before ``vertical``'s own, in order.

    A vertical declares its own parents: an entry-point plugin exposes
    ``VERTICAL_SKILL_PARENTS`` (validated by ``verticals._registry``); the
    built-in verticals declare none. A parent may be a plugin
    (``nanogpt_speedrun <- speedrun``) or built in; ``_vertical_skill_roots``
    resolves both the same way.
    """
    from ..verticals._registry import vertical_plugin

    plugin = vertical_plugin(vertical)
    return plugin.skill_parents if plugin is not None else ()


def _vertical_skill_roots(vertical: str) -> list[Traversable]:
    """Existing ``skills/`` roots for ``vertical``'s parents and then itself.

    A plugin vertical serves its tree from ``VerticalPlugin.skills_root``; a
    built-in one from ``verticals/<name>/skills`` inside this package.
    """
    from ..verticals._registry import vertical_plugin

    roots: list[Traversable] = []
    for source in (*vertical_skill_parents(vertical), vertical):
        plugin = vertical_plugin(source)
        root = (
            plugin.skills_root
            if plugin is not None and plugin.skills_root is not None
            else vertical_skill_source_path(source)
        )
        if root.is_dir():
            roots.append(root)
        elif source != vertical:
            log.warning(
                "vertical %r declares skill parent %r, which is neither an installed "
                "plugin with skills nor a built-in vertical with a skills directory",
                vertical, source,
            )
    return roots


def vertical_skill_read_dirs(vertical: str) -> list[str]:
    """Skill directories a read-only role must be able to open for ``vertical``.

    Stage prompts name the vertical's packaged playbooks by absolute path. A
    Reviewer or Planner told to open one but granted only the profile skill
    roots was refused 22 times in project s-a43d1018 and re-ran its round.
    """
    if not vertical:
        return []
    return [str(root) for root in _vertical_skill_roots(vertical) if isinstance(root, Path)]


def iter_vertical_skill_texts(vertical: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for a vertical's own skills.

    Relative names are rooted at the vertical's ``skills/`` dir (e.g.
    ``reviewer/kernel-engineering-review.md``) so they match the
    ``<role>/<name>.md`` layout the vertical's checklist prose and
    ``role_banner`` reference verbatim, and overlay the same layout as the
    bundled builtins. Parents' skills come first; the vertical's own file of
    the same relative name wins nothing -- the first emitted name is kept.
    Fail-open: an unknown vertical or one with no ``skills/`` dir yields nothing.
    """
    emitted: set[str] = set()
    for root in _vertical_skill_roots(vertical):
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


def _iter_reference_assets(
    root: Traversable,
    prefix: str = "",
    *,
    inside_references: bool = False,
) -> Iterable[tuple[str, str]]:
    """Yield supporting reference cards without making them matchable Skills."""
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")):
            continue
        relative_name = f"{prefix}{entry.name}"
        if entry.is_dir():
            yield from _iter_reference_assets(
                entry,
                f"{relative_name}/",
                inside_references=(
                    inside_references or entry.name == "references"
                ),
            )
        elif inside_references and entry.name.endswith(".md"):
            yield relative_name, entry.read_text(encoding="utf-8")


def iter_context_skill_assets(
    vertical: str,
    domain: str | None = None,
) -> Iterable[tuple[str, str]]:
    """Yield reference corpora consumed by context Skills.

    ``iter_context_skill_texts`` deliberately excludes ``references/`` because
    those cards are not independently matchable Skills. Excluding them from the
    seeder too left the owning Skill pointing at files that did not exist:
    run-01 had 43 of 94 research resources and none of the ideation cards
    (only the pattern overview since the 2026-10-07 prune).
    """
    merged: dict[str, str] = {}
    for root in _vertical_skill_roots(vertical):
        merged.update(dict(_iter_reference_assets(root)))
    if domain:
        root = domain_skill_source_path(domain)
        if root.is_dir():
            merged.update(dict(_iter_reference_assets(root)))
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


def _moved_global_skill_names() -> set[str]:
    moved: set[str] = set()
    verticals_root = Path(__file__).resolve().parents[1] / "verticals"
    for marker in verticals_root.glob(f"*/skills/{_MOVED_SKILL_MARKER}"):
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values = payload.get("paths", ()) if isinstance(payload, dict) else payload
        if isinstance(values, list):
            moved.update(
                str(value)
                for value in values
                if isinstance(value, str) and value.strip()
            )
    return moved


def retire_orphaned_builtin_seeds(skills_dir: Path, *, include_moved: bool = True) -> list[str]:
    """Remove retired seeds from matching, archiving any copy Argus cannot vouch for.

    A copy whose file facts still match what Argus recorded when it wrote it is
    factory-owned and removed. Anything else -- an operator's edit, or a copy
    seeded before Argus recorded file facts -- is archived, never deleted.
    """
    skills_dir = Path(skills_dir)
    state = _seed_state(skills_dir)
    retired = set(_RETIRED_BUILTIN_SEEDS)
    if include_moved:
        retired |= _moved_global_skill_names()
    removed: list[str] = []
    for relative_name in sorted(retired):
        path = skills_dir / relative_name
        try:
            body = path.read_bytes()
            facts = file_facts(path)
        except OSError:
            continue
        if state.get(relative_name) == facts:
            try:
                path.unlink()
            except OSError:
                continue
        else:
            archive = (
                skills_dir
                / "_retired_builtin_skills"
                / f"{relative_name}.retired"
            )
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists():
                try:
                    if archive.read_bytes() == body:
                        path.unlink()
                    else:
                        path.replace(
                            archive.with_name(f"{archive.name}.{uuid.uuid4().hex[:12]}")
                        )
                except OSError:
                    continue
            else:
                try:
                    path.replace(archive)
                except OSError:
                    continue
        removed.append(relative_name)
    return removed


def _seed_state(skills_dir: Path) -> dict[str, list[int]]:
    """The file facts of each seed as Argus last wrote or confirmed it."""
    try:
        payload = json.loads(
            (skills_dir / _BUILTIN_SEED_STATE).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(name): facts
        for name, facts in payload.items()
        if isinstance(facts, list)
    }


def _seed_texts(
    skills_dir: Path,
    texts: Iterable[tuple[str, str]],
    *,
    overwrite: bool,
) -> dict[str, bool]:
    state = _seed_state(skills_dir)
    previous_state = dict(state)
    created: dict[str, bool] = {}
    for filename, text in texts:
        text = text.replace("\r\n", "\n")
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        body = text.encode("utf-8")
        try:
            installed = dest.read_bytes()
            facts = file_facts(dest)
        except (FileNotFoundError, IsADirectoryError):
            installed = facts = None
        except OSError:
            created[filename] = False
            continue
        # Factory-owned: absent, already this text (in either line ending), or
        # untouched since Argus last wrote it. Anything else is an operator edit.
        factory_owned = (
            installed is None
            or installed.replace(b"\r\n", b"\n") == body
            or state.get(filename) == facts
        )
        if not overwrite and not factory_owned:
            created[filename] = False
            continue
        changed = installed != body
        if changed:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(dest, text)
        state[filename] = file_facts(dest)
        created[filename] = changed
    if state != previous_state:
        _atomic_write_text(
            skills_dir / _BUILTIN_SEED_STATE,
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
    return created


def seed_builtin_skills(skills_dir: Path, *, overwrite: bool = False) -> dict[str, bool]:
    """Seed bundled skills into ``skills_dir``.

    Existing files are preserved by default. The return value maps each
    bundled filename to ``True`` when it was created/replaced and ``False``
    when an existing user file was left untouched.
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    retire_orphaned_builtin_seeds(skills_dir)
    return _seed_texts(
        skills_dir,
        iter_builtin_skill_texts(),
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

    Note: this uses the FULL bundled set (``iter_builtin_skill_texts``),
    including the ``engineer/`` and ``reviewer/`` subdirectories where the
    cross-vertical skills live. Files the vertical will overwrite are skipped
    on the builtin pass so a pointer stub is never written into the workspace
    at all.

    Returns a map of relative filename → created/replaced (True) or skipped
    (False, an existing file left untouched because ``overwrite`` is False).
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    retire_orphaned_builtin_seeds(skills_dir, include_moved=False)
    # Workflow/domain Skills (real bodies) always win over a builtin
    # stub of the same relative path.
    vertical_texts = dict(iter_context_skill_texts(vertical, domain))

    # 1. Common/bundled builtins, skipping any path the vertical will overwrite
    #    (so a pointer stub is never written into the workspace).
    created = _seed_texts(
        skills_dir,
        (
            (filename, text)
            for filename, text in iter_builtin_skill_texts()
            if filename not in vertical_texts
        ),
        overwrite=overwrite,
    )

    # 2. Context-specific real bodies win when explicitly requested, newly
    # seeded, or still factory-owned; operator edits remain intact.
    created.update(
        _seed_texts(
            skills_dir,
            vertical_texts.items(),
            overwrite=overwrite,
        )
    )
    created.update(
        _seed_texts(
            skills_dir,
            iter_context_skill_assets(vertical, domain),
            overwrite=overwrite,
        )
    )

    return created


def seed_context_skills(
    skills_dir: Path,
    vertical: str,
    *,
    domain: str | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Seed only the active workflow/domain context into one runtime layer."""
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    # Moved global paths are valid here; never retire learned vertical copies.
    retire_orphaned_builtin_seeds(skills_dir, include_moved=False)
    created = _seed_texts(
        skills_dir, iter_context_skill_texts(vertical, domain), overwrite=overwrite,
    )
    created.update(_seed_texts(
        skills_dir, iter_context_skill_assets(vertical, domain), overwrite=overwrite,
    ))
    return created


def remove_unmodified_inactive_context_skill_seeds(
    skills_dir: Path,
    active_vertical: str | None,
    *,
    active_domain: str | None = None,
) -> list[str]:
    """Remove unedited factory copies outside the active workflow/domain context."""
    from ..domains import BUILTIN_DOMAINS
    from .vertical_select import available_verticals

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
    for vertical in available_verticals():
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
    # Source-controlled bundled documents are interpreted by Agents. Runtime
    # validation only rejects an empty file and does not parse frontmatter.
    if not text.strip():
        raise ValueError(f"bundled Skill is empty: {filename}")
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    text = text.replace("\r\n", "\n")
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                try:
                    if path.read_bytes().replace(b"\r\n", b"\n") == text.encode("utf-8"):
                        return
                except OSError:
                    pass
                if attempt == 7:
                    raise
                time.sleep(min(0.02 * (2**attempt), 0.25))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
