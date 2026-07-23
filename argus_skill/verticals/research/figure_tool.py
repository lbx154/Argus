"""Research-paper figure workflow: canonical image-2 prompt, paper-oriented
vision review, candidate cache, context freeze, and paper metadata/provenance
syncing.

This module owns the paper-specific figure workflow built on top of the
domain-neutral image capability in ``argus_skill.tools.image_api``. Image-2 is
one *optional* route among several the Research Visualization Router can
select (``verticals/research/skills/engineer/research-visualization-router.md``);
this module never forces its use. When image-2 is selected, the anti-fraud
essentials this module preserves are: real raster/prompt/review SHA-256
consistency and provenance registration. It intentionally does **not** enforce
a minimum candidate count or require that a prompt literally contain the
built-in template markers — the canonical prompt produced here is the
*recommended* prompt, not a mandatory gate. ``review_image`` here builds the
paper/venue-aware review instruction and calls the generic
``tools.image_api.review_image`` to talk to the model; use this module's
``review`` CLI/function for paper figures, not the domain-neutral one in
``tools.image_api``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus_skill.skills.venue_profiles import VenueProfile, get_venue_profile
from argus_skill.tools.image_api import (
    _DEFAULT_MAX_RETRIES,
    _DEFAULT_TIMEOUT_SECONDS,
    ImageToolError,
    _atomic_write_json,
    _load_sidecar_prompt,
    _project_path,
    _project_relative,
    _read_json_object,
    _read_prompt,
    _redact,
    _restore_optional_file,
    _sha256_file,
    _sha256_prompt_file,
    _sha256_text,
    _sidecar_path,
    inspect_image,
)
from argus_skill.tools.image_api import review_image as _generic_review_image

PAPER_FIGURE_PROMPT_TEMPLATE_ID = "argus-image2-paper-prompt-v1"
PAPER_FIGURE_STUDIO_SOURCE_ID = "paper-framework-figure-studio-pro-v3.1.4a"
PAPER_FIGURE_STUDIO_DEFAULT_STAGE = "S5-CANDIDATE-IMAGE"
PAPER_FIGURE_CONTEXT_FREEZE_PATH = Path(
    "paper/figures/IMAGE2_CONTEXT_FREEZE.json"
)
PAPER_FIGURE_CANDIDATE_CACHE_PATH = Path(
    "paper/figures/IMAGE2_CANDIDATE_CACHE.json"
)
# No hard minimum: a single reviewed, passing candidate is enough to be
# reusable. Image-2 is optional per the Research Visualization Router, and
# nothing in this module should force an agent to grind out a fixed batch of
# candidates before it is allowed to stop. Cache reuse exists to avoid wasted
# image calls on an unchanged context, not to gate progress.
PAPER_FIGURE_MIN_REVIEWED_CANDIDATES = 1
PAPER_FIGURE_MAX_REVIEWED_CANDIDATES = 20
_PAPER_FIGURE_REQUIRED_CONTEXT = (
    Path("research/RESEARCH_BRIEF.md"),
    Path("paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md"),
)
_PAPER_FIGURE_EVIDENCE_OPTIONS = (
    Path("paper/CLAIM_GRAPH.json"),
    Path("paper/artifacts/claims_evidence.tsv"),
    Path("paper/RESULTS_REPORT.md"),
)
_PAPER_FIGURE_OPTIONAL_CONTEXT = (
    Path("research/VENUE_PROFILE.json"),
    Path("research/EXPERIMENT_PLAN.md"),
    Path("paper/artifacts/results_table.tsv"),
)


PAPER_FIGURE_PROMPT_TEMPLATE = """Create one polished EMNLP method figure variant.
Prompt template: {template_id}
Prompt source: {figure_studio_source}
{framing}

General style:
- EMNLP/ACL/NeurIPS/CS paper method figure, full-width two-column landscape.
- Clean block-based Figma style with rounded cards (10-16px radius), neat alignment, soft pastel fills, dark-gray 2px borders, and compact information density.
- Compact, information-rich, suitable for a PDF page-width figure; little wasted space but not crowded.
- Tidy rounded or friendly sans-serif feel; must remain crisp and readable.
- Moderate badge/icon use only when semantically useful; a few simple recognizable icons are fine, not a logo wall.
- No heavy shadows, no gradients, no photorealism, no glassmorphism, no messy Excalidraw look.
- Large readable labels, short phrases, balanced hierarchy, flat vector-like raster rendering on warm white #fbfaf7.
- 干净、密实、模块化、Figma 风，圆角卡片为主，低饱和浅色块，少量 badge/logo，少留白但不拥挤。整体适合 EMNLP/ACL/NeurIPS 论文主图，不要像随手白板，也不要像艺术插画。

Style intent:
- Clean, dense, modular, Figma-like, mostly rounded cards, low-saturation pastel blocks.
- Use small badges/icons sparingly; avoid empty space while preserving alignment.
- It should look like a main figure in an EMNLP/ACL/NeurIPS paper, not a marketing graphic, stock illustration, dashboard screenshot, or casual whiteboard.

Pinned content that must appear exactly:
{content}
- SPELL EXACTLY every quoted label above. Do not invent alternate terminology, code identifiers, raw artifact paths, or extra labels.

Layout variant:
- {layout_variant}
- Keep the visible labels faithful to the pinned content, but use the layout variant to create a polished, dense, paper-native composition with visual hierarchy.
- Prefer grouped modules, phase containers, compact chips, and clear arrows over a sparse chain of identical boxes.

Negative prompt / Avoid:
- no concrete code snippets, raw paths, tiny unreadable text, character-level vertical text, or dense paragraphs
- no excessive logos or brand marks, no watermark
- no photorealistic scenes, stock photos, glassmorphism, heavy gradients, heavy shadows, texture, or arbitrary decorative blobs
- no messy whiteboard / Excalidraw-heavy sketch style
- no large empty areas, overlapping cards, squashed labels, inconsistent terminology, or extra captions that make it look like a dashboard
- no inconsistent terminology between figure and text

Aspect ratio:
- {aspect_ratio}

