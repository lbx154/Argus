"""Research-owned role prompts and explicit stage context loading."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .library_preparation import STAGE_PLAYBOOK_PATHS
from .notes import RESEARCH_NOTES_FILENAME, read_research_notes

_NOTES_STAGES = frozenset({"idea", "experiment", "paper"})
_CONTEXT_CHAR_LIMIT = 32_000

# Stages whose work actually touches compute: sizing an idea, then building
# and running experiments. Paper/review prose does not need it.
_COMPUTE_STAGES = frozenset({"idea", "experiment"})
_HARDWARE_CACHE_SECONDS = 60.0
_hardware_cache: tuple[float, str] | None = None
# Local checkpoint inventory: scanning a few cache directories is cheap, but
# not so cheap that every prompt render should redo it.
_model_inventory_cache: dict[str, tuple[float, str]] = {}
# Prompt budget: the largest checkpoints are the ones a claim about scale
# needs; beyond this many the list stops informing and starts crowding.
_MODEL_INVENTORY_LIMIT = 40
_MODEL_CACHE_DIRS_KNOB = "ARGUS_SKILL_MODEL_CACHE_DIRS"
_PROJECT_SCAN_DEPTH = 3
_SKIPPED_DIR_NAMES = frozenset({"node_modules", "site-packages", "__pycache__"})


def _query_local_gpus() -> list[str]:
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    lines: list[str] = []
    for row in proc.stdout.strip().splitlines():
        parts = [part.strip() for part in row.split(",")]
        if len(parts) != 4:
            continue
        index, name, total_mib, used_mib = parts
        try:
            total_gb = int(total_mib) / 1024
            used_gb = int(used_mib) / 1024
        except ValueError:
            continue
        free_gb = max(total_gb - used_gb, 0.0)
        lines.append(
            f"- GPU {index}: {name}, {total_gb:.0f} GB memory "
            f"({free_gb:.0f} GB free, {used_gb:.0f} GB in use by running jobs)"
        )
    return lines


def _hub_cache_dirs(project_root: Path | None) -> list[Path]:
    """Every place Hugging Face weights may already sit on this machine."""
    env = os.environ
    candidates: list[Path] = []
    if env.get("HF_HUB_CACHE"):
        candidates.append(Path(env["HF_HUB_CACHE"]))
    if env.get("HF_HOME"):
        candidates.append(Path(env["HF_HOME"]) / "hub")
    if env.get("TRANSFORMERS_CACHE"):
        candidates.append(Path(env["TRANSFORMERS_CACHE"]))
    candidates.append(Path.home() / ".cache" / "huggingface" / "hub")
    from ...core.knobs import resolve_knob

    configured = resolve_knob(_MODEL_CACHE_DIRS_KNOB, "").value
    for raw in configured.split(os.pathsep):
        if raw.strip():
            candidates.append(Path(raw.strip()).expanduser())
    if project_root is not None:
        candidates.extend(_project_local_hub_dirs(Path(project_root)))
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _project_local_hub_dirs(root: Path) -> list[Path]:
    """Directories a few levels under the project that hold ``models--*``."""
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        holds_models = False
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name
            if name.startswith("models--") or name.startswith("datasets--"):
                holds_models = True
                continue
            if name.startswith(".") or name in _SKIPPED_DIR_NAMES:
                continue
            if depth + 1 <= _PROJECT_SCAN_DEPTH:
                stack.append((Path(entry.path), depth + 1))
        if holds_models:
            found.append(directory)
    return found


def _snapshot_weight_bytes(snapshot: Path) -> int:
    """Check standard HF weight-file completeness without reading tensors."""
    try:
        config = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return 0
    except (OSError, ValueError):
        return 0

    def complete_size(names: set[str]) -> int:
        files: dict[Path, int] = {}
        try:
            for name in names:
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    return 0
                path = (snapshot / relative).resolve(strict=True)
                if not path.is_file() or path.name.endswith(".incomplete"):
                    return 0
                size = path.stat().st_size
                if size <= 0:
                    return 0
                files[path] = size
        except (OSError, RuntimeError):
            return 0
        return sum(files.values())

    for filename in ("model.safetensors", "pytorch_model.bin"):
        size = complete_size({filename})
        if size:
            return size
        try:
            index = json.loads(
                (snapshot / f"{filename}.index.json").read_text(encoding="utf-8")
            )
            mapping = index.get("weight_map") if isinstance(index, dict) else None
            if not isinstance(mapping, dict) or not mapping:
                continue
            names = list(mapping.values())
            if any(
                not isinstance(name, str) or not name
                or Path(name).suffix != Path(filename).suffix
                for name in names
            ):
                continue
            size = complete_size(set(names))
            if size:
                return size
        except (OSError, ValueError):
            continue
    return 0


def _query_local_models(project_root: Path | None) -> tuple[list[tuple[str, int, Path]], list[str]]:
    """Return ``(models, datasets)`` found in local Hugging Face caches.

    Models are ``(repo_id, weight_bytes, snapshot_dir)``. Only snapshots with
    configuration and a complete standard weight-file set are included;
    orphan blobs, metadata-only downloads, and partial shards are not models.
    """
    models: dict[str, tuple[int, Path]] = {}
    datasets: set[str] = set()
    for hub in _hub_cache_dirs(project_root):
        try:
            entries = list(os.scandir(hub))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name.startswith("datasets--"):
                datasets.add(entry.name[len("datasets--"):].replace("--", "/", 1))
                continue
            if not entry.name.startswith("models--"):
                continue
            repo_id = entry.name[len("models--"):].replace("--", "/", 1)
            try:
                snapshots = sorted((Path(entry.path) / "snapshots").iterdir())
            except OSError:
                continue
            for snapshot in snapshots:
                size = _snapshot_weight_bytes(snapshot)
                if size <= 0:
                    continue
                known = models.get(repo_id)
                if known is None or size > known[0]:
                    models[repo_id] = (size, snapshot)
    ordered = sorted(
        ((repo_id, size, hub) for repo_id, (size, hub) in models.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return ordered, sorted(datasets)


def local_model_inventory_block(project_root: Path | None = None) -> str:
    """List the checkpoints already on disk so experiments are sized to what
    is here instead of to the one model that happened to be handy.

    Informational only. Cached briefly per project root; fail-soft to an
    empty string when no cache holds any weights.
    """
    key = str(Path(project_root).resolve()) if project_root is not None else ""
    now = time.monotonic()
    cached = _model_inventory_cache.get(key)
    if cached is not None and now - cached[0] < _HARDWARE_CACHE_SECONDS:
        return cached[1]
    models, datasets = _query_local_models(project_root)
    if not models and not datasets:
        _model_inventory_cache[key] = (now, "")
        return ""
    lines: list[str] = []
    for repo_id, size, hub in models[:_MODEL_INVENTORY_LIMIT]:
        shown = f"{size / 1024**3:.1f} GB" if size >= 1024**3 else f"{size / 1024**2:.0f} MB"
        lines.append(f"- `{repo_id}` ({shown}) in `{hub}`")
    if len(models) > _MODEL_INVENTORY_LIMIT:
        lines.append(f"- ... and {len(models) - _MODEL_INVENTORY_LIMIT} smaller checkpoints")
    dataset_line = (
        "Dataset cache directories: " + ", ".join(f"`{name}`" for name in datasets[:_MODEL_INVENTORY_LIMIT])
        if datasets
        else ""
    )
    block = (
        "## Model weights already on this machine\n"
        "These local snapshots contain configuration and complete standard weight "
        "files. Use the snapshot path to reuse those files; tokenizer, dependencies, "
        "and runtime compatibility have not been tested.\n"
        + "\n".join(lines)
        + (f"\n{dataset_line}" if dataset_line else "")
        + "\n\n"
        "A claim about language models in general is tested across families "
        "and sizes, not on the one model that happened to be handy. The weights "
        "above are the cheapest way to widen a comparison, and several of them "
        "can be evaluated at once on separate GPUs."
    )
    _model_inventory_cache[key] = (now, block)
    return block


def local_hardware_block() -> str:
    """Describe the compute this machine actually has, so ideas and
    experiments are sized to it.

    Purely informational — it never blocks anything. Cached briefly so prompt
    rendering does not shell out on every turn; fail-soft to an empty string
    on machines without GPUs or without ``nvidia-smi``.
    """
    global _hardware_cache
    now = time.monotonic()
    if _hardware_cache is not None and now - _hardware_cache[0] < _HARDWARE_CACHE_SECONDS:
        return _hardware_cache[1]
    gpu_lines = _query_local_gpus()
    if not gpu_lines:
        _hardware_cache = (now, "")
        return ""
    cpu_count = os.cpu_count() or 0
    cpu_line = f"- {cpu_count} CPU cores" if cpu_count else ""
    block = (
        "## Compute available on this machine\n"
        + "\n".join(line for line in (*gpu_lines, cpu_line) if line)
        + "\n\n"
        "Experiments run locally on this hardware. Size the work to it rather "
        "than assuming a small machine: real training and evaluation runs on "
        "these GPUs are expected, several GPUs can be used at once when a run "
        "benefits, and batch sizes, model scale, and evaluation sets should "
        "use the memory that is actually free. Prefer the GPUs with the most "
        "free memory and leave others' running jobs undisturbed."
    )
    _hardware_cache = (now, block)
    return block


def _hardware_block_for_stage(stage: str, project_root: Path | None = None) -> str:
    if stage not in _COMPUTE_STAGES:
        return ""
    return "\n\n".join(
        block
        for block in (local_hardware_block(), local_model_inventory_block(project_root))
        if block
    )


def research_runtime_context(stage: str, project_root: Path | None = None) -> str:
    """Live resource facts for a Reviewer's delta, outside its policy hash."""
    return _hardware_block_for_stage(stage, project_root)


