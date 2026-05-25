"""Generate final academic-language review artifacts for EMNLP papers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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

PAPER_MAIN_TEX_PATH = Path("paper/main.tex")
ACADEMIC_LANGUAGE_REVIEW_JSON_PATH = Path("paper/ACADEMIC_LANGUAGE_REVIEW.json")
ACADEMIC_LANGUAGE_REVIEW_MD_PATH = Path("paper/ACADEMIC_LANGUAGE_REVIEW.md")
ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH = Path("paper/ACADEMIC_LANGUAGE_REVIEW_history.jsonl")
MIN_ACADEMIC_LANGUAGE_SCORE = 4.0
DEFAULT_TIMEOUT_SECONDS = 500.0
MAX_SOURCE_FILES = 120

SECTION_SCORE_KEYS: tuple[str, ...] = (
    "abstract",
    "introduction",
    "contribution_framing",
    "evidence_alignment",
    "related_work_positioning",
    "style_and_clarity",
)

REQUIRED_CHECK_KEYS: tuple[str, ...] = (
    "clear_problem_gap_contribution",
    "evidence_aligned_claims",
    "five_sentence_abstract_or_equivalent",
    "related_work_methodological",
    "calibrated_no_hype",
    "limitations_scope_present",
)

ALLOWED_DIRECTIVE_ACTIONS = {
    "rewrite_abstract",
    "rewrite_introduction",
    "tighten_contribution_sentence",
    "calibrate_claim",
    "add_evidence_sentence",
    "reorganize_related_work",
    "replace_hype_language",
    "delete_filler",
    "clarify_method_mechanism",
    "rewrite_caption_takeaway",
    "add_limitation_scope",
    "rename_code_like_label",
}

MODEL_REVIEW_METHODS = {"llm_text_reviewer", "hybrid_llm_heuristic"}

GENERIC_OPENING_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "generic_llm_success_opening",
        r"\blarge language models have achieved remarkable (?:success|performance)\b",
    ),
    (
        "generic_recent_years_opening",
        r"\bin recent years,\s+(?:large language models|llms|deep learning)\b",
    ),
    (
        "generic_witnessed_progress_opening",
        r"\brecent years have witnessed\b",
    ),
    (
        "generic_rapid_development_opening",
        r"\bwith the rapid development of\b",
    ),
)

HYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("unsupported_sota_language", r"\bstate-of-the-art\b"),
    ("salesy_novel_language", r"\b(?:novel|groundbreaking|revolutionary)\b"),
    ("unsupported_significant_language", r"\bsignificant(?:ly)? improves?\b"),
)

SECTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "introduction": ("introduction",),
    "related_work": ("related work", "background", "prior work"),
    "method": ("method", "approach", "model", "system", "skillguard"),
    "experiments": ("experiments", "evaluation", "experimental setup", "results"),
    "limitations": ("limitations", "limitations and broader impact", "discussion"),
}


class AcademicLanguageReviewError(RuntimeError):
    """Raised when an academic-language review cannot be generated."""


def generate_academic_language_review(
    project_root: Path,
    *,
    review_mode: str = "model",
    threshold: float = MIN_ACADEMIC_LANGUAGE_SCORE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    iteration: int | None = None,
    write: bool = True,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review paper prose and optionally persist review artifacts."""

    root = Path(project_root)
    threshold = max(float(threshold), MIN_ACADEMIC_LANGUAGE_SCORE)
    iteration = iteration or _next_iteration(root)
    source_paths, missing_sources = collect_latex_source_paths(root)
    source_snapshots = [
        {"path": rel_path, "sha256": _sha256_file(root / rel_path)}
        for rel_path in source_paths
        if (root / rel_path).is_file()
    ]
    source_text_by_path = _read_source_texts(root, source_paths)
    combined_source = _combined_source_text(source_text_by_path)

    issues: list[dict[str, Any]] = []
    if not (root / PAPER_MAIN_TEX_PATH).is_file():
        issues.append(
            _issue(
                "missing_main_tex",
                "blocking",
                "paper/main.tex is missing; draft the paper before academic-language review",
                hard_gate=True,
                action="rewrite_introduction",
            )
        )
    for rel_path in missing_sources:
        issues.append(
            _issue(
                "missing_latex_source",
                "blocking",
                f"referenced LaTeX source {rel_path} is missing",
                hard_gate=True,
                action="rewrite_introduction",
                target=rel_path,
            )
        )

    deterministic = _deterministic_assessment(combined_source)
    issues.extend(deterministic["issues"])
    section_scores = dict(deterministic["section_scores"])
    required_checks = dict(deterministic["required_checks"])
    score = float(deterministic["score_1_to_5"])
    review_method = "heuristic_only"
    model_review: dict[str, Any] | None = None
    evidence_spans: list[dict[str, Any]] = []

    if review_mode == "model":
        try:
            model_review = _run_model_review(
                root=root,
                source_text_by_path=source_text_by_path,
                deterministic=deterministic,
                threshold=threshold,
                env=env,
                timeout=timeout,
            )
        except (ImageToolError, AcademicLanguageReviewError) as exc:
            issues.append(
                _issue(
                    "model_review_unavailable",
                    "blocking",
                    f"text reviewer could not score paper prose: {_redact(str(exc))}",
                    hard_gate=True,
                    action="rewrite_introduction",
                )
            )
        else:
            review_method = "hybrid_llm_heuristic"
            score = _merge_model_review(
                model_review=model_review,
                section_scores=section_scores,
                required_checks=required_checks,
                evidence_spans=evidence_spans,
                issues=issues,
                fallback_score=score,
            )
    elif review_mode != "heuristic":
        raise AcademicLanguageReviewError(f"unsupported review_mode {review_mode!r}")

    for key in SECTION_SCORE_KEYS:
        section_score = _float_or_none(section_scores.get(key))
        if section_score is None:
            issues.append(
                _issue(
                    "missing_section_score",
                    "blocking",
                    f"academic-language review must score section {key!r}",
                    hard_gate=True,
                    action="rewrite_introduction",
                )
            )
        elif section_score < threshold:
            issues.append(
                _issue(
                    "low_section_score",
                    "major",
                    f"section score {key}={section_score:g} is below {threshold:g}",
                    hard_gate=True,
                    action=_section_action(key),
                )
            )

    missing_checks = [key for key in REQUIRED_CHECK_KEYS if required_checks.get(key) is not True]
    for key in missing_checks:
        issues.append(
            _issue(
                "failed_academic_required_check",
                "major",
                f"required academic-language check {key!r} is not satisfied",
                hard_gate=True,
                action=_check_action(key),
            )
        )

    hard_issues = [issue for issue in issues if issue.get("severity") == "blocking" or issue.get("hard_gate")]
    blocking_issues = [issue for issue in issues if issue.get("severity") == "blocking"]
    score = _final_score(score, section_scores)
    needs_revision = bool(hard_issues) or score < threshold
    verdict = "PASS"
    if blocking_issues:
        verdict = "BLOCKED"
    elif needs_revision:
        verdict = "FAIL"

    directives = [] if verdict == "PASS" else _revision_directives(issues, model_review)
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "argus_skill.skills.academic_language_review",
        "created_at": datetime.now(UTC).isoformat(),
        "iteration": iteration,
        "review_method": review_method,
        "verdict": verdict,
        "score_1_to_5": score,
        "threshold": threshold,
        "needs_revision": needs_revision,
        "source_snapshots": source_snapshots,
        "reviewed_source_count": len(source_snapshots),
        "section_scores": _round_scores(section_scores),
        "required_checks": required_checks,
        "evidence_spans": evidence_spans,
        "issues": issues,
        "blocking_issues": blocking_issues,
        "revision_directives": directives,
        "review_policy": {
            "rubric": "emnlp-academic-language-v1",
            "pass_requires_model": True,
            "min_evidence_spans": len(SECTION_SCORE_KEYS),
            "allowed_directive_actions": sorted(ALLOWED_DIRECTIVE_ACTIONS),
            "adapted_from": "AI-Research-SKILLs MIT workflow concepts, no exemplar prose copied",
        },
    }
    if model_review is not None:
        result["model_review"] = model_review

    if write:
        _write_json(root / ACADEMIC_LANGUAGE_REVIEW_JSON_PATH, result)
        _write_text(root / ACADEMIC_LANGUAGE_REVIEW_MD_PATH, _review_markdown(result))
        _append_history(root, ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH, result)
    return result


