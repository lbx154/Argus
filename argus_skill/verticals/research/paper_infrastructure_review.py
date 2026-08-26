"""Generate model-backed review artifacts for paper-facing infrastructure leaks."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus_skill.tools.image_api import (
    ApiError,
    ImageToolError,
    _json_request,
    _parse_chat_text,
    _parse_responses_text,
    _redact,
    _require_route,
)

from ._review_contract_constants import (
    PAPER_INFRASTRUCTURE_REVIEW_GENERATED_BY,
    PAPER_INFRASTRUCTURE_REVIEW_HISTORY_PATH,
    REVIEW_INPUT_SHA256_FIELD,
    REVIEW_PROMPT_SHA256_FIELD,
    review_sha256_file,
    review_sha256_json,
    review_sha256_text,
)
from ._reviewer_runner_fallback import (
    ReviewerRunnerError,
    run_reviewer_prompt_via_runner,
    runner_fallback_enabled,
)
from .academic_language_review import (
    PAPER_MAIN_TEX_PATH,
    _append_history,
    _read_source_texts,
    _write_json,
    _write_text,
    collect_latex_source_paths,
    describe_reviewer_route_unavailable,
)
from .venue_profiles import VenueProfile, resolve_venue_profile

PAPER_INFRASTRUCTURE_REVIEW_JSON_PATH = Path("paper/PAPER_INFRASTRUCTURE_REVIEW.json")
PAPER_INFRASTRUCTURE_REVIEW_MD_PATH = Path("paper/PAPER_INFRASTRUCTURE_REVIEW.md")
MIN_PAPER_INFRASTRUCTURE_REVIEW_SCORE = 4.0
DEFAULT_TIMEOUT_SECONDS = 500.0
PAPER_INFRASTRUCTURE_REVIEW_SOURCE_CHAR_LIMIT = 140000
REQUIRED_CHECKED_SCOPES: tuple[str, ...] = (
    "title",
    "abstract",
    "body",
    "captions",
    "tables",
    "appendix",
)
class PaperInfrastructureReviewError(RuntimeError):
    """Raised when the infrastructure-leak review cannot be generated."""


def generate_paper_infrastructure_review(
    project_root: Path,
    *,
    review_mode: str = "model",
    threshold: float = MIN_PAPER_INFRASTRUCTURE_REVIEW_SCORE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    iteration: int | None = None,
    write: bool = True,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review manuscript source for reader-facing local infrastructure leaks."""

    if review_mode != "model":
        raise PaperInfrastructureReviewError(
            "paper infrastructure review is intentionally model-only; use --review-mode model"
        )

    root = Path(project_root)
    threshold = max(float(threshold), MIN_PAPER_INFRASTRUCTURE_REVIEW_SCORE)
    venue = None
    venue_error: KeyError | None = None
    try:
        venue = resolve_venue_profile(root)
    except KeyError as exc:
        venue_error = exc
    iteration = iteration or _next_iteration(root)
    source_paths, missing_sources = collect_latex_source_paths(root)
    source_snapshots = [
        {"path": rel_path, "sha256": review_sha256_file(root / rel_path)}
        for rel_path in source_paths
        if (root / rel_path).is_file()
    ]
    source_text_by_path = _read_source_texts(root, source_paths)

    issues: list[dict[str, Any]] = []
    if venue_error is not None:
        issues.append(
            _issue(
                "unresolved_venue_profile",
                "blocking",
                str(venue_error),
                action="resolve_venue_profile",
            )
        )
    if not (root / PAPER_MAIN_TEX_PATH).is_file():
        issues.append(
            _issue(
                "missing_main_tex",
                "blocking",
                "paper/main.tex is missing; draft the paper before infrastructure-leak review",
                action="rewrite_setup_as_paper_facing",
            )
        )
    for rel_path in missing_sources:
        issues.append(
            _issue(
                "missing_latex_source",
                "blocking",
                f"referenced LaTeX source {rel_path} is missing",
                action="rewrite_setup_as_paper_facing",
                target=rel_path,
            )
        )

    blocking_issues = [issue for issue in issues if issue.get("severity") == "blocking"]
    checked_scope: list[str] = []
    model_review: dict[str, Any] | None = None
    leak_free: bool | None = None

    if source_text_by_path and not blocking_issues and venue is not None:
        try:
            model_review = _run_model_review(
                root=root,
                source_text_by_path=source_text_by_path,
                threshold=threshold,
                env=env,
                timeout=timeout,
                venue=venue,
            )
        except (ImageToolError, PaperInfrastructureReviewError) as exc:
            issue = _issue(
                "model_review_unavailable",
                "blocking",
                f"text reviewer could not inspect paper infrastructure leaks: "
                f"{describe_reviewer_route_unavailable(exc, env)}",
                action="rewrite_setup_as_paper_facing",
            )
            issues.append(issue)
            blocking_issues.append(issue)
        else:
            raw_leak_free = model_review.get("leak_free")
            leak_free = raw_leak_free if isinstance(raw_leak_free, bool) else None
            checked_scope = [
                str(item).strip()
                for item in model_review.get("checked_scope", [])
                if isinstance(item, str) and item.strip()
            ]
    elif not source_text_by_path:
        issue = _issue(
            "missing_reviewable_latex_source",
            "blocking",
            "no reviewable LaTeX source was found for paper infrastructure review",
            action="rewrite_setup_as_paper_facing",
        )
        issues.append(issue)
        blocking_issues.append(issue)

    # ``structural_status`` reflects only whether the tool could produce facts
    # (missing manuscript source / model unavailable). It is NOT a quality
    # verdict. The harness emits no PASS/FAIL on paper quality here.
    structural_status = "blocked" if blocking_issues else "ok"

    result: dict[str, Any] = {
        "schema_version": 2,
        "generated_by": PAPER_INFRASTRUCTURE_REVIEW_GENERATED_BY,
        "created_at": datetime.now(UTC).isoformat(),
        "iteration": iteration,
        "review_method": "llm_text_reviewer",
        "decision_authority": "agent_checklist",
        "harness_verdict": None,
        "no_harness_quality_verdict": True,
        "structural_status": structural_status,
        "leak_free": leak_free,
        "checked_scope": checked_scope,
        "source_snapshots": source_snapshots,
        "reviewed_source_count": len(source_snapshots),
        "issues": issues,
        "blocking_issues": blocking_issues,
        "review_policy": {
            "rubric": "paper-facing-infrastructure-leak-v2",
            "decision_authority": "reviewer agent decides against the stage checklist; "
            "the harness reports leak findings only and emits no quality verdict",
            "required_checked_scope": list(REQUIRED_CHECKED_SCOPES),
            "paper_facing_target": "title, abstract, body prose, captions, tables, and appendix prose",
        },
    }
    if model_review is not None:
        result["model_review"] = model_review

    if write:
        _write_json(root / PAPER_INFRASTRUCTURE_REVIEW_JSON_PATH, result)
        _write_text(root / PAPER_INFRASTRUCTURE_REVIEW_MD_PATH, _review_markdown(result))
        _append_history(root, PAPER_INFRASTRUCTURE_REVIEW_HISTORY_PATH, result)
    return result