def active_context_paths(stage: str) -> tuple[str, ...]:
    """Return the only normal cross-stage context path for ``stage``."""
    normalized = str(stage or "").strip().lower()
    if normalized in _NOTES_STAGES:
        return (RESEARCH_NOTES_FILENAME,)
    if normalized == "review":
        return ("paper/REVIEW.md",)
    return ()


def _stage_playbook_block(stage: str) -> str:
    playbook = STAGE_PLAYBOOK_PATHS.get(stage)
    if not playbook:
        return ""
    resolved = Path(__file__).resolve().parent / "skills" / playbook
    return (
        "## Authoritative stage playbook\n"
        f"Playbook: `{playbook}`. Open `{resolved}` before acting. It is "
        f"the single workflow playbook for `{stage}`. Other Skills are optional "
        "tools: they cannot redefine the stage, its completion bar, the research "
        "notes in RESEARCH_NOTES.md, or the files the project shows."
    )


def active_research_context(stage: str, project_root: Path | None) -> str:
    if project_root is None:
        return ""
    paths = active_context_paths(stage)
    if not paths:
        return ""
    relative = paths[0]
    if relative == RESEARCH_NOTES_FILENAME:
        text = read_research_notes(project_root)
    else:
        try:
            text = (Path(project_root) / relative).read_text(encoding="utf-8")
        except OSError:
            text = ""
    if not text.strip():
        return (
            "## Active research context\n"
            f"The only normal cross-stage context for `{stage}` is `{relative}`, "
            "and it is currently absent or empty. Do not substitute historical "
            "research files or search the project for an older version of it."
        )
    if len(text) > _CONTEXT_CHAR_LIMIT:
        text = text[:_CONTEXT_CHAR_LIMIT].rstrip() + "\n[context truncated]"
    return (
        "## Active research context\n"
        f"Loaded only from `{relative}`:\n\n{text.strip()}\n\n"
        "Treat this as the current upstream summary, not as permission to crawl "
        "historical files. Open an older file only if this document explicitly "
        "names it for a concrete dispute."
    )


