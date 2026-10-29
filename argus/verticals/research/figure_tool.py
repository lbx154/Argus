"""Paper-figure vision review on top of the domain-neutral image capability.

The figure studio skill chooses each figure's renderer; generative imagery
(image-2) is limited to non-claim-bearing assets composed inside an editable
figure, per ``skills/engineer/paper-framework-figure-studio.md``. That skill
drives generation directly through ``argus.tools.image_api``; this
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

from argus.tools.image_api import (
    _DEFAULT_MAX_RETRIES,
    _DEFAULT_TIMEOUT_SECONDS,
    _atomic_write_json,
    _load_sidecar_prompt,
    _read_prompt,
    _redact,
)
from argus.tools.image_api import review_image as _generic_review_image


def _review_prompt(
    *,
    original_prompt: str,
    rubric: str,
) -> str:
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
            "You are reviewing an academic paper figure for a top-tier academic venue "
            "submission. Review the rendered raster you "
            "are shown, and read every label directly from the image rather than "
            "trusting the prompt text.\n\n"
            "Follow the Rubric below as the authority for this review. It defines your task, the exact "
            "standards the figure must meet, and the exact JSON fields you must return. Return "
            "a single JSON object that includes every field the Rubric requests, "
            "each populated strictly from what you can actually see in the raster. "
            "Always include \"keep_or_regenerate\" (\"keep\" or \"regenerate\"). "
            "Apply the Rubric's rules for deciding whether the figure meets those standards exactly — including exact label "
            "spelling, missing/invented/duplicated labels, wrong or reversed "
            "relationships and arrows, connector penetration, element overlap, "
            "clipping, unreadable final-size type, off-palette colour, and prohibited content. "
            "Where the Rubric and any generic guidance disagree, follow the Rubric. "
            "Return only the JSON object, optionally fenced as ```json ... ```.\n\n"
            f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
            f"Rubric:\n{rubric}"
        )
    return (
        "You are reviewing an academic paper figure for a top-tier academic venue submission. "
        "Your sole task is to judge whether the figure effectively communicates "
        "the paper's method to a reader and is ready for submission. Minor colour "
        "or spacing preferences are not quality issues, but connector geometry, "
        "overlap, clipping, typography, and the conventional visual language of diagrams are.\n\n"
        "Focus on these questions:\n"
        "1. Does the figure faithfully represent the paper's method/architecture?\n"
        "2. Is the core contribution module visible (not an empty box)?\n"
        "3. Are labels readable and correctly spelled?\n"
        "4. Do all declared arrows have correct source, target, direction, branch "
        "label, and node-boundary termination, without penetrating unrelated nodes or text?\n"
        "5. Are all elements non-overlapping, unclipped, and readable at final paper size?\n"
        "6. Would a venue reviewer understand the method from this figure and its caption?\n\n"
        "Return JSON with:\n"
        "- score_1_to_5: your advisory overall impression of the figure\n"
        "- major_issues: semantic errors plus connector penetration, overlap, "
        "clipping, unreadable type, or unconventional grammar that prevents a "
        "submission-ready figure. Do not list minor colour preferences.\n"
        "- concrete_revision_prompt: if your judgment is 'regenerate', describe the "
        "specific revision to the figure's source that fixes the actual problem.\n"
        "- keep_or_regenerate: your judgment — 'keep' if the figure is both "
        "semantically correct and submission-ready, otherwise 'regenerate' — with "
        "your rationale in major_issues.\n\n"
        f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
        f"Rubric:\n{rubric or 'Does this figure effectively communicate the paper method to a venue reviewer?'}"
    )


def review_image(
    *,
    image: Path,
    out: Path | None = None,
    prompt: str = "",
    rubric: str = "",
    env: Mapping[str, str] | None = None,
    timeout: float | None = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Paper-figure vision review: build the paper prompt, then
    call the generic ``tools.image_api`` reviewer to actually talk to the
    model.
    """
    original_prompt = prompt.strip() or _load_sidecar_prompt(image)
    review_instruction = _review_prompt(
        original_prompt=original_prompt,
        rubric=rubric,
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


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus.verticals.research.figure_tool")
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
        help="accepted for older commands; not used",
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
                timeout=args.timeout,
                max_retries=int(args.max_retries),
            ))
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus figure-tool: {_redact(str(exc))}\n")
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
