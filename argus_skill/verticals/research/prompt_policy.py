"""Research-owned role prompts and explicit stage context loading."""

from __future__ import annotations

from pathlib import Path

from .library_preparation import STAGE_PLAYBOOK_PATHS

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
        "selection or move backward."
    )


def _planner_fragment(stage: str, project_root: Path | None) -> str:
    return "\n\n".join(
        block
        for block in (
            _stage_playbook_block(stage),
            _active_context_block(stage, project_root),
            (
                "## Planner responsibility\n"
                f"Plan only the highest-value unresolved work in `{stage or '(unknown)'}` "
                "under the stage playbook. Keep repairs in the current stage, avoid "
                "ceremonial tasks, and leave stage transitions to Manager."
            ),
        )
        if block
    )


def _engineer_fragment(stage: str, project_root: Path | None) -> str:
    context = _active_context_block(stage, project_root)
    stage_policy = (
        "## Engineer responsibility\n"
        "Execute the current playbook directly. Use code, explicit configuration, raw "
        "outputs, figures, bibliography, manuscript source, and rendered output as work "
        "products. Do not create substitute handoffs or process reports, and do not "
        "change stage state."
    )
    return "\n\n".join(
        block
        for block in (_stage_playbook_block(stage), context, stage_policy)
        if block
    )


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
    "render_role_prompt_fragment",
    "STAGE_PLAYBOOK_PATHS",
]