def collect_latex_source_paths(
    root: Path,
    *,
    main_path: Path = PAPER_MAIN_TEX_PATH,
) -> tuple[list[str], list[str]]:
    """Return current LaTeX source files and missing transitive references."""

    root_resolved = Path(root).resolve()
    main_rel = main_path.as_posix()
    pending = [main_rel]
    seen: set[str] = set()
    ordered: list[str] = []
    missing: list[str] = []
    while pending and len(seen) < MAX_SOURCE_FILES:
        rel_path = pending.pop(0)
        if rel_path in seen:
            continue
        seen.add(rel_path)
        resolved = _safe_project_path(root_resolved, rel_path)
        if resolved is None or not resolved.is_file():
            missing.append(rel_path)
            continue
        ordered.append(rel_path)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        for child in _latex_child_paths(text, current_rel=rel_path):
            if child not in seen:
                pending.append(child)
    return ordered, missing


def _latex_child_paths(text: str, *, current_rel: str) -> list[str]:
    current_parent = Path(current_rel).parent
    stripped = _strip_latex_comments(text)
    children: list[str] = []
    for match in re.finditer(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}", stripped):
        raw = match.group(1).strip()
        if not raw or raw.startswith(("/", "\\")):
            continue
        child = Path(raw)
        if child.suffix == "":
            child = child.with_suffix(".tex")
        children.append((current_parent / child).as_posix())
    for command in ("bibliography", "addbibresource"):
        for match in re.finditer(rf"\\{command}\s*\{{([^{{}}]+)\}}", stripped):
            for raw_part in match.group(1).split(","):
                raw = raw_part.strip()
                if not raw or raw.startswith(("/", "\\")):
                    continue
                child = Path(raw)
                if child.suffix == "":
                    child = child.with_suffix(".bib")
                children.append((current_parent / child).as_posix())
    return [_normalize_rel_path(path) for path in children if _normalize_rel_path(path) is not None]