def academic_paper_review_block() -> str:
    return (
        "## Integrated final paper review\n"
        "Act as the independent post-repair Reviewer required by the Review playbook. "
        "Judge the current complete paper rather than Engineer or Planner confidence. "
        "Follow direct claim-critical references to executed code, explicit "
        "configuration, raw rows, the real evaluator, positive controls, strong "
        "same-information baselines, citations, "
        "and primary sources. Use the host's current independent page-by-page and cold-read "
        "assessments when supplied; do not launch duplicate passes or repeat their whole-paper "
        "inspection. Resolve a concrete contradiction with a targeted check. When no current "
        "assessment is supplied, inspect every rendered page, figure, and table at publication "
        "size yourself; that inspection is the assessment, and the absence of host-side "
        "passes is never by itself a reason to withhold `done` or to wait for the host. "
        "Report scientific correctness and importance, rendered layout, visual "
        "quality, academic argument and language, and whether the paper follows the "
        "venue's rules. Do not load the research notes or crawl old reports or history. "
        "Put all three results inside the `REASON=` value of your closing lines as "
        "`Scientific: ... | Visual: ... | Language: ...`; do not leave them only in the "
        "prose above. Do not edit files or change stage state. Never reopen "
        "selection or move backward. "
        + paper_reviewer_standard()
        + " For each required "
        "narrative repair, identify its location, the concrete obstacle to understanding "
        "or inference, and the smallest repair goal. Calling prose report-like, "
        "unacademic, or less fluent is insufficient by itself. Close resolved findings; "
        "request another revision only for a remaining or newly introduced defect."
    )


