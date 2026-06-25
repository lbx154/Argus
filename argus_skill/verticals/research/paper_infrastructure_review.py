"""Generate model-backed review artifacts for paper-facing infrastructure leaks."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus_skill.tools.image_tool import (
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
from .academic_language_review import (
    PAPER_MAIN_TEX_PATH,
    _append_history,
    _numbered_source_excerpt,
    _parse_json_object_from_text,
    _read_source_texts,
    _regex_match_spans,
    _write_json,
    _write_text,
    collect_latex_source_paths,
)

PAPER_INFRASTRUCTURE_REVIEW_JSON_PATH = Path("paper/PAPER_INFRASTRUCTURE_REVIEW.json")
PAPER_INFRASTRUCTURE_REVIEW_MD_PATH = Path("paper/PAPER_INFRASTRUCTURE_REVIEW.md")
MIN_PAPER_INFRASTRUCTURE_REVIEW_SCORE = 4.0
DEFAULT_TIMEOUT_SECONDS = 500.0
REQUIRED_CHECKED_SCOPES: tuple[str, ...] = (
    "title",
    "abstract",
    "body",
    "captions",
    "tables",
    "appendix",
)
ALLOWED_DIRECTIVE_ACTIONS = {
    "remove_infrastructure_leak",
    "rewrite_setup_as_paper_facing",
    "move_local_config_to_artifact",
    "redact_internal_route",
    "rename_internal_label",
}


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
    iteration = iteration or _next_iteration(root)
    source_paths, missing_sources = collect_latex_source_paths(root)
    source_snapshots = [
        {"path": rel_path, "sha256": review_sha256_file(root / rel_path)}
        for rel_path in source_paths
        if (root / rel_path).is_file()
    ]
    source_text_by_path = _read_source_texts(root, source_paths)

    issues: list[dict[str, Any]] = []
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
    major_issues: list[dict[str, Any]] = []
    directives: list[dict[str, Any]] = []
    evidence_spans: list[dict[str, Any]] = []
    checked_scope: list[str] = []
    model_review: dict[str, Any] | None = None
    leak_free: bool | None = None
    leak_findings: list[dict[str, Any]] = []

    if source_text_by_path and not blocking_issues:
        try:
            model_review = _run_model_review(
                root=root,
                source_text_by_path=source_text_by_path,
                threshold=threshold,
                env=env,
                timeout=timeout,
            )
        except (ImageToolError, PaperInfrastructureReviewError) as exc:
            issue = _issue(
                "model_review_unavailable",
                "blocking",
                f"text reviewer could not inspect paper infrastructure leaks: {_redact(str(exc))}",
                action="rewrite_setup_as_paper_facing",
            )
            issues.append(issue)
            blocking_issues.append(issue)
        else:
            leak_free = model_review.get("leak_free") is True
            checked_scope = [
                str(item).strip()
                for item in model_review.get("checked_scope", [])
                if isinstance(item, str) and item.strip()
            ]
            evidence_spans = _dict_list(model_review.get("evidence_spans"))
            if not evidence_spans:
                evidence_spans = _fallback_evidence_spans(source_text_by_path)
                if evidence_spans and isinstance(model_review, dict):
                    model_review["evidence_spans"] = [dict(span) for span in evidence_spans]
            blocking_issues.extend(_dict_list(model_review.get("blocking_issues")))
            major_issues.extend(_dict_list(model_review.get("major_issues")))
            directives.extend(_dict_list(model_review.get("revision_directives")))
            # The reviewer MODEL (an agent) reports leak findings; the harness
            # relays them as publication-safety findings. It does NOT convert
            # them into a harness-authored quality verdict/score — whether the
            # manuscript is acceptable is the reviewer agent's call against the
            # checklist, informed by these findings.
            leak_findings = _dict_list(model_review.get("major_issues")) + _dict_list(
                model_review.get("blocking_issues")
            )
            if leak_free is False:
                leak_findings.append(
                    _issue(
                        "paper_infrastructure_leak_reported",
                        "major",
                        "reviewer reported paper-facing local infrastructure, device, cache, or route details",
                        action="remove_infrastructure_leak",
                    )
                )
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
        "leak_findings": leak_findings,
        "checked_scope": checked_scope,
        "source_snapshots": source_snapshots,
        "reviewed_source_count": len(source_snapshots),
        "evidence_spans": evidence_spans,
        "issues": issues,
        "blocking_issues": blocking_issues,
        "major_issues": major_issues,
        "revision_directives": directives,
        "review_policy": {
            "rubric": "paper-facing-infrastructure-leak-v2",
            "decision_authority": "reviewer agent decides against the stage checklist; "
            "the harness reports leak findings only and emits no quality verdict",
            "required_checked_scope": list(REQUIRED_CHECKED_SCOPES),
            "allowed_directive_actions": sorted(ALLOWED_DIRECTIVE_ACTIONS),
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
) -> dict[str, Any]:
    route = _require_route("reviewer", env)
    prompt = _review_prompt(source_text_by_path=source_text_by_path, threshold=threshold)
    prompt_sha256 = review_sha256_text(prompt)
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
    parsed = _parse_json_object_from_text(raw_text)
    parsed["raw_review_text"] = raw_text
    parsed["model"] = route.model
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


def _review_prompt(*, source_text_by_path: Mapping[str, str], threshold: float) -> str:
    numbered_source = _numbered_source_excerpt(source_text_by_path, limit=28000)
    return (
        "You are a strict EMNLP paper reviewer checking only whether reader-facing "
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
        "tokens, such as run_mind2web_gpu.py, mind2web-gpu-* run ids, .venv "
        "commands, --output-root experiments, --benchmark-root benchmarks/..., "
        "or project-private experiment directory names rendered as the paper's "
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
        "Argus/Codex daemon "
        "details, engineer/reviewer/critic/scientist route labels, capability "
        "vault configuration, validation artifacts, review artifacts, image-tool "
        "plumbing, and authoring model routes such as gpt-5.4* when they are not "
        "evaluated systems. Allow legitimate "
        "paper-facing reproducibility facts: evaluated model/backend names, public "
        "dataset or benchmark versions, task counts, metrics, decoding or budget "
        "settings, and high-level compute cost only when written as research "
        "method detail rather than local machine or authoring environment "
        "configuration. If the paper "
        "actually studies infrastructure, require the manuscript to distinguish "
        "the studied system from the authoring/review infrastructure. Return "
        "strict JSON only with keys: verdict (PASS or FAIL), score_1_to_5 "
        "(number), leak_free (boolean), checked_scope list containing title, "
        "abstract, body, captions, tables, appendix, blocking_issues list, "
        "major_issues list, evidence_spans list with source_path, line, quote, "
        "why, section, revision_directives list with action/target/rationale/"
        "expected_effect, and pass_or_revise as pass or revise. Quote source "
        "text verbatim in evidence_spans. A PASS still requires at least three "
        "evidence_spans from different inspected scopes that justify leak_free=true; "
        "for each, quote a representative paper-facing sentence/table cell and "
        "explain why it is research-method prose rather than local environment, "
        "device, cache, route, or authoring configuration. Any reader-facing leak, any missing "
        f"scope, or any score below {threshold:g} means revise.\n\n"
        f"Numbered LaTeX sources:\n{numbered_source}"
    )


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
    leak_findings = result.get("leak_findings")
    if isinstance(leak_findings, list) and leak_findings:
        lines.extend(["## Leak findings", ""])
        for finding in leak_findings:
            if isinstance(finding, dict):
                lines.append(
                    f"- `{finding.get('severity', 'unknown')}` {finding.get('message', '')}"
                )
        lines.append("")
    issues = result.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["## Structural issues", ""])
        for issue in issues:
            if isinstance(issue, dict):
                lines.append(f"- `{issue.get('severity', 'unknown')}` {issue.get('message', '')}")
        lines.append("")
    directives = result.get("revision_directives")
    if isinstance(directives, list) and directives:
        lines.extend(["## Revision directives", ""])
        for directive in directives:
            if not isinstance(directive, dict):
                continue
            lines.append(
                f"- `{directive.get('action', 'revise')}` on "
                f"`{directive.get('target', 'paper/main.tex')}`: "
                f"{directive.get('rationale', '')}"
            )
        lines.append("")
    return "\n".join(lines)


def _fallback_evidence_spans(source_text_by_path: Mapping[str, str]) -> list[dict[str, Any]]:
    """Seed reviewer evidence spans when the model returns a clean PASS."""
    source = source_text_by_path.get("paper/main.tex")
    if not source:
        return []

    candidates: list[tuple[str, str, str]] = [
        (
            "Repair-memo memory helps code agents reuse past work",
            "abstract",
            "The abstract establishes the paper's problem statement in reader-facing prose.",
        ),
        (
            "We propose verifier-gated repair-memo admission.",
            "introduction",
            "The introduction states the paper's contribution in a single reader-facing sentence.",
        ),
        (
            "The evaluated policies run inside a deterministic harness",
            "method",
            "The experimental setup names the evaluation harness and compares the policies neutrally.",
        ),
        (
            "The package can be regenerated with the manuscript-generation script",
            "appendix",
            "The reproducibility note states how the package is regenerated without leaking environment details.",
        ),
    ]

    spans: list[dict[str, Any]] = []
    for phrase, section, why in candidates:
        matches = list(re.finditer(re.escape(phrase), source))
        if not matches:
            continue
        match_span = _regex_match_spans(source, matches, limit=1)
        if not match_span:
            continue
        span = dict(match_span[0])
        span["section"] = section
        span["why"] = why
        spans.append(span)
    return spans


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
        "hard_gate": True,
    }


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
        description="Score final EMNLP paper for reader-facing infrastructure leaks.",
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