def _safe_project_path(root_resolved: Path, rel_path: str) -> Path | None:
    normalized = _normalize_rel_path(rel_path)
    if normalized is None:
        return None
    resolved = (root_resolved / normalized).resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def _normalize_rel_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "\\" in text:
        return None
    path = Path(text)
    if path.is_absolute():
        return None
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return Path(*parts).as_posix()


def _read_source_texts(root: Path, source_paths: Sequence[str]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for rel_path in source_paths:
        path = root / rel_path
        if path.is_file() and path.suffix == ".tex":
            texts[rel_path] = path.read_text(encoding="utf-8", errors="replace")
    return texts


def _combined_source_text(source_text_by_path: Mapping[str, str]) -> str:
    chunks: list[str] = []
    for rel_path, text in source_text_by_path.items():
        chunks.append(f"\n%% --- SOURCE: {rel_path} ---\n{text}")
    return "\n".join(chunks)


def _deterministic_assessment(tex_text: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    plain = _latex_to_plain_text(tex_text)
    abstract = _extract_environment(tex_text, "abstract")
    abstract_plain = _latex_to_plain_text(abstract)
    section_titles = _extract_section_titles(tex_text)
    opening = _opening_text(plain)
    score_penalty = 0.0
    section_scores = {key: 5.0 for key in SECTION_SCORE_KEYS}
    required_checks = {key: True for key in REQUIRED_CHECK_KEYS}

    if not abstract_plain.strip():
        score_penalty += 1.0
        section_scores["abstract"] = 2.0
        required_checks["five_sentence_abstract_or_equivalent"] = False
        issues.append(
            _issue(
                "missing_abstract",
                "major",
                "paper needs a concise abstract with problem, gap, method, evidence, and so-what",
                hard_gate=True,
                action="rewrite_abstract",
            )
        )
    else:
        sentence_count = _sentence_count(abstract_plain)
        if sentence_count < 4 or sentence_count > 6:
            score_penalty += 0.6
            section_scores["abstract"] = min(section_scores["abstract"], 3.5)
            required_checks["five_sentence_abstract_or_equivalent"] = False
            issues.append(
                _issue(
                    "weak_abstract_shape",
                    "major",
                    f"abstract has {sentence_count} sentence(s); target five evidence-backed sentences",
                    hard_gate=True,
                    action="rewrite_abstract",
                )
            )

    for section_key, synonyms in SECTION_SYNONYMS.items():
        if _has_section(section_titles, synonyms):
            continue
        score_penalty += 0.5
        if section_key == "introduction":
            section_scores["introduction"] = min(section_scores["introduction"], 3.0)
            required_checks["clear_problem_gap_contribution"] = False
        elif section_key == "related_work":
            section_scores["related_work_positioning"] = min(
                section_scores["related_work_positioning"], 3.0
            )
            required_checks["related_work_methodological"] = False
        elif section_key == "limitations":
            required_checks["limitations_scope_present"] = False
        issues.append(
            _issue(
                "missing_expected_section",
                "major",
                f"paper is missing an expected {section_key.replace('_', ' ')} section",
                hard_gate=section_key in {"introduction", "experiments", "limitations"},
                action=_missing_section_action(section_key),
            )
        )

    contribution_context = " ".join([abstract_plain, _section_text(tex_text, "introduction")])
    contribution_lower = contribution_context.lower()
    if (
        "we propose" not in contribution_lower
        or "we show" not in contribution_lower
        or "improv" not in contribution_lower
        or not any(character.isdigit() for character in contribution_context)
    ):
        score_penalty += 0.8
        section_scores["contribution_framing"] = min(section_scores["contribution_framing"], 3.0)
        required_checks["clear_problem_gap_contribution"] = False
        issues.append(
            _issue(
                "missing_evidence_backed_contribution_sentence",
                "major",
                "paper needs 'We propose X. We show X improves Y by Z because W.' framing",
                hard_gate=True,
                action="tighten_contribution_sentence",
            )
        )

    for code, pattern in GENERIC_OPENING_PATTERNS:
        if re.search(pattern, opening, re.I):
            score_penalty += 0.6
            section_scores["introduction"] = min(section_scores["introduction"], 3.5)
            section_scores["style_and_clarity"] = min(section_scores["style_and_clarity"], 3.5)
            required_checks["clear_problem_gap_contribution"] = False
            issues.append(
                _issue(
                    code,
                    "major",
                    "opening uses generic template prose instead of problem-specific framing",
                    hard_gate=True,
                    action="rewrite_introduction",
                )
            )

    source_without_comments = _strip_latex_comments(tex_text)
    for code, pattern in HYPE_PATTERNS:
        matches = list(re.finditer(pattern, source_without_comments, re.I))
        if not matches:
            continue
        score_penalty += min(0.8, 0.25 + len(matches) * 0.15)
        section_scores["style_and_clarity"] = min(section_scores["style_and_clarity"], 3.5)
        required_checks["calibrated_no_hype"] = False
        issues.append(
            _issue(
                code,
                "major" if len(matches) > 1 else "minor",
                f"paper uses hype/superlative language {len(matches)} time(s); calibrate claims",
                hard_gate=len(matches) > 1,
                action="replace_hype_language",
            )
        )

    placeholder_count = len(re.findall(r"\b(?:TODO|TBD|placeholder)\b", source_without_comments))
    if placeholder_count:
        score_penalty += 1.0
        section_scores["style_and_clarity"] = min(section_scores["style_and_clarity"], 2.0)
        issues.append(
            _issue(
                "placeholder_text_remaining",
                "blocking",
                "paper still contains TODO/TBD/placeholder text",
                hard_gate=True,
                action="delete_filler",
            )
        )

    code_labels = _find_display_code_labels(tex_text)
    if code_labels:
        score_penalty += 0.6
        section_scores["style_and_clarity"] = min(section_scores["style_and_clarity"], 3.4)
        issues.append(
            _issue(
                "code_like_display_label",
                "major",
                f"display prose contains code-like labels: {', '.join(sorted(code_labels)[:4])}",
                hard_gate=True,
                action="rename_code_like_label",
            )
        )

    if not _has_quantified_claim(plain):
        score_penalty += 0.7
        section_scores["evidence_alignment"] = min(section_scores["evidence_alignment"], 3.2)
        required_checks["evidence_aligned_claims"] = False
        issues.append(
            _issue(
                "missing_quantified_evidence_claim",
                "major",
                "paper needs at least one quantified result tied to the headline claim",
                hard_gate=True,
                action="add_evidence_sentence",
            )
        )

    score = max(1.0, 5.0 - score_penalty)
    return {
        "score_1_to_5": round(score, 2),
        "section_scores": _round_scores(section_scores),
        "required_checks": required_checks,
        "issues": issues,
    }


def _run_model_review(
    *,
    root: Path,
    source_text_by_path: Mapping[str, str],
    deterministic: dict[str, Any],
    threshold: float,
    env: Mapping[str, str] | None,
    timeout: float,
) -> dict[str, Any]:
    route = _require_route("reviewer", env)
    prompt = _review_prompt(
        source_text_by_path=source_text_by_path,
        deterministic=deterministic,
        threshold=threshold,
    )
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
        raise AcademicLanguageReviewError("reviewer model returned no text")
    parsed = _parse_json_object_from_text(raw_text)
    parsed["raw_review_text"] = raw_text
    parsed["model"] = route.model
    parsed["endpoint"] = endpoint
    parsed["reviewed_root"] = str(root)
    return parsed


def _review_prompt(
    *,
    source_text_by_path: Mapping[str, str],
    deterministic: dict[str, Any],
    threshold: float,
) -> str:
    numbered_source = _numbered_source_excerpt(source_text_by_path, limit=26000)
    return (
        "You are the final academic-language reviewer for an EMNLP long paper. "
        "Reject papers that read like generic agent output: template LLM openings, "
        "unsupported hype, vague claims, weak contribution framing, experiment dumps "
        "without a What/Why/So-What story, ungrouped related work, or claims not tied "
        "to evidence. Use a strict ACL/EMNLP reviewer standard. Return strict JSON only "
        "with keys: score_1_to_5 (number), section_scores object containing exactly "
        f"{list(SECTION_SCORE_KEYS)}, required_checks object containing exactly "
        f"{list(REQUIRED_CHECK_KEYS)}, evidence_spans list with at least one entry for "
        "each section score (source_path, line, quote, why, section), blocking_issues "
        "list, major_issues list, revision_directives list with action/target/rationale/"
        "expected_effect, and pass_or_revise as pass or revise. A score below "
        f"{threshold:g}, any missing evidence span, or any unsupported headline claim "
        "means revise. Quote source text verbatim in evidence_spans.\n\n"
        f"Deterministic signals:\n{json.dumps(deterministic, ensure_ascii=False)[:7000]}\n\n"
        f"Numbered LaTeX sources:\n{numbered_source}"
    )


def _merge_model_review(
    *,
    model_review: dict[str, Any],
    section_scores: dict[str, float],
    required_checks: dict[str, bool],
    evidence_spans: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    fallback_score: float,
) -> float:
    score = _float_or_none(model_review.get("score_1_to_5"))
    if score is None:
        issues.append(
            _issue(
                "model_review_missing_score",
                "blocking",
                "reviewer model did not return score_1_to_5",
                hard_gate=True,
                action="rewrite_introduction",
            )
        )
        score = fallback_score

    raw_section_scores = model_review.get("section_scores")
    if not isinstance(raw_section_scores, dict):
        issues.append(
            _issue(
                "model_review_missing_section_scores",
                "blocking",
                "reviewer model did not return section_scores",
                hard_gate=True,
                action="rewrite_introduction",
            )
        )
    else:
        for key in SECTION_SCORE_KEYS:
            model_score = _float_or_none(raw_section_scores.get(key))
            if model_score is None:
                issues.append(
                    _issue(
                        "model_review_missing_section_score",
                        "blocking",
                        f"reviewer model did not score {key}",
                        hard_gate=True,
                        action=_section_action(key),
                    )
                )
            else:
                section_scores[key] = min(
                    float(section_scores.get(key, 5.0)),
                    max(1.0, min(5.0, model_score)),
                )

    raw_checks = model_review.get("required_checks")
    if not isinstance(raw_checks, dict):
        issues.append(
            _issue(
                "model_review_missing_required_checks",
                "blocking",
                "reviewer model did not return required_checks",
                hard_gate=True,
                action="rewrite_introduction",
            )
        )
    else:
        for key in REQUIRED_CHECK_KEYS:
            required_checks[key] = required_checks.get(key) is True and raw_checks.get(key) is True

    raw_spans = model_review.get("evidence_spans")
    if not isinstance(raw_spans, list) or not raw_spans:
        issues.append(
            _issue(
                "model_review_missing_evidence_spans",
                "blocking",
                "reviewer model must quote evidence spans from the reviewed source",
                hard_gate=True,
                action="rewrite_introduction",
            )
        )
    else:
        for raw_span in raw_spans:
            if isinstance(raw_span, dict):
                evidence_spans.append(dict(raw_span))

    issues.extend(_model_issues(model_review))
    return max(1.0, min(5.0, score))


def _model_issues(model_review: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for field, severity in (("blocking_issues", "blocking"), ("major_issues", "major")):
        raw_items = model_review.get(field)
        if not isinstance(raw_items, list):
            continue
        for index, item in enumerate(raw_items):
            if isinstance(item, dict):
                text = str(item.get("issue") or item.get("description") or item.get("rationale") or "").strip()
                action = _normalize_action(item.get("action")) or "calibrate_claim"
                target = str(item.get("target") or "paper/main.tex")
            else:
                text = str(item).strip()
                action = "calibrate_claim"
                target = "paper/main.tex"
            if text:
                issues.append(
                    _issue(
                        f"model_{field}_{index}",
                        severity,
                        text,
                        hard_gate=True,
                        action=action,
                        target=target,
                    )
                )
    return issues


def _revision_directives(
    issues: list[dict[str, Any]],
    model_review: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    directives: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(action: str, target: str, rationale: str, expected_effect: str | None = None) -> None:
        key = (action, target)
        if key in seen:
            return
        seen.add(key)
        directives.append(
            {
                "action": action,
                "target": target,
                "rationale": rationale,
                "expected_effect": expected_effect or _expected_effect(action),
            }
        )

    if model_review is not None and isinstance(model_review.get("revision_directives"), list):
        for raw_directive in model_review["revision_directives"]:
            if not isinstance(raw_directive, dict):
                continue
            action = _normalize_action(raw_directive.get("action"))
            if action is None:
                continue
            target = str(raw_directive.get("target") or "paper/main.tex")
            rationale = str(raw_directive.get("rationale") or "").strip()
            expected = str(raw_directive.get("expected_effect") or "").strip()
            add(action, target, rationale or "address model-identified prose issue", expected or None)

    for issue in issues:
        if issue.get("severity") == "minor" and not issue.get("hard_gate"):
            continue
        action = _normalize_action(issue.get("action")) or "calibrate_claim"
        target = str(issue.get("target") or "paper/main.tex")
        add(action, target, str(issue.get("message") or "revise academic prose"))
    return directives


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    hard_gate: bool = False,
    action: str = "calibrate_claim",
    target: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
        "action": _normalize_action(action) or "calibrate_claim",
    }
    if hard_gate:
        issue["hard_gate"] = True
    if target:
        issue["target"] = target
    return issue


def _normalize_action(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return normalized if normalized in ALLOWED_DIRECTIVE_ACTIONS else None


def _expected_effect(action: str) -> str:
    effects = {
        "rewrite_abstract": "state problem, gap, method, result, and implication clearly",
        "rewrite_introduction": "replace generic setup with problem-specific motivation",
        "tighten_contribution_sentence": "make the positive contribution and mechanism explicit",
        "calibrate_claim": "align claims with measured evidence and uncertainty",
        "add_evidence_sentence": "tie the headline claim to a concrete result artifact",
        "reorganize_related_work": "group prior work by method and gap rather than chronology",
        "replace_hype_language": "remove salesy or unsupported superlative prose",
        "delete_filler": "remove low-information prose that weakens the paper",
        "clarify_method_mechanism": "explain why the method changes the measured outcome",
        "rewrite_caption_takeaway": "make figure/table captions carry the main result",
        "add_limitation_scope": "state scope limits without undermining the supported claim",
        "rename_code_like_label": "use reviewable human-readable labels instead of raw identifiers",
    }
    return effects.get(action, "improve academic language quality")


def _section_action(key: str) -> str:
    return {
        "abstract": "rewrite_abstract",
        "introduction": "rewrite_introduction",
        "contribution_framing": "tighten_contribution_sentence",
        "evidence_alignment": "add_evidence_sentence",
        "related_work_positioning": "reorganize_related_work",
        "style_and_clarity": "delete_filler",
    }.get(key, "calibrate_claim")


def _check_action(key: str) -> str:
    return {
        "clear_problem_gap_contribution": "tighten_contribution_sentence",
        "evidence_aligned_claims": "add_evidence_sentence",
        "five_sentence_abstract_or_equivalent": "rewrite_abstract",
        "related_work_methodological": "reorganize_related_work",
        "calibrated_no_hype": "replace_hype_language",
        "limitations_scope_present": "add_limitation_scope",
    }.get(key, "calibrate_claim")


def _missing_section_action(key: str) -> str:
    return {
        "introduction": "rewrite_introduction",
        "related_work": "reorganize_related_work",
        "method": "clarify_method_mechanism",
        "experiments": "add_evidence_sentence",
        "limitations": "add_limitation_scope",
    }.get(key, "calibrate_claim")


def _final_score(score: float, section_scores: Mapping[str, object]) -> float:
    candidates = [max(1.0, min(5.0, score))]
    for key in SECTION_SCORE_KEYS:
        section_score = _float_or_none(section_scores.get(key))
        if section_score is not None:
            candidates.append(section_score)
    return round(max(1.0, min(5.0, min(candidates))), 2)


def _round_scores(scores: Mapping[str, object]) -> dict[str, float]:
    rounded: dict[str, float] = {}
    for key, value in scores.items():
        score = _float_or_none(value)
        if score is not None:
            rounded[str(key)] = round(max(1.0, min(5.0, score)), 2)
    return rounded


def _strip_latex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        escaped = False
        out: list[str] = []
        for char in line:
            if char == "%" and not escaped:
                break
            out.append(char)
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        lines.append("".join(out))
    return "\n".join(lines)


def _latex_to_plain_text(text: str) -> str:
    stripped = _strip_latex_comments(text)
    stripped = re.sub(r"\\cite(?:[a-zA-Z]*)?(?:\[[^\]]*\])*\{[^{}]*\}", " citation ", stripped)
    stripped = re.sub(r"\\(?:ref|label|url|href)(?:\[[^\]]*\])?\{[^{}]*\}", " ", stripped)
    stripped = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", stripped)
    stripped = stripped.replace("{", " ").replace("}", " ")
    stripped = re.sub(r"\s+", " ", stripped)
    return stripped.strip()


def _extract_environment(text: str, name: str) -> str:
    match = re.search(
        rf"\\begin\s*\{{\s*{re.escape(name)}\s*\}}(.*?)\\end\s*\{{\s*{re.escape(name)}\s*\}}",
        text,
        re.S,
    )
    return match.group(1) if match is not None else ""


def _extract_section_titles(text: str) -> list[str]:
    titles: list[str] = []
    stripped = _strip_latex_comments(text)
    pattern = re.compile(r"\\section\*?(?:\[[^\]]*\])?\s*\{")
    for match in pattern.finditer(stripped):
        title = _balanced_brace_content(stripped, match.end() - 1)
        if title:
            titles.append(_normalize_title(_latex_to_plain_text(title)))
    return titles


def _has_section(section_titles: Sequence[str], synonyms: Sequence[str]) -> bool:
    normalized = {_normalize_title(title) for title in section_titles}
    for synonym in synonyms:
        needle = _normalize_title(synonym)
        if any(needle in title or title in needle for title in normalized):
            return True
    return False


def _section_text(text: str, title: str) -> str:
    stripped = _strip_latex_comments(text)
    pattern = re.compile(r"\\section\*?(?:\[[^\]]*\])?\s*\{")
    matches = list(pattern.finditer(stripped))
    for index, match in enumerate(matches):
        raw_title = _balanced_brace_content(stripped, match.end() - 1)
        if raw_title is None or _normalize_title(title) not in _normalize_title(raw_title):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(stripped)
        return _latex_to_plain_text(stripped[start:end])
    return ""


def _balanced_brace_content(text: str, opening_brace: int) -> str | None:
    if opening_brace >= len(text) or text[opening_brace] != "{":
        return None
    depth = 0
    chunks: list[str] = []
    index = opening_brace
    while index < len(text):
        char = text[index]
        if char == "\\" and depth > 0 and index + 1 < len(text):
            chunks.append(text[index : index + 2])
            index += 2
            continue
        if char == "{":
            depth += 1
            if depth > 1:
                chunks.append(char)
        elif char == "}":
            depth -= 1
            if depth == 0:
                return "".join(chunks)
            chunks.append(char)
        elif depth > 0:
            chunks.append(char)
        index += 1
    return None


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()])


def _opening_text(plain: str) -> str:
    return plain[:1800]


def _find_display_code_labels(tex_text: str) -> set[str]:
    contexts = [tex_text]
    labels: set[str] = set()
    for context in contexts:
        for match in re.finditer(r"\\texttt\s*\{([^{}]*(?:_|\\_)[^{}]*)\}", context):
            labels.add(match.group(1).strip())
        for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9]*(?:\\_)[A-Za-z0-9][A-Za-z0-9\\_]*\b", context):
            labels.add(match.group(0).strip())
    return {label for label in labels if label}


def _has_quantified_claim(plain: str) -> bool:
    return bool(
        re.search(
            r"\b(?:improv\w*|increase\w*|reduce\w*|outperform\w*|beat\w*)\b.{0,80}"
            r"(?:\d+(?:\.\d+)?\s*(?:%|points?|pp)|p\s*[<=>]\s*0?\.\d+)",
            plain,
            re.I,
        )
    )


def _numbered_source_excerpt(
    source_text_by_path: Mapping[str, str],
    *,
    limit: int,
) -> str:
    lines: list[str] = []
    total = 0
    for rel_path, text in source_text_by_path.items():
        header = f"--- {rel_path} ---"
        lines.append(header)
        total += len(header) + 1
        for line_no, line in enumerate(text.splitlines(), start=1):
            rendered = f"{rel_path}:L{line_no}: {line}"
            if total + len(rendered) + 1 > limit:
                lines.append("[truncated]")
                return "\n".join(lines)
            lines.append(rendered)
            total += len(rendered) + 1
    return "\n".join(lines)


def _parse_json_object_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.S)
        if match is None:
            raise AcademicLanguageReviewError("review text did not contain a JSON object")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AcademicLanguageReviewError(f"review JSON was invalid: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise AcademicLanguageReviewError("review JSON must be an object")
    return value


def _review_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Academic Language Review",
        "",
        f"- Verdict: `{result['verdict']}`",
        f"- Score: `{result['score_1_to_5']}` / 5 (threshold `{result['threshold']}`)",
        f"- Review method: `{result['review_method']}`",
        f"- Needs revision: `{result['needs_revision']}`",
        "",
    ]
    issues = result.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["## Issues", ""])
        for issue in issues:
            if not isinstance(issue, dict):
                continue
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