Figma tokens for camera-ready cleanup:
- Background #fbfaf7; stroke #1f2933 at 2px.
- Corner radius 10-16px; card padding 12-20px; card gap 12-24px.
- Pastels: acquisition #ffe2d1, parsing #fff2bd, memory/wiki #dcecff, agent #e2f7df, domains #eadfff, benchmark #fff1c9.
- Text sizes: title 38-52px, section headers 22-30px, card labels 16-22px, chips 12-16px.
"""


def render_paper_figure_prompt(
    *,
    figure_title: str = "Method Overview",
    content: str = "",
    layout_variant: str = (
        "20 polished Figma wireframe: component frames, auto-layout-like spacing, "
        "section tabs, chips, and carefully staggered components."
    ),
    framing: str = "",
    aspect_ratio: str = "1536x1024 landscape",
    venue_profile: VenueProfile | None = None,
    # Legacy parameters — composed into content block if content is empty
    studio_stage: str = PAPER_FIGURE_STUDIO_DEFAULT_STAGE,  # noqa: ARG001 — kept for write_paper_figure_prompt's keyword pass-through
    input_label: str = "",
    mechanism_label: str = "",
    verification_label: str = "",
    state_label: str = "",
    execution_label: str = "",
    output_label: str = "",
    evidence_label: str = "",
    benefit_label: str = "",
    failure_label: str = "",
    caption_plan: str = "",
    legend_plan: str = "",
    body_reference_plan: str = "",
    core_step_visibility_plan: str = "",
    claimed_improvement_anchor: str = "",
    symbol_formula_necessity: str = "",
    semantic_contract: str = "",
) -> str:
    """Render a paper-figure prompt using the 6-section structure.

    Preferred usage: provide ``figure_title``, a free-form ``content`` block
    listing every label that must appear verbatim, a ``layout_variant``,
    and an ``aspect_ratio``.

    If ``content`` is empty, legacy stage-label parameters are composed into
    a default content block for backward compatibility.
    """
    if not content.strip():
        stages = [s for s in [
            input_label, mechanism_label, verification_label,
            state_label, execution_label, output_label, evidence_label,
        ] if s]
        chips = [c for c in [benefit_label, failure_label] if c]
        lines = [f'- Title: "{figure_title}"']
        if stages:
            lines.append('- Show: "' + '" -> "'.join(stages) + '".')
        if chips:
            lines.append('- Components/chips: "' + '", "'.join(chips) + '".')
        content = "\n".join(lines)

    if not framing.strip():
        persona = venue_profile.figure_style_persona if venue_profile is not None else "EMNLP/ACL"
        framing = (
            f"Figma-style technical diagram for an {persona} paper. "
            f"Subject: {figure_title}."
        )

    prompt = PAPER_FIGURE_PROMPT_TEMPLATE.format(
        template_id=PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        figure_studio_source=PAPER_FIGURE_STUDIO_SOURCE_ID,
        framing=framing,
        content=content,
        layout_variant=layout_variant,
        aspect_ratio=aspect_ratio,
    ).strip() + _plan_section(
        caption_plan=caption_plan,
        legend_plan=legend_plan,
        body_reference_plan=body_reference_plan,
        core_step_visibility_plan=core_step_visibility_plan,
        claimed_improvement_anchor=claimed_improvement_anchor,
        symbol_formula_necessity=symbol_formula_necessity,
        semantic_contract=semantic_contract,
    ) + "\n"
    if venue_profile is not None:
        prompt = _apply_venue_persona(prompt, venue_profile)
    return prompt


def _apply_venue_persona(prompt: str, venue_profile: VenueProfile) -> str:
    """Rewrite the venue persona baked into the static prompt template.

    ``PAPER_FIGURE_PROMPT_TEMPLATE`` hardcodes the EMNLP/ACL/NeurIPS family in
    a few places (``EMNLP method figure``, the ``EMNLP/ACL/NeurIPS ... paper``
    style clauses, the Chinese "适合 EMNLP/ACL/NeurIPS 论文主图" note). When a
    ``venue_profile`` is supplied, swap those literals for the profile's
    ``figure_style_persona`` (family clauses) / ``reviewer_persona`` (the short
    "<venue> method figure" label) so an AAAI figure reads as an AAAI figure.
    This is a true no-op for the EMNLP profile (figure_style_persona ==
    "EMNLP/ACL/NeurIPS", reviewer_persona == "EMNLP") and is never called when
    ``venue_profile`` is None, so legacy prompts are byte-identical.
    """
    persona = venue_profile.figure_style_persona
    replaced = prompt.replace("EMNLP/ACL/NeurIPS", persona)
    replaced = replaced.replace(
        "EMNLP method figure", f"{venue_profile.reviewer_persona} method figure"
    )
    return replaced


def _plan_section(
    *,
    caption_plan: str = "",
    legend_plan: str = "",
    body_reference_plan: str = "",
    core_step_visibility_plan: str = "",
    claimed_improvement_anchor: str = "",
    symbol_formula_necessity: str = "",
    semantic_contract: str = "",
) -> str:
    """Render the optional figure-plan directives as a labelled block.

    These were previously accepted by ``render_paper_figure_prompt`` /
    ``write_paper_figure_prompt`` and wired from real CLI flags + the
    paper-illustration skill, but silently DROPPED — so an agent following the
    documented workflow passed ``--caption-plan`` / ``--semantic-contract`` and
    they never reached the prompt. This wires them through. Additive: when every
    plan is empty (all legacy usage) it returns ``""`` so the base prompt is
    byte-for-byte unchanged; a non-empty plan is appended as an explicit
    "must honor" constraint the image model can act on.
    """
    directives = [
        ("Caption plan", caption_plan),
        ("Legend plan", legend_plan),
        ("Body reference", body_reference_plan),
        ("Core steps that must stay visible", core_step_visibility_plan),
        ("Claimed-improvement anchor", claimed_improvement_anchor),
        ("Symbol/formula necessity", symbol_formula_necessity),
        ("Semantic contract", semantic_contract),
    ]
    lines = [f"- {label}: {val.strip()}" for label, val in directives if val and val.strip()]
    if not lines:
        return ""
    return "\n\nFigure plan (must honor):\n" + "\n".join(lines)


def write_paper_figure_prompt(
    prompt_file: Path,
    *,
    figure_title: str = "Method Overview",
    content: str = "",
    layout_variant: str = (
        "20 polished Figma wireframe: component frames, auto-layout-like spacing, "
        "section tabs, chips, and carefully staggered components."
    ),
    framing: str = "",
    aspect_ratio: str = "1536x1024 landscape",
    venue_profile: VenueProfile | None = None,
    force: bool = False,
    # Legacy parameters — passed through for backward compat
    studio_stage: str = PAPER_FIGURE_STUDIO_DEFAULT_STAGE,
    input_label: str = "",
    mechanism_label: str = "",
    verification_label: str = "",
    state_label: str = "",
    execution_label: str = "",
    output_label: str = "",
    evidence_label: str = "",
    benefit_label: str = "",
    failure_label: str = "",
    caption_plan: str = "",
    legend_plan: str = "",
    body_reference_plan: str = "",
    core_step_visibility_plan: str = "",
    claimed_improvement_anchor: str = "",
    symbol_formula_necessity: str = "",
    semantic_contract: str = "",
) -> dict[str, Any]:
    if prompt_file.exists() and not force:
        raise ImageToolError(f"{prompt_file} already exists; pass --force to overwrite")
    prompt = render_paper_figure_prompt(
        figure_title=figure_title,
        content=content,
        layout_variant=layout_variant,
        framing=framing,
        aspect_ratio=aspect_ratio,
        venue_profile=venue_profile,
        studio_stage=studio_stage,
        input_label=input_label,
        mechanism_label=mechanism_label,
        verification_label=verification_label,
        state_label=state_label,
        execution_label=execution_label,
        output_label=output_label,
        evidence_label=evidence_label,
        benefit_label=benefit_label,
        failure_label=failure_label,
        caption_plan=caption_plan,
        legend_plan=legend_plan,
        body_reference_plan=body_reference_plan,
        core_step_visibility_plan=core_step_visibility_plan,
        claimed_improvement_anchor=claimed_improvement_anchor,
        symbol_formula_necessity=symbol_formula_necessity,
        semantic_contract=semantic_contract,
    )
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")
    return {
        "prompt_path": str(prompt_file),
        "prompt_sha256": _sha256_prompt_file(prompt_file),
        "template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "bytes": len(prompt.encode("utf-8")),
    }


def _review_prompt(
    *,
    original_prompt: str,
    rubric: str,
    venue_profile: VenueProfile | None = None,
) -> str:
    figure_persona = (
        venue_profile.figure_style_persona if venue_profile is not None else "EMNLP/ACL"
    )
    reviewer_persona = (
        venue_profile.reviewer_persona if venue_profile is not None else "EMNLP"
    )
    # When the caller supplies a real rubric (as the AI-figure validator does),
    # that rubric is authoritative: it defines the reviewer's task, the exact
    # pass/fail criteria, AND the exact JSON fields to emit (e.g.
    # ``confirmed_labels``, ``findings``, ``extra_tokens_present``). The generic
    # "communicate the method" schema below must never override those fields,
    # otherwise a caller asking for structured verdicts (label confirmation,
    # exact-content checks) silently gets only ``score_1_to_5`` back. When no
    # rubric is supplied, the historical generic schema is used verbatim so
    # existing paper-figure callers keep byte-identical behavior.
    if rubric and rubric.strip():
        return (
            f"You are reviewing an academic paper figure for an {figure_persona} "
            "submission. You are a VISION reviewer: judge the rendered raster you "
            "are shown, and read every label directly off the image rather than "
            "trusting the prompt text.\n\n"
            "The Rubric below is AUTHORITATIVE. It defines your task, your exact "
            "acceptance criteria, and the exact JSON fields you must return. Emit "
            "a single JSON object that includes EVERY field the Rubric requests, "
            "each populated strictly from what you can actually see in the raster. "
            "Always include \"keep_or_regenerate\" (\"keep\" or \"regenerate\"). "
            "Apply the Rubric's pass/fail rules exactly — including exact label "
            "spelling, missing/invented/duplicated labels, wrong or reversed "
            "relationships and arrows, off-palette colour, and prohibited content. "
            "Where the Rubric and any generic guidance disagree, the Rubric wins. "
            "Return only the JSON object, optionally fenced as ```json ... ```.\n\n"
            f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
            f"Rubric:\n{rubric}"
        )
    return (
        f"You are reviewing an academic paper figure for an {figure_persona} submission. "
        "Your ONLY job is to judge whether the figure effectively communicates "
        "the paper's method to a reader. Do NOT nitpick pixel-level prompt "
        "compliance, chip placement, badge count, or exact visual hierarchy — "
        "those are style preferences, not quality issues.\n\n"
        "Focus on these questions:\n"
        "1. Does the figure faithfully represent the paper's method/architecture?\n"
        "2. Is the core contribution module visible (not an empty box)?\n"
        "3. Are labels readable and correctly spelled?\n"
        "4. Is the data flow / reader path clear?\n"
        f"5. Would an {reviewer_persona} reviewer understand the method from this figure + its caption?\n\n"
        "Return JSON with:\n"
        "- score_1_to_5: 4+ means acceptable for submission, 3 means needs one more pass, "
        "1-2 means fundamentally wrong (wrong modules, misleading flow, unreadable)\n"
        "- major_issues: ONLY issues that would mislead a reader or misrepresent the method. "
        "Do NOT list cosmetic preferences as major issues.\n"
        "- concrete_revision_prompt: if score < 4, provide a SPECIFIC revision to the prompt "
        "that fixes the actual problem. The prompt must still use the standard template "
        "(General style, Pinned content, Layout variant, Negative prompt, Aspect ratio, "
        "Figma tokens sections).\n"
        "- keep_or_regenerate: 'keep' if score >= 4, 'regenerate' only if the figure "
        "would actively mislead readers about the method.\n\n"
        f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
        f"Rubric:\n{rubric or f'Does this figure effectively communicate the paper method to an {reviewer_persona} reviewer?'}"
    )


def review_image(
    *,
    image: Path,
    out: Path | None = None,
    prompt: str = "",
    rubric: str = "",
    venue_profile: VenueProfile | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Paper-figure vision review: build the venue-aware paper prompt, then
    call the generic ``tools.image_api`` reviewer to actually talk to the
    model. Same caller-facing signature/behavior as the pre-split
    ``tools.image_api.review_image``.
    """
    original_prompt = prompt.strip() or _load_sidecar_prompt(image)
    review_instruction = _review_prompt(
        original_prompt=original_prompt,
        rubric=rubric,
        venue_profile=venue_profile,
    )
    target = out or image.with_suffix(image.suffix + ".review.json")
    result = _generic_review_image(
        image=image,
        review_instruction=review_instruction,
        out=target,
        prompt=original_prompt,
        env=env,
        timeout=timeout,
        max_retries=max_retries,
    )
    result["rubric"] = rubric
    _atomic_write_json(target, result)
    return result