def _run_model_review(
    *,
    root: Path,
    source_text_by_path: Mapping[str, str],
    threshold: float,
    env: Mapping[str, str] | None,
    timeout: float,
    venue: VenueProfile,
) -> dict[str, Any]:
    prompt = _review_prompt(
        source_text_by_path=source_text_by_path, threshold=threshold, venue=venue
    )
    prompt_sha256 = review_sha256_text(prompt)
    try:
        route = _require_route("reviewer", env)
    except ImageToolError:
        # No OpenAI-compatible reviewer route configured. This gate is a pure
        # TEXT judgement (manuscript prose, never figures), so fall back to the
        # fleet agent-CLI runner (e.g. copilot) instead of hard-blocking the
        # paper. Restore the historic block with
        # ARGUS_SKILL_REVIEWER_DISABLE_RUNNER_FALLBACK=1.
        if not runner_fallback_enabled(env):
            raise
        try:
            raw_text, review_model = run_reviewer_prompt_via_runner(
                prompt,
                run_label="research.paper_infrastructure_review",
                working_dir=str(root),
                env=env,
                timeout=timeout,
            )
        except ReviewerRunnerError as exc:
            raise PaperInfrastructureReviewError(str(exc)) from exc
        endpoint = "runner"
    else:
        review_model = route.model
        endpoint = "/responses"
        try:
            data = _json_request(
                route,
                endpoint,
                {"model": route.model, "input": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            raw_text = _parse_responses_text(data)
        except ApiError as exc:
            if exc.status not in (400, 404):
                raise
            endpoint = "/chat/completions"
            data = _json_request(
                route,
                endpoint,
                {"model": route.model, "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            raw_text = _parse_chat_text(data)
    if not raw_text:
        raise PaperInfrastructureReviewError("reviewer model returned no text")
    parsed = _parse_review_text(raw_text)
    parsed["raw_review_text"] = raw_text
    parsed["model"] = review_model
    parsed["endpoint"] = endpoint
    parsed["reviewed_root"] = str(root)
    parsed[REVIEW_PROMPT_SHA256_FIELD] = prompt_sha256
    parsed[REVIEW_INPUT_SHA256_FIELD] = review_sha256_json(
        {
            "prompt_sha256": prompt_sha256,
            "source_sha256": {
                path: review_sha256_text(text)
                for path, text in sorted(source_text_by_path.items())
            },
            "threshold": threshold,
        }
    )
    return parsed


def _review_prompt(
    *, source_text_by_path: Mapping[str, str], threshold: float, venue: VenueProfile
) -> str:
    _ = threshold
    numbered_source = _complete_numbered_source(source_text_by_path)
    return (
        f"You are a strict {venue.reviewer_persona} paper reviewer checking only whether reader-facing "
        "manuscript prose leaks local execution infrastructure irrelevant to the "
        "scientific paper. Inspect title, abstract, body, captions, tables, and "
        "appendix prose. Ignore LaTeX comments, build logs, and external artifacts "
        "unless the manuscript renders them for readers. Reject leaks of local "
        "hardware ordinals, local machine capacity, or device placement such as "
        "GPU card numbers, single local GPU, local GPU, workstation/node labels, "
        "cuda:6, CUDA_VISIBLE_DEVICES, local hardware IDs, cache directories such "
        "as HF_HOME, TRANSFORMERS_CACHE, TORCH_HOME, XDG_CACHE_HOME, /root/.cache, "
        "absolute local paths under /root or /home, local evaluation or local "
        "training configuration phrasing, local runtime/environment paragraphs, "
        "local software-environment tables that document the authoring machine "
        "rather than the evaluated research system, API keys, private endpoints, "
        "raw local runner commands or run identifiers that encode device/config "
        "tokens, virtual-environment commands, private output roots, or "
        "project-specific experiment directories rendered as the paper's "
        "reproducibility interface. A reproducibility appendix may describe a "
        "neutral replay command alias, seed policy, public benchmark, metric, "
        "split, and artifact types such as manifest/status/progress/raw rows/"
        "summary TSV, but raw local CLI strings and path names must stay in "
        "non-rendered manifests/logs or supplementary package metadata. Reject "
        "body/setup/result prose that turns operational audit-bundle metadata "
        "into scientific exposition: wall-clock logging, artifact hashes, status "
        "snapshots, progress logs, STOP-file cancellation contracts, internal "
        "manifest mechanics, or provenance-refresh workflow details belong in "
        "appendix replay notes, manifests, or supplementary metadata, not in the "
        "main narrative unless the paper explicitly studies that infrastructure. "
        "Orchestration-daemon details, internal role/route labels, capability "
        "vault configuration, validation or review artifacts, image-tool "
        "plumbing, and authoring model identifiers when they are not evaluated "
        "systems. Allow legitimate "
        "paper-facing reproducibility facts: evaluated model/backend names, public "
        "dataset or benchmark versions, task counts, metrics, decoding or budget "
        "settings, and high-level compute cost only when written as research "
        "method detail rather than local machine or authoring environment "
        "configuration. If the paper "
        "actually studies infrastructure, require the manuscript to distinguish "
        "the studied system from the authoring/review infrastructure. Write a prose "
        "review, not JSON. Order findings by severity; for every material finding give "
        "the source path and line or section, quote the reader-facing evidence, and "
        "suggest a concrete fix. If no leak is present, say so plainly. End with these "
        "two tolerant named lines (semicolon-separate the inspected scopes):\n"
        "LEAK_FREE=true or false\n"
        "CHECKED_SCOPE=title; abstract; body; captions; tables; appendix\n"
        "The source inventory "
        "below is complete and untruncated for the reviewed LaTeX files; if no "
        "appendix source appears, treat the appendix as absent rather than as an "
        "uninspected missing scope.\n\n"
        f"Complete numbered LaTeX sources:\n{numbered_source}"
    )


def _parse_review_text(text: str) -> dict[str, Any]:
    """Read two useful facts while preserving all reviewer prose verbatim."""
    from ...core.role_reply import legacy_json_object, read_block, read_key_values, read_list

    legacy = legacy_json_object(text)
    if legacy is not None:
        parsed = dict(legacy)
    else:
        keys = ("LEAK_FREE", "CHECKED_SCOPE")
        values = read_key_values(text, keys)
        checked_scope = read_block(text, "CHECKED_SCOPE", keys)
        if checked_scope:
            values["CHECKED_SCOPE"] = checked_scope
        raw_leak_free = str(values.get("LEAK_FREE") or "").strip().casefold()
        parsed = {
            "leak_free": (
                True if raw_leak_free in {"true", "yes"}
                else False if raw_leak_free in {"false", "no"}
                else None
            ),
            "checked_scope": list(read_list(values, "CHECKED_SCOPE")),
        }
    parsed["review_text"] = text
    return parsed


def _complete_numbered_source(source_text_by_path: Mapping[str, str]) -> str:
    lines: list[str] = []
    for rel_path, text in source_text_by_path.items():
        lines.append(f"--- {rel_path} ---")
        for line_no, line in enumerate(text.splitlines(), start=1):
            lines.append(f"{rel_path}:L{line_no}: {line}")
    numbered_source = "\n".join(lines)
    if len(numbered_source) > PAPER_INFRASTRUCTURE_REVIEW_SOURCE_CHAR_LIMIT:
        raise PaperInfrastructureReviewError(
            "paper infrastructure review requires complete untruncated LaTeX "
            f"sources, but the collected source is {len(numbered_source)} chars "
            f"over the {PAPER_INFRASTRUCTURE_REVIEW_SOURCE_CHAR_LIMIT} char review "
            "limit; split or reduce rendered paper-facing sources before review"
        )
    return numbered_source


def _review_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Paper Infrastructure Review",
        "",
        "- Decision authority: `agent_checklist` (the reviewer agent decides; "
        "the harness emits no quality verdict)",
        f"- Structural status: `{result['structural_status']}`",
        f"- Review method: `{result['review_method']}`",
        f"- Leak free (reviewer model): `{result['leak_free']}`",
        "",
    ]
    issues = result.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["## Structural issues", ""])
        for issue in issues:
            if isinstance(issue, dict):
                lines.append(f"- `{issue.get('severity', 'unknown')}` {issue.get('message', '')}")
        lines.append("")
    review = result.get("model_review")
    if isinstance(review, dict) and str(review.get("review_text") or "").strip():
        lines.extend(["## Advisory infrastructure review", "", str(review["review_text"]), ""])
    return "\n".join(lines)


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    action: str,
    target: str = "paper/main.tex",
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "target": target,
        "action": action,
    }


def _next_iteration(root: Path) -> int:
    history = root / PAPER_INFRASTRUCTURE_REVIEW_HISTORY_PATH
    if not history.is_file():
        return 1
    try:
        lines = [line for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return 1
    return len(lines) + 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.verticals.research.paper_infrastructure_review",
        description=(
            "Score the selected-venue paper for reader-facing infrastructure leaks."
        ),
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--review-mode", choices=("model",), default="model")
    parser.add_argument("--threshold", type=float, default=MIN_PAPER_INFRASTRUCTURE_REVIEW_SCORE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--iteration", type=int)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write paper/PAPER_INFRASTRUCTURE_REVIEW.json and .md",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = generate_paper_infrastructure_review(
            args.project_root,
            review_mode=args.review_mode,
            threshold=args.threshold,
            timeout=args.timeout,
            iteration=args.iteration,
            write=bool(args.write),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus-skill paper-infrastructure-review: {_redact(str(exc))}\n")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    # Exit nonzero only when the tool could not produce facts (structural /
    # tool failure), never as a harness quality verdict.
    return 0 if result.get("structural_status") == "ok" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
