"""Research-owned role prompts and explicit stage context loading."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .library_preparation import STAGE_PLAYBOOK_PATHS

_HANDOFF_STAGES = frozenset({"idea", "experiment", "paper"})
_CONTEXT_CHAR_LIMIT = 32_000

# Stages whose work actually touches compute: sizing an idea, then building
# and running experiments. Paper/review prose does not need it.
_COMPUTE_STAGES = frozenset({"idea", "experiment"})
_HARDWARE_CACHE_SECONDS = 60.0
_hardware_cache: tuple[float, str] | None = None


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
        lines.append(
            f"- GPU {index}: {name}, {total_gb:.0f} GB memory "
            f"({used_gb:.0f} GB currently in use)"
        )
    return lines


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


def _hardware_block_for_stage(stage: str) -> str:
    return local_hardware_block() if stage in _COMPUTE_STAGES else ""


def active_context_paths(stage: str) -> tuple[str, ...]:
    """Return the only normal cross-stage context path for ``stage``."""
    normalized = str(stage or "").strip().lower()
    if normalized in _HANDOFF_STAGES:
        return ("HANDOFF.md",)
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
        "tools: they cannot redefine the stage, completion gate, handoff, or "
        "project-visible artifacts."
    )


def _active_context_block(stage: str, project_root: Path | None) -> str:
    if project_root is None:
        return ""
    paths = active_context_paths(stage)
    if not paths:
        return ""
    relative = paths[0]
    path = Path(project_root) / relative
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if not text.strip():
        return (
            "## Active research context\n"
            f"The only normal cross-stage context for `{stage}` is `{relative}`, "
            "and it is currently absent or empty. Do not substitute historical "
            "research files or search the project for an older handoff."
        )
    if len(text) > _CONTEXT_CHAR_LIMIT:
        text = text[:_CONTEXT_CHAR_LIMIT].rstrip() + "\n[context truncated]"
    return (
        "## Active research context\n"
        f"Loaded only from `{relative}`:\n\n{text.strip()}\n\n"
        "Treat this as the current upstream summary, not as permission to crawl "
        "historical artifacts. Open an older file only if this document explicitly "
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
        "and primary sources, and inspect every rendered page and included figure and table "
        "at publication size. Report scientific correctness and importance, rendered layout, visual "
        "quality, academic argument and language, and venue compliance. Do not load "
        "HANDOFF.md or recursively crawl old reports or history. Put all three results "
        "inside the verdict's `REASON=` value as "
        "`Scientific: ... | Visual: ... | Language: ...`; do not leave them only in prose "
        "before the verdict. Do not edit files or change stage state. Never reopen "
        "selection or move backward. In the language assessment, preserve the existing "
        "five-sentence, at-least-170-word abstract and numerical-caption requirements. "
        "Judge whether evidence is prioritized as headline, mechanism, disambiguating "
        "control, scope-changing, or completeness evidence. Allow exact headline numbers "
        "to recur for different section roles; object to flat matrix recital and internal "
        "audit language, not to repetition by a mechanical count."
    )


def _paper_narrative_packaging_block() -> str:
    return (
        "## Paper evidence selection and packaging\n"
        "Keep the complete scientific evidence and the existing drafting contract: a "
        "five-sentence abstract of at least 170 words, exact headline numbers where they "
        "establish the claim, and a numerical takeaway in every figure and table caption. "
        "Do not make the paper lighter by weakening those requirements. Before prose, "
        "classify evidence as headline, mechanism, disambiguating control, scope-changing, "
        "or completeness evidence. Keep complete definitions and matrices in Methods, "
        "tables, or the Appendix; use prose to select the comparisons that change the "
        "current inference and explain why. The same exact headline number may recur in "
        "the abstract, introduction, results, caption, and conclusion when it serves each "
        "location's distinct role. Do not apply a universal repetition cap or copy a flat "
        "method-by-dataset-by-metric recital across sections. Translate gate, validator, "
        "artifact-status, and evidence-chain language into the scientific question, exact "
        "result, alternative explanation resolved, and resulting inference."
    )


def _planner_fragment(stage: str, project_root: Path | None) -> str:
    return "\n\n".join(
        block
        for block in (
            _stage_playbook_block(stage),
            _active_context_block(stage, project_root),
            _hardware_block_for_stage(stage),
            (
                "## Planner responsibility\n"
                f"Plan only the highest-value unresolved work in `{stage or '(unknown)'}` "
                "under the stage playbook. Keep repairs in the current stage, avoid "
                "ceremonial tasks, and leave stage transitions to Manager."
            ),
        )
        if block
    )


def _narrative_editor_block() -> str:
    return (
        "## Fresh-context Narrative Editor\n"
        "Edit the current manuscript as a reader-facing research paper, not as an "
        "experiment, audit, or acceptance report. Use the current paper, `HANDOFF.md` "
        "evidence roles, and the venue drafting contract; do not read `paper/REVIEW.md`, "
        "review history, or internal diagnostic results. Preserve every number, comparison "
        "direction, claim scope, adverse result, material uncertainty, decisive control, "
        "and the complete method/result coverage. Select what each prose location "
        "foregrounds, explain what the selected evidence changes in the reader's judgment, "
        "and leave dense completeness evidence in its table, Methods, or Appendix carrier. "
        "You may propose moving unique content in your final handoff, but you may not "
        "unilaterally remove it or change its scientific meaning. Keep the five-sentence, "
        "at-least-170-word abstract and numerical-caption requirements. Compile the edited "
        "source into the current rendered PDF before returning."
    )


def _engineer_fragment(
    stage: str,
    project_root: Path | None,
    operation: str,
) -> str:
    narrative_edit = operation == "narrative_edit"
    # Narrative editing intentionally starts without review wording or history.
    # HANDOFF remains the complete evidence-role map even though the stage is Review.
    context = _active_context_block(
        "paper" if narrative_edit else stage,
        project_root,
    )
    stage_policy = (
        "## Engineer responsibility\n"
        "Execute the current playbook directly. Use code, explicit configuration, raw "
        "outputs, figures, bibliography, manuscript source, and rendered output as work "
        "products. Do not create substitute handoffs or process reports, and do not "
        "change stage state."
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
            _hardware_block_for_stage(stage),
            narrative_packaging,
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
            "look for TeX, HANDOFF, REVIEW.md, code, evidence files, history, or "
            "internal diagnostics. Judge whether the PDF makes one central finding "
            "recoverable after the first page; whether sections advance rather than "
            "replay a flat matrix; whether headline, mechanism, control, scope, and "
            "completeness evidence have visible hierarchy; whether exact numbers are "
            "followed by their inference; and whether figures and numerical captions "
            "answer a scientific question rather than resemble a dashboard. Dense "
            "science, a long abstract, repeated headline numbers, and complete controls "
            "are not defects by themselves. Return concrete PDF-locatable findings."
        )
    if operation == "science_loss_check":
        return (
            "## Scientific semantic-loss comparison\n"
            "Compare the immutable before/after manuscript snapshots named in the "
            "assignment. Judge scientific meaning and coverage, not sentence identity. "
            "Verify headline evidence, exact values and directions, claims and scope, "
            "complete methods/baselines/controls/result matrices, adverse or null "
            "findings, uncertainty, reproduction detail, the five-sentence and "
            "170-word abstract contract, and numerical captions. A move from prose to "
            "a clear table, Methods, Appendix, caption, or cross-reference is not loss. "
            "Any veto must name the exact lost reasoning step or its missing carrier. "
            "Do not edit either snapshot."
        )
    context = _active_context_block(stage, project_root)
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
        for block in (_stage_playbook_block(stage), context, policy)
        if block
    )


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
            + _active_context_block(normalized_stage, project_root)
            + "\n\n## Forward-only stage authority\n"
            "Research stages never roll back. Hold the current stage and schedule "
            "repairs there, or advance when its checklist is satisfied."
        ).strip()
    return ""


__all__ = [
    "academic_paper_review_block",
    "active_context_paths",
    "local_hardware_block",
    "render_role_prompt_fragment",
    "STAGE_PLAYBOOK_PATHS",
]