def _context_records(
    project_root: Path,
    paths: Sequence[Path],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = _project_path(project_root, raw_path)
        if not path.is_file():
            raise ImageToolError(f"paper figure freeze input does not exist: {path}")
        rel = _project_relative(project_root, path)
        if rel in seen:
            continue
        seen.add(rel)
        records.append(
            {
                "path": rel,
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return sorted(records, key=lambda row: str(row["path"]))


def _context_sha256(records: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        [
            {
                "path": str(row.get("path") or ""),
                "sha256": str(row.get("sha256") or ""),
            }
            for row in records
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(canonical)


def freeze_paper_figure_context(
    *,
    project_root: Path,
    inputs: Sequence[Path] | None = None,
    out: Path = PAPER_FIGURE_CONTEXT_FREEZE_PATH,
) -> dict[str, Any]:
    """Freeze stable evidence + structure inputs for image-2 candidate reuse.

    ``paper/main.tex`` is intentionally excluded: prose, citation placement, and
    minor layout edits must not invalidate reviewed figure candidates.
    """
    project_root = project_root.resolve()
    if inputs:
        selected = list(inputs)
    else:
        selected = list(_PAPER_FIGURE_REQUIRED_CONTEXT)
        missing_required = [
            path
            for path in _PAPER_FIGURE_REQUIRED_CONTEXT
            if not _project_path(project_root, path).is_file()
        ]
        if missing_required:
            raise ImageToolError(
                "freeze paper evidence/structure before image generation; missing "
                + ", ".join(str(path) for path in missing_required)
            )
        evidence = [
            path
            for path in _PAPER_FIGURE_EVIDENCE_OPTIONS
            if _project_path(project_root, path).is_file()
        ]
        if not evidence:
            raise ImageToolError(
                "freeze requires at least one claim/evidence artifact: "
                + ", ".join(str(path) for path in _PAPER_FIGURE_EVIDENCE_OPTIONS)
            )
        selected.extend(evidence)
        selected.extend(
            path
            for path in _PAPER_FIGURE_OPTIONAL_CONTEXT
            if _project_path(project_root, path).is_file()
        )
    records = _context_records(project_root, selected)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "context_sha256": _context_sha256(records),
        "inputs": records,
        "excludes": [
            "paper/main.tex",
            "paper/main.pdf",
            "paper prose/layout-only edits",
        ],
    }
    _atomic_write_json(_project_path(project_root, out), payload)
    return payload


def _paper_context_status(
    *,
    project_root: Path,
    freeze_path: Path = PAPER_FIGURE_CONTEXT_FREEZE_PATH,
) -> dict[str, Any]:
    path = _project_path(project_root, freeze_path)
    if not path.is_file():
        return {
            "frozen": False,
            "current": False,
            "reason": "missing_context_freeze",
            "freeze_path": _project_relative(project_root, path),
        }
    try:
        payload = _read_json_object(path)
        raw_inputs = payload.get("inputs")
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise ImageToolError("context freeze has no inputs")
        paths = [
            Path(str(row.get("path") or ""))
            for row in raw_inputs
            if isinstance(row, dict) and str(row.get("path") or "").strip()
        ]
        records = _context_records(project_root, paths)
        current_sha = _context_sha256(records)
        frozen_sha = str(payload.get("context_sha256") or "")
        return {
            "frozen": True,
            "current": current_sha == frozen_sha,
            "reason": (
                "current"
                if current_sha == frozen_sha
                else "evidence_or_structure_changed"
            ),
            "context_sha256": frozen_sha,
            "current_context_sha256": current_sha,
            "freeze_path": _project_relative(project_root, path),
            "inputs": records,
        }
    except (OSError, ImageToolError, ValueError) as exc:
        return {
            "frozen": True,
            "current": False,
            "reason": f"invalid_context_freeze:{type(exc).__name__}",
            "freeze_path": _project_relative(project_root, path),
        }


def _review_passed(review: Mapping[str, Any]) -> bool:
    for field in ("passed", "pass", "accepted"):
        if review.get(field) is True:
            return True
    verdict = str(review.get("verdict") or "").strip().lower()
    if verdict in {"pass", "accepted", "keep"}:
        return True
    keep = str(review.get("keep_or_regenerate") or "").strip().lower()
    if keep == "keep":
        return True
    text = str(review.get("review") or "")
    lowered = text.lower()
    if re.search(r"keep_or_regenerate\s*[:=]\s*keep\b", lowered):
        return True
    score_match = re.search(
        r"score(?:_1_to_5)?\s*[:=]\s*([0-5](?:\.\d+)?)",
        lowered,
    )
    return bool(
        score_match
        and float(score_match.group(1)) >= 4.0
        and "regenerate" not in lowered
        and "revise" not in lowered
    )


def paper_figure_cache_status(
    *,
    project_root: Path,
    figure_type: str = "method",
    min_candidates: int = PAPER_FIGURE_MIN_REVIEWED_CANDIDATES,
    max_candidates: int = PAPER_FIGURE_MAX_REVIEWED_CANDIDATES,
    freeze_path: Path = PAPER_FIGURE_CONTEXT_FREEZE_PATH,
    cache_path: Path = PAPER_FIGURE_CANDIDATE_CACHE_PATH,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    context = _paper_context_status(
        project_root=project_root,
        freeze_path=freeze_path,
    )
    result: dict[str, Any] = {
        **context,
        "figure_type": figure_type,
        "min_candidates": max(1, int(min_candidates)),
        "max_candidates": max(1, int(max_candidates)),
        "passed_candidates": 0,
        "reusable": False,
        "candidates": [],
        "cache_path": _project_relative(
            project_root,
            _project_path(project_root, cache_path),
        ),
    }
    if not context.get("current"):
        return result
    path = _project_path(project_root, cache_path)
    if not path.is_file():
        result["reason"] = "missing_candidate_cache"
        return result
    try:
        payload = _read_json_object(path)
    except ImageToolError:
        result["reason"] = "invalid_candidate_cache"
        return result
    if str(payload.get("context_sha256") or "") != str(
        context.get("context_sha256") or ""
    ):
        result["reason"] = "candidate_cache_context_mismatch"
        return result
    valid: list[dict[str, Any]] = []
    for row in payload.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("figure_type") or "") != figure_type:
            continue
        if row.get("passed_review") is not True:
            continue
        output = _project_path(project_root, str(row.get("output_path") or ""))
        review = _project_path(project_root, str(row.get("review_path") or ""))
        if not output.is_file() or not review.is_file():
            continue
        if str(row.get("output_sha256") or "") != _sha256_file(output):
            continue
        if str(row.get("review_sha256") or "") != _sha256_file(review):
            continue
        valid.append(dict(row))
    limit = max(1, int(max_candidates))
    valid = valid[-limit:]
    result["candidates"] = valid
    result["passed_candidates"] = len(valid)
    result["reusable"] = len(valid) >= max(1, int(min_candidates))
    result["reason"] = "reviewed_candidate_cache_ready" if result["reusable"] else (
        "need_more_reviewed_candidates"
    )
    return result


def _register_paper_figure_candidate(
    *,
    project_root: Path,
    entry: Mapping[str, Any],
    review_payload: Mapping[str, Any],
    cache_path: Path = PAPER_FIGURE_CANDIDATE_CACHE_PATH,
) -> dict[str, Any]:
    context = _paper_context_status(project_root=project_root)
    if not context.get("current"):
        return {
            "registered": False,
            "reason": str(context.get("reason") or "context_not_frozen"),
        }
    path = _project_path(project_root, cache_path)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "context_sha256": context["context_sha256"],
        "freeze_path": context["freeze_path"],
        "candidates": [],
    }
    if path.is_file():
        try:
            existing = _read_json_object(path)
            if str(existing.get("context_sha256") or "") == str(
                context["context_sha256"]
            ):
                payload = existing
        except ImageToolError:
            pass
    candidates = payload.setdefault("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
        payload["candidates"] = candidates
    passed_review = _review_passed(review_payload)
    if not passed_review:
        return {
            "registered": False,
            "reason": "review_not_passed",
            "context_sha256": context["context_sha256"],
        }
    candidate = {
        "figure_id": str(entry.get("figure_id") or ""),
        "figure_type": str(entry.get("figure_type") or "method"),
        "prompt_path": str(entry.get("prompt_path") or ""),
        "prompt_sha256": str(entry.get("prompt_sha256") or ""),
        "output_path": str(entry.get("output_path") or ""),
        "output_sha256": str(entry.get("output_sha256") or ""),
        "review_path": str(entry.get("review_path") or ""),
        "review_sha256": str(entry.get("review_sha256") or ""),
        "passed_review": True,
        "registered_at": datetime.now(UTC).isoformat(),
    }
    output_sha = candidate["output_sha256"]
    candidates = [
        row
        for row in candidates
        if not (
            isinstance(row, dict)
            and str(row.get("output_sha256") or "") == output_sha
        )
    ]
    candidates.append(candidate)
    figure_type = candidate["figure_type"]
    same_type = [
        row
        for row in candidates
        if isinstance(row, dict)
        and str(row.get("figure_type") or "") == figure_type
    ][-PAPER_FIGURE_MAX_REVIEWED_CANDIDATES:]
    other_types = [
        row
        for row in candidates
        if not (
            isinstance(row, dict)
            and str(row.get("figure_type") or "") == figure_type
        )
    ]
    payload["candidates"] = other_types + same_type
    _atomic_write_json(path, payload)
    status = paper_figure_cache_status(
        project_root=project_root,
        figure_type=figure_type,
    )
    return {
        "registered": True,
        "passed_review": candidate["passed_review"],
        "context_sha256": context["context_sha256"],
        "cache_path": _project_relative(project_root, path),
        "passed_candidates": status["passed_candidates"],
        "reusable": status["reusable"],
    }


def _load_image2_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"figures": []}
    payload = _read_json_object(path)
    figures = payload.get("figures")
    if not isinstance(figures, list):
        raise ImageToolError(f"{path} `figures` must be a JSON list")
    return payload


def _upsert_image2_manifest_entry(manifest_path: Path, entry: dict[str, Any]) -> None:
    payload = _load_image2_manifest(manifest_path)
    figures = payload.setdefault("figures", [])
    figure_id = str(entry.get("figure_id") or "")
    replaced = False
    for index, existing in enumerate(figures):
        if isinstance(existing, dict) and str(existing.get("figure_id") or "") == figure_id:
            figures[index] = entry
            replaced = True
            break
    if not replaced:
        figures.append(entry)
    _atomic_write_json(manifest_path, payload)


def _prompt_hash_variants(prompt_file: Path) -> set[str]:
    """Return all plausible SHA-256 hashes for a prompt file.

    Accepts raw-file hash (canonical), stripped-text hash, and
    as-is-text hash so that sidecars written by older versions
    still pass validation.
    """
    raw_file_hash = _sha256_file(prompt_file)
    text = prompt_file.read_text(encoding="utf-8", errors="replace")
    return {raw_file_hash, _sha256_text(text), _sha256_text(text.strip())}


def _require_matching_prompt(
    *,
    prompt_file: Path,
    sidecar: dict[str, Any],
) -> tuple[str, str]:
    """Verify the prompt/sidecar hash consistency an accepted candidate needs.

    This intentionally does NOT require the prompt to contain the built-in
    ``PAPER_FIGURE_PROMPT_TEMPLATE_ID`` / ``PAPER_FIGURE_STUDIO_SOURCE_ID``
    markers: the canonical prompt from ``render_paper_figure_prompt`` is the
    *recommended* prompt, not a mandatory marker gate. Any prompt text is
    accepted as long as the real raster/prompt/review hash chain is
    consistent — that consistency, not prompt provenance, is the anti-fraud
    essential.
    """
    prompt_text = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise ImageToolError(f"prompt file is empty: {prompt_file}")

    prompt_sha = str(sidecar.get("prompt_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", prompt_sha):
        raise ImageToolError("generation sidecar must contain a lowercase prompt_sha256")
    if prompt_sha not in _prompt_hash_variants(prompt_file):
        raise ImageToolError(
            "generation sidecar prompt_sha256 does not match prompt_path; "
            "regenerate through image-2 or restore the matching prompt file"
        )
    raw_prompt = sidecar.get("prompt")
    if not isinstance(raw_prompt, str) or not raw_prompt.strip():
        raise ImageToolError("generation sidecar must preserve the exact prompt text")
    raw_prompt_hashes = {
        _sha256_text(raw_prompt),
        _sha256_text(raw_prompt.strip()),
        _sha256_text(raw_prompt.rstrip("\n")),
    }
    if prompt_sha not in raw_prompt_hashes and not (
        raw_prompt_hashes & _prompt_hash_variants(prompt_file)
    ):
        raise ImageToolError("generation sidecar prompt text hash does not match prompt_sha256")
    return prompt_text, prompt_sha


def _sidecar_output_sha(sidecar: dict[str, Any]) -> str:
    for field in ("output_sha256", "sha256"):
        value = str(sidecar.get(field) or "").strip().lower()
        if value:
            return value
    image = sidecar.get("image")
    if isinstance(image, dict):
        for field in ("output_sha256", "sha256"):
            value = str(image.get(field) or "").strip().lower()
            if value:
                return value
    return ""


def _review_image_sha(review: dict[str, Any]) -> str:
    image = review.get("image")
    if isinstance(image, dict):
        for field in ("output_sha256", "sha256"):
            value = str(image.get(field) or "").strip().lower()
            if value:
                return value
    return ""


def _recorded_prompt_path(project_root: Path, prompt_file: Path, sidecar: dict[str, Any]) -> str:
    raw_prompt_path = sidecar.get("prompt_path")
    if isinstance(raw_prompt_path, str) and raw_prompt_path.strip():
        try:
            candidate = _project_path(project_root, raw_prompt_path)
        except ImageToolError:
            candidate = None
        if candidate is not None and candidate.resolve() == prompt_file.resolve():
            return _project_relative(project_root, candidate)
    return _project_relative(project_root, prompt_file)


def sync_paper_metadata(
    *,
    project_root: Path,
    image: Path,
    figure_id: str,
    figure_type: str = "method",
    manifest: Path = Path("paper/figures/IMAGE2_FIGURES.json"),
    prompt_file: Path | None = None,
    sidecar: Path | None = None,
    inspect_path: Path | None = None,
    review_path: Path | None = None,
    provenance_path: Path | None = None,
    figure_studio_stage: str = PAPER_FIGURE_STUDIO_DEFAULT_STAGE,
) -> dict[str, Any]:
    """Synchronize image-2 manifest/provenance from the real raster and sidecars."""

    if not figure_id.strip():
        raise ImageToolError("missing --figure-id")
    project_root = project_root.resolve()
    manifest_path = _project_path(project_root, manifest)
    _load_image2_manifest(manifest_path)
    from .figure_provenance import (
        FIGURE_PROVENANCE_PATH,
        figure_manifest_transaction,
        preflight_figure_provenance,
        register_figure,
    )

    try:
        canonical_manifest_path = preflight_figure_provenance(project_root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ImageToolError(
            f"canonical figure provenance is not writable/valid: {exc}"
        ) from exc
    image_path = _project_path(project_root, image)
    if not image_path.is_file():
        raise ImageToolError(f"generated image does not exist: {image_path}")

    sidecar_path = _project_path(project_root, sidecar) if sidecar is not None else _sidecar_path(image_path)
    if not sidecar_path.is_file():
        raise ImageToolError(f"generation sidecar does not exist: {sidecar_path}")
    sidecar_payload = _read_json_object(sidecar_path)

    if prompt_file is None:
        raw_prompt_path = sidecar_payload.get("prompt_path")
        prompt_path = None
        if isinstance(raw_prompt_path, str) and raw_prompt_path.strip():
            try:
                recorded_prompt_path = _project_path(project_root, raw_prompt_path)
            except ImageToolError:
                recorded_prompt_path = None
            if recorded_prompt_path is not None and recorded_prompt_path.is_file():
                prompt_path = recorded_prompt_path
        inferred_prompt_path = image_path.with_suffix(".prompt.txt")
        if prompt_path is None and inferred_prompt_path.is_file():
            prompt_path = inferred_prompt_path
        if prompt_path is None:
            raise ImageToolError(
                "pass --prompt-file; generation sidecar has no usable in-project "
                "prompt_path and no sibling prompt file exists"
            )
    else:
        prompt_path = _project_path(project_root, prompt_file)
    if not prompt_path.is_file():
        raise ImageToolError(f"prompt file does not exist: {prompt_path}")
    _prompt_text, _sidecar_prompt_sha = _require_matching_prompt(
        prompt_file=prompt_path,
        sidecar=sidecar_payload,
    )
    # Always use the canonical raw-file hash for downstream artifacts,
    # regardless of what the sidecar recorded (it may use an older
    # stripped-text hash convention).
    prompt_sha = _sha256_prompt_file(prompt_path)

    image_info = inspect_image(image_path)
    output_sha = str(image_info.get("sha256") or "").strip().lower()
    sidecar_output_sha = _sidecar_output_sha(sidecar_payload)
    if sidecar_output_sha != output_sha:
        raise ImageToolError(
            "generation sidecar output SHA-256 does not match the current raster; "
            "do not patch only metadata hashes"
        )

    inspect_sidecar_path = (
        _project_path(project_root, inspect_path)
        if inspect_path is not None
        else image_path.with_suffix(image_path.suffix + ".inspect.json")
    )
    _atomic_write_json(inspect_sidecar_path, image_info)

    review_sidecar_path = (
        _project_path(project_root, review_path)
        if review_path is not None
        else image_path.with_suffix(image_path.suffix + ".review.json")
    )
    if not review_sidecar_path.is_file():
        raise ImageToolError(f"review sidecar does not exist: {review_sidecar_path}")
    review_payload = _read_json_object(review_sidecar_path)
    review_sha = _review_image_sha(review_payload)
    if review_sha and review_sha != output_sha:
        raise ImageToolError("review sidecar image SHA-256 does not match the current raster")

    provenance_sidecar_path = (
        _project_path(project_root, provenance_path)
        if provenance_path is not None
        else image_path.with_suffix(image_path.suffix + ".provenance.json")
    )
    model = str(sidecar_payload.get("model") or "gpt-image-2")
    requested_size = str(
        sidecar_payload.get("requested_size")
        or f"{image_info.get('width') or 0}x{image_info.get('height') or 0}"
    )
    prompt_rel = _recorded_prompt_path(project_root, prompt_path, sidecar_payload)
    output_rel = _project_relative(project_root, image_path)
    sidecar_rel = _project_relative(project_root, sidecar_path)
    inspect_rel = _project_relative(project_root, inspect_sidecar_path)
    review_rel = _project_relative(project_root, review_sidecar_path)
    provenance_rel = _project_relative(project_root, provenance_sidecar_path)
    review_file_sha = _sha256_file(review_sidecar_path)

    provenance = {
        "figure_id": figure_id,
        "figure_type": figure_type,
        "generator": "codex-image2",
        "model": model,
        "generator_model": model,
        "tool": "argus_skill.verticals.research.figure_tool",
        "prompt_template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "figure_studio_source": PAPER_FIGURE_STUDIO_SOURCE_ID,
        "figure_studio_stage": figure_studio_stage,
        "prompt_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "output_path": output_rel,
        "output_sha256": output_sha,
        "sidecar_path": sidecar_rel,
        "inspect_path": inspect_rel,
        "review_path": review_rel,
        "review_sha256": review_file_sha,
        "requested_size": requested_size,
        "width": image_info.get("width"),
        "height": image_info.get("height"),
    }
    original_requested_size = sidecar_payload.get("original_requested_size")
    if isinstance(original_requested_size, str) and original_requested_size:
        provenance["original_requested_size"] = original_requested_size
    if sidecar_payload.get("size_normalized_to_multiple_of_16") is True:
        provenance["size_normalized_to_multiple_of_16"] = True
    _atomic_write_json(provenance_sidecar_path, provenance)

    entry = {
        "figure_id": figure_id,
        "figure_type": figure_type,
        "source": "raster",
        "generator": "codex-image2",
        "model": model,
        "generator_model": model,
        "prompt_template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "figure_studio_source": PAPER_FIGURE_STUDIO_SOURCE_ID,
        "figure_studio_stage": figure_studio_stage,
        "prompt_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "output_path": output_rel,
        "output_sha256": output_sha,
        "sidecar_path": sidecar_rel,
        "inspect_path": inspect_rel,
        "review_path": review_rel,
        "review_sha256": review_file_sha,
        "generation_provenance_path": provenance_rel,
        "requested_size": requested_size,
        "width": image_info.get("width"),
        "height": image_info.get("height"),
    }
    if isinstance(original_requested_size, str) and original_requested_size:
        entry["original_requested_size"] = original_requested_size
    if sidecar_payload.get("size_normalized_to_multiple_of_16") is True:
        entry["size_normalized_to_multiple_of_16"] = True
    cache_result = _register_paper_figure_candidate(
        project_root=project_root,
        entry=entry,
        review_payload=review_payload,
    )
    if cache_result.get("registered"):
        cache_fields = {
            "context_sha256": cache_result.get("context_sha256"),
            "candidate_cache_path": cache_result.get("cache_path"),
            "candidate_cache_passed_count": cache_result.get("passed_candidates"),
            "candidate_cache_reusable": cache_result.get("reusable"),
        }
        provenance.update(cache_fields)
        entry.update(cache_fields)
        _atomic_write_json(provenance_sidecar_path, provenance)
    with figure_manifest_transaction(project_root):
        _load_image2_manifest(manifest_path)
        canonical_manifest_path = preflight_figure_provenance(project_root)
        legacy_manifest_snapshot = (
            manifest_path.read_bytes() if manifest_path.exists() else None
        )
        canonical_manifest_snapshot = (
            canonical_manifest_path.read_bytes()
            if canonical_manifest_path.exists()
            else None
        )
        try:
            _upsert_image2_manifest_entry(manifest_path, entry)
            register_figure(
                project_root=project_root,
                figure_id=figure_id,
                role=figure_type,
                renderer="image2",
                source_path=prompt_path,
                output_path=image_path,
                inputs=(sidecar_path, inspect_sidecar_path, provenance_sidecar_path),
                review_path=review_sidecar_path,
                render_metadata_path=sidecar_path,
                command=(
                    "python -m argus_skill.tools.image_api generate "
                    f"--prompt-file {prompt_rel} --out {output_rel} "
                    f"--size {requested_size}"
                ),
                manifest_path=FIGURE_PROVENANCE_PATH,
                _transaction_locked=True,
            )
        except BaseException:
            _restore_optional_file(manifest_path, legacy_manifest_snapshot)
            _restore_optional_file(
                canonical_manifest_path,
                canonical_manifest_snapshot,
            )
            raise
    entry["figure_provenance_path"] = (
        "paper/figures/FIGURE_PROVENANCE.json"
    )
    return entry


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.verticals.research.figure_tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    paper = sub.add_parser("paper-prompt", help="write the canonical Argus paper figure prompt")
    paper.add_argument("--out", type=Path, required=True)
    paper.add_argument("--project-root", type=Path, default=Path("."))
    paper.add_argument("--figure-type", default="method")
    paper.add_argument(
        "--ignore-reviewed-cache",
        action="store_true",
        help="write another prompt even when a reviewed candidate is already reusable",
    )
    paper.add_argument(
        "--min-candidates",
        type=int,
        default=PAPER_FIGURE_MIN_REVIEWED_CANDIDATES,
    )
    paper.add_argument("--force", action="store_true")
    paper.add_argument("--studio-stage", default=PAPER_FIGURE_STUDIO_DEFAULT_STAGE)
    paper.add_argument("--figure-title", default="Method Overview")
    paper.add_argument("--input-label", default="Literature-grounded inputs")
    paper.add_argument("--mechanism-label", default="Reusable agent skill loop")
    paper.add_argument("--verification-label", default="Evidence gate")
    paper.add_argument("--state-label", default="Reusable state/library")
    paper.add_argument("--execution-label", default="Agent execution")
    paper.add_argument("--output-label", default="Submission-ready paper")
    paper.add_argument("--evidence-label", default="Full-scale evidence")
    paper.add_argument("--benefit-label", default="Better grounded claims")
    paper.add_argument("--failure-label", default="Overclaiming avoided")
    paper.add_argument("--caption-plan", default=None)
    paper.add_argument("--legend-plan", default=None)
    paper.add_argument("--body-reference-plan", default=None)
    paper.add_argument("--core-step-visibility-plan", default=None)
    paper.add_argument("--claimed-improvement-anchor", default=None)
    paper.add_argument("--symbol-formula-necessity", default=None)
    paper.add_argument("--semantic-contract", default=None)
    paper.add_argument("--layout-variant", default=None)
    paper.add_argument("--venue", default=None, help="venue key (e.g. AAAI, EMNLP) for the figure style persona")

    rev = sub.add_parser(
        "review",
        help="review a local paper figure with the vision-capable text model",
    )
    rev.add_argument("--image", type=Path, required=True)
    rev.add_argument("--out", type=Path)
    rev.add_argument("--prompt")
    rev.add_argument("--prompt-file", type=Path)
    rev.add_argument("--rubric", default="")
    rev.add_argument("--venue", default=None, help="venue key (e.g. AAAI, EMNLP) for the reviewer persona")
    rev.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    rev.add_argument("--max-retries", type=int, default=_DEFAULT_MAX_RETRIES)

    sync = sub.add_parser(
        "sync-paper-metadata",
        help="synchronize IMAGE2_FIGURES.json and provenance from image-2 sidecars",
    )
    sync.add_argument("--project-root", type=Path, default=Path("."))
    sync.add_argument("--image", type=Path, required=True)
    sync.add_argument("--figure-id", required=True)
    sync.add_argument("--figure-type", default="method")
    sync.add_argument("--manifest", type=Path, default=Path("paper/figures/IMAGE2_FIGURES.json"))
    sync.add_argument("--prompt-file", type=Path)
    sync.add_argument("--sidecar", type=Path)
    sync.add_argument("--inspect-path", type=Path)
    sync.add_argument("--review-path", type=Path)
    sync.add_argument("--provenance-path", type=Path)
    sync.add_argument("--figure-studio-stage", default=PAPER_FIGURE_STUDIO_DEFAULT_STAGE)

    freeze = sub.add_parser(
        "freeze-paper-context",
        help="freeze claim/evidence and structure inputs before image-2 generation",
    )
    freeze.add_argument("--project-root", type=Path, default=Path("."))
    freeze.add_argument(
        "--input",
        action="append",
        type=Path,
        default=[],
        help="explicit stable context input; repeatable",
    )
    freeze.add_argument(
        "--out",
        type=Path,
        default=PAPER_FIGURE_CONTEXT_FREEZE_PATH,
    )

    cache = sub.add_parser(
        "paper-cache-status",
        help="report whether reviewed image-2 candidates can be reused",
    )
    cache.add_argument("--project-root", type=Path, default=Path("."))
    cache.add_argument("--figure-type", default="method")
    cache.add_argument(
        "--min-candidates",
        type=int,
        default=PAPER_FIGURE_MIN_REVIEWED_CANDIDATES,
    )
    cache.add_argument(
        "--max-candidates",
        type=int,
        default=PAPER_FIGURE_MAX_REVIEWED_CANDIDATES,
    )

    args = parser.parse_args(argv)
    try:
        if args.cmd == "paper-prompt":
            if not args.ignore_reviewed_cache:
                cache_status = paper_figure_cache_status(
                    project_root=args.project_root,
                    figure_type=args.figure_type,
                    min_candidates=args.min_candidates,
                )
                if cache_status["reusable"]:
                    _print_json({"cache_hit": True, **cache_status})
                    return 0
            kwargs: dict[str, Any] = {
                "prompt_file": args.out,
                "studio_stage": args.studio_stage,
                "figure_title": args.figure_title,
                "input_label": args.input_label,
                "mechanism_label": args.mechanism_label,
                "verification_label": args.verification_label,
                "state_label": args.state_label,
                "execution_label": args.execution_label,
                "output_label": args.output_label,
                "evidence_label": args.evidence_label,
                "benefit_label": args.benefit_label,
                "failure_label": args.failure_label,
                "force": bool(args.force),
            }
            for cli_name, helper_name in (
                ("caption_plan", "caption_plan"),
                ("legend_plan", "legend_plan"),
                ("body_reference_plan", "body_reference_plan"),
                ("core_step_visibility_plan", "core_step_visibility_plan"),
                ("claimed_improvement_anchor", "claimed_improvement_anchor"),
                ("symbol_formula_necessity", "symbol_formula_necessity"),
                ("semantic_contract", "semantic_contract"),
                ("layout_variant", "layout_variant"),
            ):
                value = getattr(args, cli_name)
                if value is not None:
                    kwargs[helper_name] = value
            if args.venue is not None:
                kwargs["venue_profile"] = get_venue_profile(args.venue)
            _print_json(write_paper_figure_prompt(**kwargs))
            return 0
        if args.cmd == "review":
            prompt = _read_prompt(args.prompt, args.prompt_file) if (args.prompt or args.prompt_file) else ""
            _print_json(review_image(
                image=args.image,
                out=args.out,
                prompt=prompt,
                rubric=args.rubric,
                venue_profile=get_venue_profile(args.venue) if args.venue is not None else None,
                timeout=float(args.timeout),
                max_retries=int(args.max_retries),
            ))
            return 0
        if args.cmd == "sync-paper-metadata":
            _print_json(sync_paper_metadata(
                project_root=args.project_root,
                image=args.image,
                figure_id=args.figure_id,
                figure_type=args.figure_type,
                manifest=args.manifest,
                prompt_file=args.prompt_file,
                sidecar=args.sidecar,
                inspect_path=args.inspect_path,
                review_path=args.review_path,
                provenance_path=args.provenance_path,
                figure_studio_stage=args.figure_studio_stage,
            ))
            return 0
        if args.cmd == "freeze-paper-context":
            _print_json(
                freeze_paper_figure_context(
                    project_root=args.project_root,
                    inputs=args.input or None,
                    out=args.out,
                )
            )
            return 0
        if args.cmd == "paper-cache-status":
            _print_json(
                paper_figure_cache_status(
                    project_root=args.project_root,
                    figure_type=args.figure_type,
                    min_candidates=args.min_candidates,
                    max_candidates=args.max_candidates,
                )
            )
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus-skill figure-tool: {_redact(str(exc))}\n")
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