def _next_iteration(root: Path) -> int:
    history = root / ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH
    if not history.is_file():
        return 1
    try:
        lines = [line for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return 1
    return len(lines) + 1


def _append_history(root: Path, path: Path, result: dict[str, Any]) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "created_at": result.get("created_at"),
        "iteration": result.get("iteration"),
        "verdict": result.get("verdict"),
        "score_1_to_5": result.get("score_1_to_5"),
        "needs_revision": result.get("needs_revision"),
        "source_sha256": {
            entry.get("path"): entry.get("sha256")
            for entry in result.get("source_snapshots", [])
            if isinstance(entry, dict)
        },
        "issue_codes": [
            issue.get("code")
            for issue in result.get("issues", [])
            if isinstance(issue, dict) and issue.get("code")
        ],
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, sort_keys=True) + "\n")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    os.replace(tmp, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.skills.academic_language_review",
        description="Score final EMNLP paper academic language and narrative quality.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--review-mode", choices=("model", "heuristic"), default="model")
    parser.add_argument("--threshold", type=float, default=MIN_ACADEMIC_LANGUAGE_SCORE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--iteration", type=int)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write paper/ACADEMIC_LANGUAGE_REVIEW.json and .md",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = generate_academic_language_review(
            args.project_root,
            review_mode=args.review_mode,
            threshold=args.threshold,
            timeout=args.timeout,
            iteration=args.iteration,
            write=bool(args.write),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus-skill academic-language-review: {_redact(str(exc))}\n")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