# Workflow vocabulary that must never leak into a manuscript. The words are
# quoted to the model as tokens, one per tuple entry, so the prompt can name
# them without the prose itself speaking that way.
_WORKFLOW_WORDS_KEPT_OUT_OF_MANUSCRIPTS = (
    "bounded",
    "certified",
    "gate",
    "artifact",
    "mission",
    "round",
    "handoff",
    "validator",
    "audit",
)


def paper_writing_standard() -> str:
    """The one writing standard every paper-facing prompt shares.

    It deliberately fixes no quota. The selected venue's strong accepted papers
    are the reference, and the claim decides how long, how numerical, and how
    hedged each passage should be.
    """
    return (
        "The standard is a strong accepted paper at the selected venue, the kind the "
        "exemplar skill has you read; there is no house quota for sentences, words, "
        "numbers, or caption format. Let the claim decide the form. "
        "Apply 'Plan the manuscript length' in research-paper-playbook.md: "
        "for a full-length paper, target nearly all permitted body space under "
        "the exact track's official counting rules, not total PDF pages. "
        "Respect explicit short-paper and partial-edit requests. Actively develop "
        "principle-level analysis toward that target: mechanisms, assumptions, "
        "derivations, and design tradeoffs. Experiments support the argument; do not write "
        "an experiment report. Record the target and actual body extent "
        "in existing research notes. The abstract is as "
        "long and as numerical as the venue's norm and the claim require: a large "
        "speedup is stated as a speedup, a narrow margin is stated with its "
        "uncertainty, and a mechanism finding may need no number at all. In prose, "
        "give a number the precision the comparison needs, usually two or three "
        "significant digits, and keep full precision in tables; a paragraph that has "
        "become a list of numbers has stopped arguing. A caption tells the reader what "
        "to see: a number when the number is the point, a pattern when the pattern is "
        "the point. Say plainly what the evidence establishes, state each limit once "
        "where it matters, and hedge a sentence only when the evidence for that "
        "sentence is uncertain. Think in evidence roles (headline, mechanism, control, "
        "scope, completeness) while deciding what goes where, but those words, and "
        "every workflow word such as "
        f"{', '.join(_WORKFLOW_WORDS_KEPT_OUT_OF_MANUSCRIPTS[:-1])}, or "
        f"{_WORKFLOW_WORDS_KEPT_OUT_OF_MANUSCRIPTS[-1]}, "
        "never appear in the manuscript. A clear "
        "thesis that a method helps only under identified conditions, or that an "
        "expected effect does not hold, is a legitimate paper when its evidence is as "
        "complete as a positive result would need; what is not allowed is presenting "
        "unfinished development as a finding."
    )


