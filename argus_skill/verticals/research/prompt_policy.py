"""Research-owned role prompts and explicit stage context loading."""

from __future__ import annotations

from pathlib import Path

_HANDOFF_STAGES = frozenset({"idea", "build", "experiment", "paper"})
_CONTEXT_CHAR_LIMIT = 32_000


def active_context_paths(stage: str) -> tuple[str, ...]:
    """Return the only normal cross-stage context path for ``stage``."""
    normalized = str(stage or "").strip().lower()
    if normalized in _HANDOFF_STAGES:
        return ("HANDOFF.md",)
    if normalized == "review":
        return ("paper/REVIEW.md",)
    return ()


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
        "Start with `paper/main.tex`, its rendered output, and `paper/REVIEW.md`. "
        "Follow only direct claim-critical references to executed code, explicit "
        "configuration, raw rows, the real evaluator, and primary sources. Confirm that "
        "the three preliminary passes inspected the same pre-repair paper, then "
        "independently reassess the repaired current version across all three dimensions.\n\n"
        "Scientific: judge novelty, correctness, claim-to-code fidelity, positive controls, "
        "strong same-information baselines, evidence scale, citations, and complete support "
        "for the thesis.\n"
        "Visual: inspect every rendered page and every included figure and table at "
        "publication scale. Reject visible overlap, clipping, overflow, connector "
        "penetration, wrong arrows, unreadable labels, malformed tables, misleading plots, "
        "abnormal whitespace, broken float placement, or inconsistent typography.\n"
        "Language: require confident, precise academic prose with a coherent argument. "
        "Reject defensive qualifier boilerplate, experiment chronology, internal workflow "
        "language, repeated caveats, and integrity self-praise.\n\n"
        "Check venue compliance and rendered layout across the complete source and paper. "
        "Put all three results inside the verdict's `REASON=` value as "
        "`Scientific: ... | Visual: ... | Language: ...`; do not leave them only in prose "
        "before the verdict. Do not load HANDOFF.md or "
        "recursively crawl old reports or history. Independently reject a weak idea, broken "
        "realization, stale baseline, unsupported claim, or unfinished paper even when "
        "Engineer is confident. Keep repairs in Review; never reopen selection or move "
        "backward. Overwrite `paper/REVIEW.md` only with the strongest accept case, "
        "reject-level issues, verdict, and next action. Do not create another review file "
        "or review history."
    )


def _planner_fragment(stage: str, project_root: Path | None) -> str:
    review_sequence = ""
    if stage == "review":
        review_sequence = (
            "## Final Review sequence\n"
            "The Reviewer first runs scientific completeness, strict rendered visual "
            "inspection, and academic-language review concurrently in read-only mode. "
            "Schedule one Engineer task to apply their combined findings and recompile. "
            "The next independent Reviewer performs integrated final acceptance. Write "
            "only `paper/REVIEW.md` as the project review file."
        )
    return "\n\n".join(
        block
        for block in (
            _active_context_block(stage, project_root),
            (
                "## Forward-only research planning\n"
                f"Current stage: `{stage or '(unknown)'}`. Plan repairs and new work "
                "inside this stage. Never request rollback. If a later stage exposes an "
                "earlier method, experiment, or manuscript defect, keep the current "
                "stage and schedule the concrete repair there. At a transition, replace "
                "`HANDOFF.md` completely with only the minimum context the next stage "
                f"needs, starting with `# HANDOFF — {stage.upper()}`. Never append."
            ),
            review_sequence,
            (
                "## Paper entry and writing policy\n"
                "Do not enter Paper until mechanism-relevant wins clearly exceed losses, "
                "the headline and primary comparisons win, and the strongest "
                "same-information baseline is beaten. Until then, improve the method in "
                "Experiment. Paper work is thesis-driven and contribution-led, not a "
                "negative-result report, experiment chronology, or compliance exercise."
            ),
        )
        if block
    )


def _engineer_fragment(stage: str, project_root: Path | None) -> str:
    context = _active_context_block(stage, project_root)
    stage_policy = (
        "## Research execution policy\n"
        "Use code, explicit run configuration, raw outputs, figures, bibliography, "
        "manuscript source, and rendered output directly as work products. Do not create "
        "extra handoff substitutes. Experiments remain adaptive: preserve each run's "
        "reproducibility while changing methods, baselines, benchmark design, controls, "
        "or next experiments when evidence justifies it."
    )
    if stage in _HANDOFF_STAGES:
        stage_policy += (
            "\nBefore completing this stage, replace `HANDOFF.md` completely and start "
            f"it with `# HANDOFF — {stage.upper()}`. Do not append history or impose "
            "another handoff schema."
        )
    if stage == "review":
        stage_policy = (
            "## Final Review execution\n"
            "Read the scientific, visual, and language findings in `paper/REVIEW.md`. "
            "Apply them to the real method, evidence, manuscript, figures, and tables as "
            "needed, then recompile the complete paper. Hand the repaired paper to the "
            "normal independent Reviewer for one integrated final decision."
        )
    elif stage == "paper":
        stage_policy += (
            "\nWrite confidently around the central thesis and strongest supported win. "
            "Remove defensive qualifier boilerplate and integrity self-praise; discuss "
            "only limitations that materially change interpretation."
        )
    return "\n\n".join(block for block in (context, stage_policy) if block)


def _reviewer_fragment(
    stage: str,
    scope: str,
    project_root: Path | None,
) -> str:
    context = _active_context_block(stage, project_root)
    if stage == "review" or scope == "final_submission":
        policy = academic_paper_review_block()
    else:
        policy = (
            "## Research-stage adjudication\n"
            "Judge whether the current-stage work advances the selected contribution. "
            "Separate implementation, evaluator, control, and scale defects from a "
            "scientific failure. Keep the current stage and name the repair; never "
            "request rollback or reopen the completed idea selection."
        )
        if stage == "experiment":
            policy += (
                " Recommend Paper only when wins clearly exceed losses on "
                "mechanism-relevant evaluations, headline comparisons win, and the "
                "strongest same-information baseline is beaten."
            )
    return "\n\n".join(block for block in (context, policy) if block)


def render_role_prompt_fragment(
    *,
    role: str,
    operation: str,
    stage: str,
    scope: str,
    project_root: Path | None,
) -> str:
    """Render only policy owned by the Research vertical."""
    _ = operation
    normalized_role = str(role or "").strip().lower()
    normalized_stage = str(stage or "").strip().lower()
    normalized_scope = str(scope or "").strip().lower().replace("-", "_")
    if normalized_role == "planner":
        return _planner_fragment(normalized_stage, project_root)
    if normalized_role == "engineer":
        return _engineer_fragment(normalized_stage, project_root)
    if normalized_role == "reviewer":
        return _reviewer_fragment(
            normalized_stage,
            normalized_scope,
            project_root,
        )
    if normalized_role == "manager":
        return (
            _active_context_block(normalized_stage, project_root)
            + "\n\n## Forward-only stage authority\n"
            "Research stages never roll back. Hold the current stage and schedule "
            "repairs there, or advance when its checklist is satisfied."
        ).strip()
    return ""


__all__ = [
    "academic_paper_review_block",
    "active_context_paths",
    "render_role_prompt_fragment",
]
