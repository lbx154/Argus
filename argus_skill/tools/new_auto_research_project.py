"""Create a clean-slate EMNLP auto-research project from the AGENTS template."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ..skills.builtins import (
    AVAILABLE_DOMAINS,
    DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
    DOMAIN_DESCRIPTIONS,
    builtin_skill_source_path,
    iter_builtin_skill_texts,
    seed_builtin_skills,
    seed_builtin_skills_for_domain,
)

DEFAULT_PARENT = Path("/home/argustest")
DEFAULT_PREFIX = "agent-emnlp-auto-research-v"
DEFAULT_TEMPLATE = "agent-md-new-project-template.md"
DEFAULT_PROJECT_CODE_DIR = "code"
PYTHON = Path("/home/argustest/miniconda3/bin/python")
COPY_READY_HEADING = "## Copy-ready `AGENTS.md`"
TRAILER = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

STARTER_CODE_TEMPLATE_PACKAGE = "argus_skill.tools.project_templates.code"
STARTER_CODE_TEMPLATE_FILES = (
    "__init__.py",
    "llm.py",
    "generate_image_2.py",
    "generate_image2_figure.py",
)


class LaunchError(RuntimeError):
    """Raised when project bootstrapping cannot safely continue."""


@dataclass(frozen=True)
class LaunchConfig:
    parent: Path = DEFAULT_PARENT
    version: str | None = None
    project_dir: Path | None = None
    template_path: Path | None = None
    objective: str | None = None
    non_goals: str | None = None
    compute_budget: str | None = None
    domain: str | None = None  # e.g. "cv", "multimodal", "agent", "infra"
    start_daemon: bool = True
    init_git: bool = True
    dry_run: bool = False
    overwrite_empty: bool = True


@dataclass(frozen=True)
class LaunchResult:
    project_dir: Path
    agents_path: Path
    skills_dir: Path
    project_name: str
    version: str
    domain: str | None
    daemon_started: bool
    git_commit: str | None
    daemon_output: str
    status_output: str
    dry_run: bool = False


def extract_copy_ready_agents_md(template_text: str) -> str:
    """Return the copy-ready AGENTS.md body from a built-in template."""
    heading_index = template_text.find(COPY_READY_HEADING)
    if heading_index < 0:
        raise LaunchError(f"template is missing {COPY_READY_HEADING!r}")
    fence_start = template_text.find("```", heading_index)
    if fence_start < 0:
        raise LaunchError("template is missing the opening markdown fence")
    body_start = template_text.find("\n", fence_start)
    if body_start < 0:
        raise LaunchError("template opening fence is malformed")
    body_start += 1
    fence_end = template_text.find("\n```", body_start)
    if fence_end < 0:
        raise LaunchError("template is missing the closing markdown fence")
    body = template_text[body_start:fence_end].strip()
    if not body.startswith("# AGENTS.md"):
        raise LaunchError("copy-ready template body must start with '# AGENTS.md'")
    return body + "\n"


def render_agents_md(
    template_text: str,
    *,
    project_name: str,
    version: str,
    objective: str | None = None,
    non_goals: str | None = None,
    compute_budget: str | None = None,
) -> str:
    """Fill the project-specific fields in the copy-ready AGENTS.md template."""
    body = extract_copy_ready_agents_md(template_text)
    objective = objective or default_objective(project_name)
    non_goals = non_goals or default_non_goals(project_name, version)
    compute_budget = compute_budget or default_compute_budget()
    replacements = {
        "[write the target research problem and deliverable]": objective,
        "[write what must not be optimized, copied, or claimed]": non_goals,
        "[write limits and stop conditions]": compute_budget,
        "| [input] | [source] | [status] | [allowed use] | [rationale] |": (
            default_allowed_inputs_table_rows()
        ),
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    unresolved = [
        token
        for token in (
            "[write the target research problem and deliverable]",
            "[write what must not be optimized, copied, or claimed]",
            "[write limits and stop conditions]",
            "| [input] | [source] | [status] | [allowed use] | [rationale] |",
        )
        if token in body
    ]
    if unresolved:
        raise LaunchError(f"rendered AGENTS.md still has placeholders: {unresolved}")
    return body


def default_objective(project_name: str) -> str:
    return (
        f"Start {project_name} as a clean-slate EMNLP/ACL long-paper auto-research "
        "workspace: choose an independent frontier-domain thesis from current "
        "literature/source discovery, train or adapt a meaningful domain model with "
        "the available GPU budget, evaluate only on existing real benchmark sources "
        "or official task/data releases, run the full-scale evidence matrix, then write "
        "an exemplar-locked, visually polished submission package that passes the exact "
        "final `validate-full-emnlp` gate."
    )


def default_non_goals(project_name: str, version: str) -> str:
    return (
        f"Do not copy, rename, polish, or continue any prior `agent-emnlp-auto-research-v*` "
        f"workspace as {project_name}. Do not reuse prior titles, claims, benchmark "
        "episodes, results, figures, paper generators, review JSON, or paper story unless "
        "listed as allowed raw evidence with license/access status and a narrow rationale."
    )


def default_compute_budget() -> str:
    return (
        "Use API/model budget only for necessary literature, coding, image-2, review, and "
        "experiment work. Use local GPU capacity for the strongest feasible "
        "domain-appropriate training/adaptation run rather than defaulting to tiny "
        "custom scorers. Stop or ask for operator guidance if a required real benchmark, "
        "model weight/license, GPU capability, or full-scale run is unavailable, or if "
        "repeated repair cycles make no validator-relevant progress."
    )


def default_allowed_inputs_table_rows() -> str:
    return "\n".join(
        [
            "| Global research playbook | `/home/argustest/research.md` | local operator guidance | Paper-quality and research-process guidance only | Stable cross-project writing and validation policy |",
            "| Argus source tree | `/home/argustest/argus-skill` | local source | Validators, built-in skills, helper APIs, and daemon runtime | Required toolchain for this workspace |",
            "| Exported built-in skills | `./argus_builtin_skills/*.md`, `./argus_builtin_skills/**/*.md` | generated local copy | Read-only local skill guidance | Keeps the daemon self-contained without copying the whole Argus repository |",
            "| Public literature, datasets, and repositories | verified URLs/scholarly sources | source-specific license/access | Topic discovery, citations, benchmark construction, and baseline implementation | Must be recorded before use in research artifacts |",
        ]
    )


def load_template_text(template_path: Path | None = None) -> str:
    if template_path is not None:
        return template_path.read_text(encoding="utf-8")
    source_path = builtin_skill_source_path() / DEFAULT_TEMPLATE
    if source_path.exists():
        return source_path.read_text(encoding="utf-8")
    for filename, text in iter_builtin_skill_texts():
        if filename == DEFAULT_TEMPLATE:
            return text
    raise LaunchError(f"built-in template not found: {DEFAULT_TEMPLATE}")


def normalize_version(raw: str | None) -> str:
    if raw is None:
        raise LaunchError("version is required when project_dir is not provided")
    value = raw.strip()
    match = re.fullmatch(r"(?:v)?(\d+)", value)
    if not match:
        match = re.fullmatch(rf"{re.escape(DEFAULT_PREFIX)}(\d+)", value)
    if not match:
        raise LaunchError(f"version must look like '15', 'v15', or '{DEFAULT_PREFIX}15': {raw!r}")
    return f"v{int(match.group(1))}"


def next_version(parent: Path, *, prefix: str = DEFAULT_PREFIX) -> str:
    max_seen = 0
    if parent.exists():
        for child in parent.iterdir():
            if not child.is_dir() or not child.name.startswith(prefix):
                continue
            suffix = child.name[len(prefix):]
            if suffix.isdigit():
                max_seen = max(max_seen, int(suffix))
    return f"v{max_seen + 1}"


def resolve_project(config: LaunchConfig) -> tuple[Path, str, str]:
    parent = config.parent.expanduser().resolve()
    if config.project_dir is not None:
        project_dir = config.project_dir.expanduser().resolve()
        name = project_dir.name
        match = re.fullmatch(rf"{re.escape(DEFAULT_PREFIX)}(\d+)", name)
        version = f"v{int(match.group(1))}" if match else normalize_version(config.version or "1")
        return project_dir, name, version
    version = normalize_version(config.version) if config.version else next_version(parent)
    name = f"{DEFAULT_PREFIX}{version.removeprefix('v')}"
    return parent / name, name, version


def create_project(config: LaunchConfig) -> LaunchResult:
    project_dir, project_name, version = resolve_project(config)
    agents_path = project_dir / "AGENTS.md"
    skills_dir = project_dir / DEFAULT_PROJECT_BUILTIN_SKILLS_DIR
    template_text = load_template_text(config.template_path)
    agents_md = render_agents_md(
        template_text,
        project_name=project_name,
        version=version,
        objective=config.objective,
        non_goals=config.non_goals,
        compute_budget=config.compute_budget,
    )
    if config.dry_run:
        return LaunchResult(
            project_dir=project_dir,
            agents_path=agents_path,
            skills_dir=skills_dir,
            project_name=project_name,
            version=version,
            domain=config.domain,
            daemon_started=False,
            git_commit=None,
            daemon_output="",
            status_output="",
            dry_run=True,
        )
    _prepare_project_dir(project_dir, overwrite_empty=config.overwrite_empty)
    agents_path.write_text(agents_md, encoding="utf-8")
    if config.domain:
        seed_builtin_skills_for_domain(skills_dir, config.domain, overwrite=True)
    else:
        seed_builtin_skills(skills_dir, overwrite=True)
    seed_starter_code(project_dir, overwrite=True)
    seed_research_bootstrap(
        project_dir,
        project_name=project_name,
        objective=_continuous_objective_from_agents(agents_md),
        overwrite=True,
    )
    git_commit = init_git(project_dir, project_name) if config.init_git else None
    daemon_output = ""
    status_output = ""
    if config.start_daemon:
        daemon_output = start_daemon(project_dir, agents_md)
        status_output = status(project_dir)
    return LaunchResult(
        project_dir=project_dir,
        agents_path=agents_path,
        skills_dir=skills_dir,
        project_name=project_name,
        version=version,
        domain=config.domain,
        daemon_started=config.start_daemon,
        git_commit=git_commit,
        daemon_output=daemon_output,
        status_output=status_output,
    )


def _prepare_project_dir(project_dir: Path, *, overwrite_empty: bool) -> None:
    if project_dir.exists():
        if not project_dir.is_dir():
            raise LaunchError(f"project path exists but is not a directory: {project_dir}")
        if any(project_dir.iterdir()):
            raise LaunchError(
                f"project directory is not empty: {project_dir}. "
                "Use a new version/path; this launcher never deletes existing work."
            )
        if not overwrite_empty:
            raise LaunchError(f"project directory already exists: {project_dir}")
    else:
        project_dir.mkdir(parents=True)


def seed_starter_code(project_dir: Path, *, overwrite: bool = True) -> dict[Path, bool]:
    """Write starter project helper code into ``code/`` from bundled templates.

    Returns a map from written path to whether the file changed.
    """
    code_dir = project_dir / DEFAULT_PROJECT_CODE_DIR
    result: dict[Path, bool] = {}
    for relative_name, text in iter_starter_code_templates():
        target = code_dir / relative_name
        if target.exists() and not overwrite:
            result[target] = False
            continue
        old = target.read_text(encoding="utf-8") if target.exists() else None
        if old == text:
            result[target] = False
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        result[target] = True
    return result


def seed_research_bootstrap(
    project_dir: Path,
    *,
    project_name: str,
    objective: str,
    overwrite: bool = True,
) -> dict[Path, bool]:
    """Write the initial auto-research ledger without claiming readiness.

    The launcher always seeds starter helper code, so daemon empty-directory
    preflight cannot reliably infer that the research ledger is missing. Keep
    this bootstrap deterministic and conservative: it gives the first Engineer
    stable files to extend, while all downstream stages remain non-successful
    until real literature, benchmark, run, and paper artifacts exist.
    """
    files = _research_bootstrap_files(project_name=project_name, objective=objective)
    result: dict[Path, bool] = {}
    for relative_name, text in files.items():
        target = project_dir / relative_name
        if target.exists() and not overwrite:
            result[target] = False
            continue
        old = target.read_text(encoding="utf-8") if target.exists() else None
        if old == text:
            result[target] = False
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        result[target] = True
    return result


def _research_bootstrap_files(*, project_name: str, objective: str) -> dict[str, str]:
    pipeline_state = {
        "current_stage": "literature",
        "objective": objective,
        "target_venue": "EMNLP",
        "paper_scope": "long-paper",
        "stages": {
            "brief": {
                "status": "done",
                "artifact": "research/RESEARCH_BRIEF.md",
                "notes": "Launcher seed only; revise after literature/source discovery.",
            },
            "literature": {
                "status": "pending",
                "artifact": "research/LITERATURE_GROUNDING.json",
            },
            "novelty": {
                "status": "missing",
                "artifact": "research/IDEA_PROVENANCE.json",
            },
            "plan": {
                "status": "missing",
                "artifact": "research/EXPERIMENT_PLAN.md",
            },
            "benchmark": {
                "status": "missing",
                "artifact": "experiments/BENCHMARK_PROVENANCE.md",
            },
            "run": {"status": "missing"},
            "analysis": {"status": "missing"},
            "narrative": {"status": "missing"},
            "draft": {"status": "missing"},
            "assurance": {"status": "missing"},
            "revision": {"status": "missing"},
            "submission": {"status": "missing"},
        },
        "last_gate": {
            "verdict": "pending",
            "reason": (
                "official launcher seed only; final readiness requires completed "
                "literature grounding, full-scale evidence, paper reviews, and "
                "validate-full-emnlp exit 0"
            ),
        },
    }
    gate = (
        "PYTHONPATH=/home/argustest/argus-skill "
        "/home/argustest/miniconda3/bin/python -m "
        "argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root ."
    )
    return {
        "research/PIPELINE_STATE.json": json.dumps(
            pipeline_state,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "research/RESEARCH_BRIEF.md": (
            "# Research Brief\n\n"
            f"- Project: `{project_name}`\n"
            f"- Primary goal: {objective}\n"
            "- Target venue/scope: EMNLP/ACL long paper, 7.5-8 main-content pages, "
            "references/appendix starting on page 9 or later, and no total-page cap after the body.\n"
            "- Non-negotiable evidence bar: choose a frontier-domain problem, train or adapt "
            "a meaningful modern model when learning is involved, and use existing real "
            "benchmarks or official task/data releases for all paper-facing evidence.\n"
            "- Current stage: literature/source discovery.\n\n"
            "This is the official launcher seed. The next agent must replace this "
            "brief with a literature-grounded problem statement before selecting "
            "the final thesis, benchmark, or paper story.\n\n"
            f"Final gate: `{gate}`\n"
        ),
        "research/EXPERIMENT_PLAN.md": (
            "# Experiment Plan\n\n"
            "Seed scaffold only. Do not mark the plan stage ready until "
            "`research/LITERATURE_GROUNDING.json`, `research/IDEA_PROVENANCE.json`, "
            "`research/CODE_REUSE_PLAN.json`, `research/BASELINE_AND_BENCHMARK_PLAN.md`, "
            "and `experiments/BENCHMARK_PROVENANCE.md` contain source-backed content.\n\n"
            "Required method target: use the available GPU budget for a meaningful "
            "domain-appropriate trained/adapted model. Tiny scorers, prompt-only wrappers, "
            "and exact-oracle policies are baselines/smoke tests unless the operator "
            "explicitly lowers the scope.\n\n"
            "Required evidence target: at least 240 distinct scored main benchmark "
            "tasks or episodes for every required method/baseline condition, drawn from "
            "existing real benchmarks or official task/data releases, with raw rows under "
            "`experiments/**` and status/progress artifacts. Synthetic/local tasks are "
            "smoke-only and cannot support final paper claims.\n"
        ),
        "research/CLAIMS_TO_TEST.md": (
            "# Claims To Test\n\n"
            "Seed scaffold only. Add claims after the literature and benchmark "
            "plan identify the method, baselines, expected effects, ablations, "
            "robustness checks, and failure cases.\n"
        ),
        "research/GO_NO_GO.md": (
            "# Go / No-Go\n\n"
            "Initial decision: no-go for drafting. Advance only after the full "
            "benchmark matrix has completed and `validate-full-scale-evidence` "
            "passes on current experiment artifacts.\n"
        ),
        "experiments/BENCHMARK_PROVENANCE.md": (
            "# Benchmark Provenance\n\n"
            "Seed scaffold only. Record surveyed benchmark papers, repositories, "
            "licenses/access constraints, selected existing real benchmark sources, "
            "sampling/adaptation logic, and the final task schema before running "
            "experiments. Final evidence must not use synthetic/local benchmarks; "
            "synthetic tasks are allowed only as smoke tests and must be excluded from "
            "paper-facing result claims.\n"
        ),
        "experiments/MODEL_SCALE_PLAN.md": (
            "# Model Scale Plan\n\n"
            "Seed scaffold only. Before implementation, record the chosen model/backbone, "
            "parameter count, trainable parameter count, training/adaptation recipe, "
            "dataset size, GPU type/count, memory strategy, expected GPU-hours, checkpoint "
            "or adapter path, and why this is a meaningful frontier-domain model rather "
            "than a toy scorer.\n"
        ),
    }


def iter_starter_code_templates() -> list[tuple[str, str]]:
    """Return deterministic starter-code templates bundled with this package."""
    package_root = resources.files(STARTER_CODE_TEMPLATE_PACKAGE)
    templates: list[tuple[str, str]] = []
    for filename in STARTER_CODE_TEMPLATE_FILES:
        resource = package_root / filename
        templates.append((filename, resource.read_text(encoding="utf-8")))
    return templates


def init_git(project_dir: Path, project_name: str) -> str:
    _run(["git", "init", "--quiet"], cwd=project_dir)
    _run(
        [
            "git",
            "add",
            "AGENTS.md",
            DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
            DEFAULT_PROJECT_CODE_DIR,
            "research",
            "experiments",
        ],
        cwd=project_dir,
    )
    message = f"Seed {project_name} auto-research workspace\n\n{TRAILER}"
    _run(["git", "commit", "--quiet", "-m", message], cwd=project_dir)
    return _run(["git", "rev-parse", "--short", "HEAD"], cwd=project_dir).strip()


def start_daemon(project_dir: Path, agents_md: str) -> str:
    objective = _continuous_objective_from_agents(agents_md)
    return _run(
        [
            str(PYTHON if PYTHON.exists() else Path(sys.executable)),
            "-m",
            "argus_skill",
            "--daemon",
            "--continuous",
            "--objective",
            objective,
        ],
        cwd=project_dir,
        env=_argus_env(),
        timeout=90,
    )


def status(project_dir: Path) -> str:
    return _run(
        [
            str(PYTHON if PYTHON.exists() else Path(sys.executable)),
            "-m",
            "argus_skill",
            "--status",
        ],
        cwd=project_dir,
        env=_argus_env(),
        timeout=30,
    )


def _continuous_objective_from_agents(agents_md: str) -> str:
    for line in agents_md.splitlines():
        if line.startswith("- Primary paper goal: "):
            return line.split(": ", 1)[1]
    return "Start a clean-slate EMNLP/ACL long-paper auto-research project and continue until validate-full-emnlp exits 0."


def _argus_env() -> dict[str, str]:
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing}" if existing else str(repo_root)
    return env


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        rendered = " ".join(cmd)
        raise LaunchError(
            f"command failed with exit {proc.returncode}: {rendered}\n{output.strip()}"
        )
    return output


def _interactive_domain_select() -> str | None:
    """Interactively ask the user to pick a research domain."""
    print("\n🔬 Select a research domain (loads only relevant skills):\n")
    keys = list(AVAILABLE_DOMAINS.keys())
    for i, key in enumerate(keys, 1):
        desc = DOMAIN_DESCRIPTIONS[key]
        print(f"  {i}. {desc}")
    print(f"  {len(keys) + 1}. All domains (load everything — may be slow)")
    print()
    while True:
        try:
            choice = input("Domain [1-{}]: ".format(len(keys) + 1)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not choice:
            continue
        try:
            idx = int(choice)
        except ValueError:
            # Try matching by name
            if choice.lower() in AVAILABLE_DOMAINS:
                return choice.lower()
            print(f"  Invalid choice. Enter 1-{len(keys) + 1} or a domain name.")
            continue
        if 1 <= idx <= len(keys):
            selected = keys[idx - 1]
            print(f"\n  ✓ Selected: {DOMAIN_DESCRIPTIONS[selected]}\n")
            return selected
        elif idx == len(keys) + 1:
            print("\n  ✓ Loading all domains\n")
            return None
        else:
            print(f"  Invalid choice. Enter 1-{len(keys) + 1}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="new-auto-research-project",
        description=(
            "Create an agent-emnlp-auto-research-vN workspace from the bundled "
            "AGENTS.md template, export built-in skills, seed starter code, "
            "initialize git, and optionally start the Argus continuous daemon."
        ),
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="version to create, e.g. 15 or v15; omit to pick the next available version",
    )
    parser.add_argument(
        "--parent",
        type=Path,
        default=DEFAULT_PARENT,
        help=f"parent directory for versioned workspaces (default: {DEFAULT_PARENT})",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="explicit project directory instead of parent + version naming",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help=f"AGENTS template file (default: built-in {DEFAULT_TEMPLATE})",
    )
    parser.add_argument(
        "--objective",
        default=None,
        help="project-specific primary paper goal to place in AGENTS.md and daemon objective",
    )
    parser.add_argument(
        "--non-goals",
        default=None,
        help="project-specific non-goals to place in AGENTS.md",
    )
    parser.add_argument(
        "--compute-budget",
        default=None,
        help="project-specific compute/API budget and stop conditions",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="create the workspace but do not start the Argus daemon",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="skip git init/add/commit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved project path/version without creating files",
    )
    parser.add_argument(
        "--domain",
        choices=list(AVAILABLE_DOMAINS.keys()),
        default=None,
        help=(
            "research domain to activate (only loads domain-relevant skills). "
            "Available: " + ", ".join(f"{k} ({v})" for k, v in DOMAIN_DESCRIPTIONS.items())
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Interactive domain selection if not specified
    domain = args.domain
    if domain is None and sys.stdin.isatty() and not args.dry_run:
        domain = _interactive_domain_select()

    config = LaunchConfig(
        parent=args.parent,
        version=args.version,
        project_dir=args.project_dir,
        template_path=args.template,
        objective=args.objective,
        non_goals=args.non_goals,
        compute_budget=args.compute_budget,
        domain=domain,
        start_daemon=not args.no_start,
        init_git=not args.no_git,
        dry_run=args.dry_run,
    )
    try:
        result = create_project(config)
    except (LaunchError, OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"new-auto-research-project: {exc}\n")
        return 2
    print(format_result(result))
    return 0


def format_result(result: LaunchResult) -> str:
    lines = [
        f"project : {result.project_dir}",
        f"version : {result.version}",
        f"domain  : {result.domain or 'all (no filter)'}",
        f"AGENTS  : {result.agents_path}",
        f"skills  : {result.skills_dir}",
        f"code    : {result.project_dir / DEFAULT_PROJECT_CODE_DIR}",
    ]
    if result.dry_run:
        lines.append("dry-run : no files created")
        return "\n".join(lines)
    if result.git_commit:
        lines.append(f"commit  : {result.git_commit}")
    lines.append(f"daemon  : {'started' if result.daemon_started else 'not started'}")
    if result.daemon_output.strip():
        lines.append("")
        lines.append(result.daemon_output.strip())
    if result.status_output.strip():
        lines.append("")
        lines.append(result.status_output.strip())
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