def paper_reviewer_standard() -> str:
    """How the Reviewer applies the writing standard: as a venue reviewer, not a checker."""
    return (
        "Judge the writing as a reviewer at the selected venue would: would this be "
        "accepted, and what would a careful reader object to? Do not enforce an "
        "abstract length, sentence count, number density, or caption format; a longer "
        "or shorter abstract, more or fewer numbers, and a headline figure that recurs "
        "across sections are all fine when they serve the argument at that venue. "
        "Object when a claim outruns its evidence, when a reader cannot recover the "
        "central finding, when a number's meaning is unclear from its context, when "
        "prose recites a result matrix instead of arguing, when hedging or limitation "
        "lists stand in for a clear statement, or when internal workflow vocabulary "
        "appears. Do not ask for more hedging than the evidence requires, and do not "
        "ask for a number where a plain statement is clearer. "
        "Apply the manuscript-length policy in research-paper-playbook.md: "
        "compare counted body extent with the full-paper writing target, "
        "not total PDF pages. Judge the depth of principle-level analysis and "
        "request expansion of terse mechanisms, derivations, and design tradeoffs. "
        "Experiments should support the argument, "
        "not turn it into an experiment report. Put actionable expansion requests "
        "in the existing paper/REVIEW.md, "
        "respecting explicit short-paper and partial-edit requests."
    )


def _paper_narrative_packaging_block() -> str:
    return (
        "## Paper writing standard\n"
        + paper_writing_standard()
        + " Keep the complete scientific evidence: complete definitions and matrices "
        "live in Methods, tables, or the Appendix, and prose selects the comparisons "
        "that change the current inference and explains why. A headline number may "
        "recur in the abstract, introduction, results, caption, and conclusion when it "
        "does each location's job; do not copy a flat method-by-dataset-by-metric "
        "recital across sections. Translate any workflow or evidence-bookkeeping "
        "language into the scientific question, the result, the "
        "alternative explanation resolved, and the resulting inference."
    )


def _planner_fragment(stage: str, project_root: Path | None) -> str:
    return "\n\n".join(
        block
        for block in (
            _stage_playbook_block(stage),
            active_research_context(stage, project_root),
            _hardware_block_for_stage(stage, project_root),
            (
                "## Post-result experiment scale assessment\n"
                "After Reviewer accepts the current experiment, apply the "
                "'Planner scale assessment after a passing experiment' section of "
                "research-experiment-playbook.md before leaving Experiment. "
                "Assess actual training and evaluation coverage against the operator "
                "objective, not just the run's acceptance checks. If insufficient, "
                "keep the stage and assign an incremental scale-up, preferring "
                "existing benchmarks and reusing valid code, configurations, and results. "
                "Apply the playbook's cost-aware benchmark policy: small custom "
                "benchmarks are allowed with no API calls or only a small amount "
                "within the authorized budget; reassess total cost when expanding. "
                "Respect stricter project-specific restrictions. "
                "If sufficient, explain why in the existing plan and REASON, "
                "then return ADVANCE_TO_STAGE=paper with the paper task. "
                "Do not schedule a separate inspection mission or repeat an "
                "unchanged assessment; perform this judgment in your normal "
                "planning turn."
                if stage == "experiment"
                else ""
            ),
            (
                "## Planner responsibility\n"
                f"Plan only the highest-value unresolved work in `{stage or '(unknown)'}` "
                "under the stage playbook. Keep repairs in the current stage, avoid "
                "ceremonial tasks, and leave stage transitions to Manager. A hypothesis "
                "the evidence has refuted closes its family of repairs: do not schedule "
                "another variant of the same objective under a new title; schedule the "
                "re-derivation of the thesis from what the evidence establishes and its "
                "confirmation on untouched data at the scale the claim needs. In `paper`, "
                "schedule writing; a run belongs there only for a specific evidence gap "
                "the manuscript exposed. Retire the refuted family's pending tasks "
                "with RETIRE_TASK."
            ),
        )
        if block
    )


