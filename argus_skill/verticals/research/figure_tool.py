"""Paper-figure vision review on top of the domain-neutral image capability.

The Research Visualization Router chooses each figure's renderer; generative
imagery (image-2) is limited to non-claim-bearing assets composed inside an
editable figure, per ``skills/engineer/paper-illustration-image2.md``. That
skill drives generation directly through ``argus_skill.tools.image_api``; this
module contributes the paper-aware REVIEW instruction so a rendered figure is
judged the way a venue reviewer would judge it. Use this module's ``review``
CLI/function for paper figures, not the domain-neutral one in
``tools.image_api``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from argus_skill.tools.image_api import (
    _DEFAULT_MAX_RETRIES,
    _DEFAULT_TIMEOUT_SECONDS,
    _atomic_write_json,
    _load_sidecar_prompt,
    _read_prompt,
    _redact,
)
from argus_skill.tools.image_api import review_image as _generic_review_image
from argus_skill.verticals.research.venue_profiles import (
    VenueProfile,
    resolve_venue_profile,
)


def _review_prompt(
    *,
    original_prompt: str,
    rubric: str,
    venue_profile: VenueProfile | None = None,
) -> str:
    figure_persona = (
        venue_profile.figure_style_persona
        if venue_profile is not None
        else "top-tier academic venue"
    )
    reviewer_persona = (
        venue_profile.reviewer_persona if venue_profile is not None else "venue"
    )
    # When the caller supplies a real rubric (as the AI-figure validator does),
    # that rubric is authoritative: it defines the reviewer's task, the exact
    # pass/fail criteria, AND the exact JSON fields to emit (e.g.
    # ``confirmed_labels``, ``findings``, ``extra_tokens_present``). The generic
    # "communicate the method" schema below must never override those fields,
    # otherwise a caller asking for structured verdicts (label confirmation,
    # exact-content checks) silently gets only ``score_1_to_5`` back. When no
    # rubric is supplied, the generic schema below is used.
    if rubric and rubric.strip():
        return (
            f"You are reviewing an academic paper figure for a {figure_persona} "
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
            "relationships and arrows, connector penetration, element overlap, "
            "clipping, unreadable final-size type, off-palette colour, and prohibited content. "
            "Where the Rubric and any generic guidance disagree, the Rubric wins. "
            "Return only the JSON object, optionally fenced as ```json ... ```.\n\n"
            f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
            f"Rubric:\n{rubric}"
        )
    return (
        f"You are reviewing an academic paper figure for a {figure_persona} submission. "
        "Your ONLY job is to judge whether the figure effectively communicates "
        "the paper's method to a reader and is ready for submission. Minor colour "
        "or spacing preferences are not quality issues, but connector geometry, "
        "overlap, clipping, typography, and conventional diagram grammar are.\n\n"
        "Focus on these questions:\n"
        "1. Does the figure faithfully represent the paper's method/architecture?\n"
        "2. Is the core contribution module visible (not an empty box)?\n"
        "3. Are labels readable and correctly spelled?\n"
        "4. Do all declared arrows have correct source, target, direction, branch "
        "label, and node-boundary termination, without penetrating unrelated nodes or text?\n"
        "5. Are all elements non-overlapping, unclipped, and readable at final paper size?\n"
        f"6. Would a {reviewer_persona} reviewer understand the method from this figure + its caption?\n\n"
        "Return JSON with:\n"
        "- score_1_to_5: 4+ means acceptable for submission, 3 means needs one more pass, "
        "1-2 means fundamentally wrong (wrong modules, misleading flow, unreadable)\n"
        "- major_issues: semantic errors plus connector penetration, overlap, "
        "clipping, unreadable type, or unconventional grammar that prevents a "
        "submission-ready figure. Do not list minor colour preferences.\n"
        "- concrete_revision_prompt: if score < 4, describe the SPECIFIC revision "
        "that fixes the actual problem in the figure's source.\n"
        "- keep_or_regenerate: 'keep' only if score >= 4 and the figure is both "
        "semantically correct and submission-ready; otherwise 'regenerate'.\n\n"
        f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
        f"Rubric:\n{rubric or f'Does this figure effectively communicate the paper method to a {reviewer_persona} reviewer?'}"
    )


def review_image(
    *,
    image: Path,
    out: Path | None = None,
    prompt: str = "",
    rubric: str = "",
    venue_profile: VenueProfile | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Paper-figure vision review: build the venue-aware paper prompt, then
    call the generic ``tools.image_api`` reviewer to actually talk to the
    model.
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


def _resolve_optional_venue(project_root: Path) -> VenueProfile | None:
    """Best-effort venue resolution for review persona wording.

    A project without a researched venue profile still gets a useful figure
    review — the persona just stays generic.
    """
    try:
        return resolve_venue_profile(project_root)
    except Exception:  # noqa: BLE001 — persona is a nicety, never a blocker
        return None


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.verticals.research.figure_tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    rev = sub.add_parser(
        "review",
        help="review a local paper figure with the vision-capable text model",
    )
    rev.add_argument("--image", type=Path, required=True)
    rev.add_argument("--out", type=Path)
    rev.add_argument("--prompt")
    rev.add_argument("--prompt-file", type=Path)
    rev.add_argument("--rubric", default="")
    rev.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="project whose researched venue profile shapes the reviewer persona",
    )
    rev.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    rev.add_argument("--max-retries", type=int, default=_DEFAULT_MAX_RETRIES)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "review":
            prompt = _read_prompt(args.prompt, args.prompt_file) if (args.prompt or args.prompt_file) else ""
            _print_json(review_image(
                image=args.image,
                out=args.out,
                prompt=prompt,
                rubric=args.rubric,
                venue_profile=_resolve_optional_venue(args.project_root),
                timeout=args.timeout,
                max_retries=int(args.max_retries),
            ))
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus-skill figure-tool: {_redact(str(exc))}\n")
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