def _narrative_editor_block() -> str:
    return (
        "## Fresh-context Narrative Editor\n"
        "Keep the current manuscript as the starting point. Inspect it and the latest "
        "actionable Reviewer findings supplied for this round; edit only a located "
        "problem that impairs reader understanding or the argument. Use the current "
        "paper, the evidence roles in the research notes (`RESEARCH_NOTES.md`), the venue drafting skill, and "
        "`engineer/references/paper-writing-craft.md` for how the repair should read. Do not "
        "search review history or internal diagnostic reports, or copy reviewer-response "
        "wording into the manuscript. Preserve clear content, structure, and wording. "
        "Prefer adding a missing explanation or adjusting local sentence order; explain "
        "why a local repair is insufficient before reorganizing a section or the paper. "
        "If no concrete problem needs repair, report that no manuscript change is needed "
        "and return to Reviewer without editing. Preserve every number, comparison "
        "direction, claim scope, adverse result, material uncertainty, decisive control, "
        "and the complete method/result coverage. Within the affected passage, clarify "
        "what the evidence establishes using only supported inferences; keep other "
        "evidence in its existing carrier. "
        "You may propose moving unique content in your closing note, but you may not "
        "unilaterally remove it or change its scientific meaning. Keep the abstract's "
        "claims and evidence; its length and shape follow the venue and the claim, not a "
        "quota. Compile when "
        "manuscript inputs changed or the rendered PDF is missing or stale; reuse a "
        "current PDF when no input changed."
    )


def _engineer_fragment(
    stage: str,
    project_root: Path | None,
    operation: str,
) -> str:
    narrative_edit = operation == "narrative_edit"
    # The research notes supply evidence roles; current repair feedback arrives through
    # the normal round context. Do not preload REVIEW.md or historical reports.
    context = active_research_context(
        "paper" if narrative_edit else stage,
        project_root,
    )
    stage_policy = (
        "## Engineer responsibility\n"
        "Execute the current playbook directly. Use code, explicit configuration, raw "
        "outputs, figures, bibliography, manuscript source, and rendered output as work "
        "products. Do not create substitute summaries or process reports, and do not "
        "change stage state. The host runs independent preliminary paper reviews after your "
        "turn; do not spawn a second scientific, visual, or cold-read review team."
    )
    narrative_packaging = (
        _paper_narrative_packaging_block()
        if stage == "paper" or narrative_edit
        else ""
    )
    return "\n\n".join(
        block
        for block in (
            _stage_playbook_block(stage),
            context,
            _hardware_block_for_stage(stage, project_root),
            narrative_packaging,
            (
                "## On-demand concept figure\n"
                "Only when a conceptual, method, or architecture figure needs drawing, "
                "open engineer/paper-framework-figure-studio.md. Default to Method D: "
                "reference figures, an authorized image-API design blueprint, then "
                "editable SVG reconstruction and native PPTX through PPT Master. "
                "Method B direct local vector drawing is the disclosed fallback; "
                "engineer/research-svg-pipeline.md provides its pipeline renderer. "
                "Reuse an existing suitable figure; do not invoke the component every "
                "round or for prose-only edits. Reuse a suitable blueprint without "
                "another paid request. Follow the skill's budget, privacy, model-choice "
                "and fallback requirements; generated content is not evidence. Include "
                "the vector PDF after the Introduction, targeting page 2 or 3 in the "
                "compiled paper, and keep the editable SVG source."
                if stage == "paper" and not narrative_edit else ""
            ),
            _narrative_editor_block() if narrative_edit else "",
            stage_policy,
        )
        if block
    )


def _reviewer_fragment(
    stage: str,
    scope: str,
    project_root: Path | None,
    operation: str,
) -> str:
    if operation == "cold_read":
        return (
            "## Rendered-PDF cold read\n"
            "Read only `paper/main.pdf` in the isolated working directory. Do not "
            "look for TeX, the research notes, REVIEW.md, code, evidence files, history, or "
            "internal diagnostics. Judge whether the PDF makes one central finding "
            "recoverable after the first page; whether sections advance rather than "
            "replay a flat matrix; whether headline, mechanism, control, scope, and "
            "completeness evidence have visible hierarchy; whether the scientific meaning "
            "of key comparisons is clear from the passage and necessary context; and "
            "whether figures and numerical captions "
            "answer a scientific question rather than resemble a dashboard. "
            + paper_reviewer_standard()
            + " Dense science and complete controls are not defects by themselves. "
            "Do not demand another explanation after "
            "each number when the context already supplies it. For each required repair, "
            "return a PDF location, a concrete obstacle to understanding or inference, "
            "and the smallest repair goal. A report-like tone or a preference for "
            "smoother wording alone is insufficient. Pass when no substantive "
            "reader-facing defect remains."
        )
    if operation == "science_loss_check":
        return (
            "## Scientific semantic-loss comparison\n"
            "Compare the immutable before/after manuscript snapshots named in the "
            "assignment. Judge scientific meaning and coverage, not sentence identity. "
            "Verify headline evidence, exact values and directions, claims and scope, "
            "complete methods/baselines/controls/result matrices, adverse or null "
            "findings, uncertainty, reproduction detail, the abstract's claims and "
            "evidence, and what each caption tells the reader. A move from prose to "
            "a clear table, Methods, Appendix, caption, or cross-reference is not loss. "
            "Any veto must name the exact lost reasoning step or its missing carrier. "
            "Do not edit either snapshot."
        )
    if stage == "review" or scope == "final_submission":
        policy = academic_paper_review_block()
    else:
        policy = (
            "## Reviewer responsibility\n"
            "Independently judge the current work against the stage playbook and direct "
            "evidence. Separate implementation defects from scientific evidence, name "
            "the smallest decisive repair, and do not change stage state."
        )
    return "\n\n".join(
        block
        # Live notes/REVIEW contents belong to the Reviewer's round delta, not
        # this static policy fragment used to decide whether a session resumes.
        for block in (
            _stage_playbook_block(stage),
            policy,
        )
        if block
    )


def render_role_prompt_context(
    *,
    role: str,
    operation: str,
    stage: str,
    scope: str,
    project_root: Path | None,
) -> str:
    """Keep changing research evidence in the round delta, not the static policy."""
    if role == "reviewer" and operation == "evaluate":
        return "\n\n".join(
            block for block in (
                active_research_context(stage, project_root),
                research_runtime_context(stage, project_root),
            ) if block
        )
    return ""


def render_role_prompt_fragment(
    *,
    role: str,
    operation: str,
    stage: str,
    scope: str,
    project_root: Path | None,
) -> str:
    """Render only policy owned by the Research vertical."""
    normalized_role = str(role or "").strip().lower()
    normalized_operation = str(operation or "").strip().lower()
    normalized_stage = str(stage or "").strip().lower()
    normalized_scope = str(scope or "").strip().lower().replace("-", "_")
    if normalized_role == "planner":
        return _planner_fragment(normalized_stage, project_root)
    if normalized_role == "engineer":
        return _engineer_fragment(
            normalized_stage,
            project_root,
            normalized_operation,
        )
    if normalized_role == "reviewer":
        return _reviewer_fragment(
            normalized_stage,
            normalized_scope,
            project_root,
            normalized_operation,
        )
    if normalized_role == "manager":
        return (
            _stage_playbook_block(normalized_stage)
            + "\n\n"
            + active_research_context(normalized_stage, project_root)
            + "\n\n## Forward-only stage authority\n"
            "Research stages never roll back. Hold the current stage and schedule "
            "repairs there, or advance when the stage's work is complete."
        ).strip()
    return ""


__all__ = [
    "academic_paper_review_block",
    "paper_reviewer_standard",
    "paper_writing_standard",
    "active_research_context",
    "active_context_paths",
    "local_hardware_block",
    "local_model_inventory_block",
    "research_runtime_context",
    "render_role_prompt_fragment",
    "render_role_prompt_context",
    "STAGE_PLAYBOOK_PATHS",
]
