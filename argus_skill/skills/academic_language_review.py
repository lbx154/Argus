"""Generate final academic-language review artifacts for EMNLP papers."""
from __future__ import annotations

import argparse
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

from ._review_contract_constants import (
    ACADEMIC_LANGUAGE_REVIEW_GENERATED_BY,
    ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH,
    REVIEW_INPUT_SHA256_FIELD,
    REVIEW_PROMPT_SHA256_FIELD,
    review_sha256_file,
    review_sha256_json,
    review_sha256_text,
)

PAPER_MAIN_TEX_PATH = Path("paper/main.tex")
ACADEMIC_LANGUAGE_REVIEW_JSON_PATH = Path("paper/ACADEMIC_LANGUAGE_REVIEW.json")
ACADEMIC_LANGUAGE_REVIEW_MD_PATH = Path("paper/ACADEMIC_LANGUAGE_REVIEW.md")
MIN_ACADEMIC_LANGUAGE_SCORE = 4.0
DEFAULT_TIMEOUT_SECONDS = 500.0
MAX_SOURCE_FILES = 120
MIN_REVIEW_ABSTRACT_WORDS = 170
MIN_REVIEW_INTRODUCTION_WORDS = 900
MIN_INTRODUCTION_CITATION_KEYS = 3

SECTION_SCORE_KEYS: tuple[str, ...] = (
    "abstract",
    "introduction",
    "contribution_framing",
    "evidence_alignment",
    "related_work_positioning",
    "method_system_clarity",
    "style_and_clarity",
)

REQUIRED_CHECK_KEYS: tuple[str, ...] = (
    "clear_problem_gap_contribution",
    "evidence_aligned_claims",
    "five_sentence_abstract_or_equivalent",
    "related_work_methodological",
    "method_system_readable",
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

FORMULAIC_PROSE_LIMITS: tuple[tuple[str, str, int, str], ...] = (
    (
        "contrastive_template_overuse",
        r"\b(?:rather than|instead of|not\s+(?:only\s+)?(?:a\s+)?[a-z][a-z-]*"
        r"(?:\s+[a-z][a-z-]*){0,4}\s+but|not about|not as)\b",
        10,
        (
            "paper overuses contrastive template prose; rewrite paragraphs into "
            "direct scientific exposition instead of repeated not-X-but-Y framing"
        ),
    ),
    (
        "over_defensive_scope_caveats",
        r"\b(?:narrow|benchmark[- ]scoped|scope boundary|does not establish|"
        r"not proof|not a general|only shows|limited to|slice-specific|"
        r"not causal)\b",
        8,
        (
            "paper overuses scope caveats; keep one calibrated scope statement "
            "near the claim and move the rest to limitations"
        ),
    ),
    (
        "stock_transition_overuse",
        r"\b(?:that distinction matters|that matters because|put differently|"
        r"the headline result|the gap is that|the result is therefore)\b",
        4,
        (
            "paper repeats stock transition templates; replace them with "
            "section-specific motivation, mechanism, and evidence sentences"
        ),
    ),
)

INTRODUCTION_RESULT_PREVIEW_PATTERN = (
    r"\b(?:achiev\w*|reach\w*|solv\w*|outperform\w*|improv\w*|increase\w*|"
    r"reduce\w*|recover\w*|yield\w*|win\w*|success|accuracy|score)\b"
    r".{0,80}(?:\d+(?:\.\d+)?\s*(?:%|points?|pp)|\d+\s*/\s*\d+)"
    r"|(?:\d+(?:\.\d+)?\s*(?:%|points?|pp)|\d+\s*/\s*\d+)"
    r".{0,80}\b(?:success|accuracy|score|improv\w*|outperform\w*)\b"
)

INTRODUCTION_ROADMAP_PATTERN = (
    r"\b(?:we|this paper|our)\s+"
    r"(?:make|offer|propose|introduce|present|evaluate|study|show|report)\b|"
    r"\b(?:contribution|contributions|we show|we find)\b"
)

ABSTRACT_READER_HOSTILE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "abstract_references_layout_artifact",
        r"(?:\\(?:ref|autoref|cref)\s*\{|\b(?:appendix|supplement(?:ary)?)\s+"
        r"(?:figure|table|section)\b|\b(?:figure|table)\s*\d+[a-z]?\b)",
        "abstract should not refer to appendix/figure/table layout artifacts; it must stand alone",
    ),
    (
        "abstract_mentions_internal_review_artifact",
        r"\b(?:validator|validation gate|review gate|academic[- ]language review|"
        r"evidence span|revision directive|source snapshot|artifact manifest|"
        r"result_to_claim|paper quality calibration)\b|"
        r"(?:paper|experiments|results|bench|research)/[A-Za-z0-9_.\-/]+",
        "abstract contains validator/artifact vocabulary instead of reader-facing paper prose",
    ),
)

ABSTRACT_CAVEAT_PATTERNS: tuple[str, ...] = (
    r"\bcontrolled\b",
    r"\bsynthetic\b",
    r"\bbenchmark[- ]scoped\b",
    r"\bnot\s+(?:a\s+)?causal\b",
    r"\bnot\s+proof\b",
    r"\bdoes\s+not\s+establish\b",
    r"\blimited\s+to\b",
    r"\bonly\s+shows\b",
    r"\bcaveat\b",
    r"\blimitation\b",
)

ABSTRACT_PROBLEM_TERMS: tuple[str, ...] = (
    "challenge",
    "failure",
    "fails",
    "gap",
    "bottleneck",
    "limitation",
    "open question",
    "unclear",
    "difficulty",
    "difficult",
    "benchmark gap",
    "evaluation gap",
)

ABSTRACT_RESULT_FIRST_PATTERN = (
    r"\b(?:achiev\w*|beat\w*|outperform\w*|improv\w*|increase\w*|reduce\w*|"
    r"score\w*|raise\w*|recover\w*|yield\w*|win\w*)\b|"
    r"\b\d+(?:\.\d+)?\s*(?:%|points?|pp)?\b|"
    r"\b\d+\s*/\s*\d+\b|p\s*[<=>]\s*0?\.\d+"
)

SECTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "introduction": ("introduction",),
    "related_work": ("related work", "background", "prior work"),
    "method": ("method", "approach", "model", "system", "skillguard"),
    "experiments": ("experiments", "evaluation", "experimental setup", "results"),
    "limitations": ("limitations", "limitations and broader impact", "discussion"),
}

EVALUATED_SYSTEM_DETAIL_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "missing_method_framework_or_runtime",
        r"\b(?:agent framework|framework|runtime|harness|benchmark driver|"
        r"evaluation suite|simulator|execution environment|controller|"
        r"orchestrator|policy engine|python\s+\d|implementation)\b",
        (
            "method/setup must name the evaluated system framework, runtime, "
            "harness, or controller, not the paper-generation infrastructure"
        ),
    ),
    (
        "missing_method_agent_mechanism",
        r"\b(?:agent|planner|skill|memory|retrieval|tool|verifier|reflection|"
        r"policy|routing|controller|state|admission|promotion|gate)\b",
        "method/setup must explain the agent mechanism rather than only reporting scores",
    ),
    (
        "missing_method_evaluation_protocol",
        r"\b(?:baseline|benchmark|task|episode|trial|metric|budget|temperature|token|"
        r"cost|scored|run)\b",
        "method/setup must give enough evaluation protocol detail to interpret the results",
    ),
)

MODEL_IDENTIFIER_PATTERN = (
    r"\b(?:gpt[-_ ]?\d(?:[\w.\-:]*)?|o\d(?:[\w.\-:]*)?|claude[-_ ]?\d(?:[\w.\-:]*)?|"
    r"gemini[-_ ]?\d(?:[\w.\-:]*)?|llama[-_ ]?\d(?:[\w.\-:]*)?|qwen[-_ ]?\d(?:[\w.\-:]*)?|"
    r"mistral(?:[\w.\-:]*)?|deepseek(?:[\w.\-:]*)?)\b"
)

MODEL_USE_CONTEXT_PATTERN = (
    r"\b(?:llm|large language model|language model|prompt(?:ed|ing)?|"
    r"temperature|decoding|token budget|model route|model call|api call|"
    r"openai|anthropic|gemini|claude|gpt[-_ ]?\d|llama[-_ ]?\d|qwen[-_ ]?\d)\b"
)

NO_EXTERNAL_MODEL_PATTERN = (
    r"\b(?:no|without|does not|do not|never)\s+(?:call|use|invoke|query|run)\s+"
    r"(?:an?\s+)?(?:external\s+)?(?:llm|large language model|language model|model|api)\b|"
    r"\bbenchmark loop itself does not call an external llm\b|"
    r"\bdeterministic\b.{0,100}\b(?:symbolic|no external llm|without external llm)\b"
)

INTERNAL_GENERATION_INFRASTRUCTURE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "mentions_internal_generation_infrastructure",
        r"\b(?:argus(?:[- ]skill)?\s+pipeline|codex\s+engineer|"
        r"engineer\s+routing|reviews?/calibration|planner,\s*engineer,\s*and\s*reviewer|"
        r"academic[- ]language review|paper[-_ ]layout[-_ ]review|layout review tool|"
        r"paper generator|capability[- ]vault|daemon|validation[- ]route|"
        r"argus image tool)\b",
        (
            "method/setup is describing the internal Argus/Codex paper-generation "
            "or review infrastructure; replace it with facts about the evaluated "
            "system, benchmark harness, baselines, metrics, and actual model use"
        ),
    ),
)


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
        {"path": rel_path, "sha256": review_sha256_file(root / rel_path)}
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
        "generated_by": ACADEMIC_LANGUAGE_REVIEW_GENERATED_BY,
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
    normalized_children: list[str] = []
    for path in children:
        normalized = _normalize_rel_path(path)
        if normalized is not None:
            normalized_children.append(normalized)
    return normalized_children


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
        for code, message, penalty, score_cap in _abstract_quality_issue_specs(abstract):
            score_penalty += penalty
            section_scores["abstract"] = min(section_scores["abstract"], score_cap)
            required_checks["five_sentence_abstract_or_equivalent"] = False
            issues.append(
                _issue(
                    code,
                    "major",
                    message,
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

    introduction_plain = _section_text(tex_text, "introduction")
    if introduction_plain.strip():
        introduction_words = _word_count(introduction_plain)
        if introduction_words < MIN_REVIEW_INTRODUCTION_WORDS:
            score_penalty += 0.8
            section_scores["introduction"] = min(section_scores["introduction"], 3.0)
            section_scores["contribution_framing"] = min(
                section_scores["contribution_framing"], 3.4
            )
            required_checks["clear_problem_gap_contribution"] = False
            issues.append(
                _issue(
                    "thin_introduction",
                    "major",
                    (
                        f"introduction has {introduction_words} words; a normal "
                        f"EMNLP long-paper introduction should be at least "
                        f"{MIN_REVIEW_INTRODUCTION_WORDS} words and should explain "
                        "the problem, literature gap, method insight, result preview, "
                        "contributions, and scope"
                    ),
                    hard_gate=True,
                    action="rewrite_introduction",
                )
            )
        for code, message in find_introduction_readability_issues(tex_text):
            score_penalty += 0.6
            section_scores["introduction"] = min(section_scores["introduction"], 3.1)
            section_scores["contribution_framing"] = min(
                section_scores["contribution_framing"], 3.4
            )
            required_checks["clear_problem_gap_contribution"] = False
            issues.append(
                _issue(
                    code,
                    "major",
                    message,
                    hard_gate=True,
                    action="rewrite_introduction",
                )
            )

    contribution_context = " ".join([abstract_plain, _section_text(tex_text, "introduction")])
    if not _has_reader_facing_contribution(contribution_context):
        score_penalty += 0.8
        section_scores["contribution_framing"] = min(section_scores["contribution_framing"], 3.0)
        required_checks["clear_problem_gap_contribution"] = False
        issues.append(
            _issue(
                "missing_evidence_backed_contribution_sentence",
                "major",
                (
                    "paper needs a reader-facing contribution sentence or paragraph that "
                    "names the method, task/context, measured effect, and design lever "
                    "or scoped comparator; name a mechanism only when the current "
                    "ablations isolate it"
                ),
                hard_gate=True,
                action="tighten_contribution_sentence",
            )
        )

    for code, message in find_method_system_readability_issues(tex_text):
        score_penalty += 0.45
        section_scores["method_system_clarity"] = min(
            section_scores["method_system_clarity"], 3.2
        )
        section_scores["style_and_clarity"] = min(section_scores["style_and_clarity"], 3.7)
        required_checks["method_system_readable"] = False
        issues.append(
            _issue(
                code,
                "major",
                message,
                hard_gate=True,
                action="clarify_method_mechanism",
                target="paper/main.tex",
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
        match_spans = _regex_match_spans(source_without_comments, matches)
        match_summary = _match_summary(match_spans)
        score_penalty += min(0.8, 0.25 + len(matches) * 0.15)
        section_scores["style_and_clarity"] = min(section_scores["style_and_clarity"], 3.5)
        required_checks["calibrated_no_hype"] = False
        issues.append(
            _issue(
                code,
                "major" if len(matches) > 1 else "minor",
                (
                    f"paper uses hype/superlative language {len(matches)} time(s); "
                    f"calibrate claims at {match_summary}"
                ),
                hard_gate=len(matches) > 1,
                action="replace_hype_language",
                target=_line_target(match_spans),
                evidence_spans=match_spans,
            )
        )

    for code, message, match_spans, penalty, score_cap in _formulaic_prose_issue_specs(tex_text):
        score_penalty += penalty
        section_scores["style_and_clarity"] = min(section_scores["style_and_clarity"], score_cap)
        section_scores["introduction"] = min(section_scores["introduction"], 3.4)
        required_checks["calibrated_no_hype"] = False
        issues.append(
            _issue(
                code,
                "major",
                f"{message}; matches include {_match_summary(match_spans)}",
                hard_gate=True,
                action="delete_filler",
                target=_line_target(match_spans),
                evidence_spans=match_spans,
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
        raise AcademicLanguageReviewError("reviewer model returned no text")
    parsed = _parse_json_object_from_text(raw_text)
    parsed["raw_review_text"] = raw_text
    parsed["model"] = route.model
    parsed["endpoint"] = endpoint
    parsed["reviewed_root"] = str(root)
    parsed[REVIEW_PROMPT_SHA256_FIELD] = prompt_sha256
    parsed[REVIEW_INPUT_SHA256_FIELD] = review_sha256_json(
        {
            "deterministic": deterministic,
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
        "without a What/Why/So-What story, ungrouped related work, repeated "
        "not-X-but-Y/benchmark-scoped caveats, or claims not tied "
        "to evidence. Evidence spans are reviewer-internal audit artifacts: do not ask "
        "authors to paste source paths, appendix/figure references, validation-gate "
        "vocabulary, or evidence quotes into the abstract to satisfy this review. Reject "
        "papers that leave basic evaluated-system facts implicit: the Method/Experimental "
        "Setup must let a reviewer identify the system under study, its runtime or "
        "benchmark harness, the controller/skill/memory mechanism, baselines, task "
        "source, metrics, and budget. For no-GPU agent experiments, the final paper "
        "should name the approved hosted backbone such as gpt-5-mini plus decoding "
        "and budget settings; a deterministic/no-external-model loop is acceptable "
        "only when the claim is explicitly downgraded to a deterministic baseline or "
        "pilot rather than a final agent-system result. Do not credit or describe the "
        "Argus/Codex daemon, engineer/reviewer routes, academic-language review, layout "
        "review, or image tool used to write this paper as if they were paper-method "
        "components. Treat gpt-5.4, gpt-5.4-mini, and similar orchestration/reviewer "
        "model labels as internal infrastructure names, not publishable evaluated-model "
        "identifiers; hard-block them when they appear in the abstract, method, setup "
        "tables, captions, or results prose. These details should be reader-facing prose or a compact table, "
        "not only comments or JSON artifacts. Reject tables that expose internal "
        "Argus/Codex route labels instead of paper-facing experimental facts. A "
        "professional setup/results table should have explicit Benchmark/Source and "
        "Model/Backend columns, task count/split, method/baseline role, "
        "decoding/budget policy, metric, and the numerical takeaway. Reject final "
        "claims supported by one benchmark family only; same-family variants such as "
        "SWE-bench Verified/Lite/Multimodal do not count as independent sources. If "
        "a table says only 'route', 'component', or an internal role such as "
        "engineer/reviewer, require the manuscript to redesign the table. Reject "
        "a paper whose abstract reads like a validator checklist, starts with a numeric "
        "result before the problem/gap, or spends its scarce space on defensive caveats "
        "instead of problem, method, result, and implication. Apply this ACL/EMNLP "
        f"standard: abstracts under {MIN_REVIEW_ABSTRACT_WORDS} words or introductions "
        f"under {MIN_REVIEW_INTRODUCTION_WORDS} words are not submission-quality for "
        "a long paper unless the manuscript has an explicit venue exception. "
        "Reject an Introduction that has fewer than three separate cited "
        "prior-work/benchmark hooks before Related Work; packing many keys into "
        "one or two citation macros does not create a normal literature-grounded "
        "opening. A normal EMNLP introduction should use citations to establish "
        "the gap, then explain the method insight, quantified evidence preview, "
        "and contribution roadmap in natural prose. Also reject introductions "
        "fragmented into many 50--80 word validator-shaped paragraphs or repeated "
        "stock starts such as 'The gap', 'The result', 'That framing', and "
        "'Put differently'; merge them into substantive paper paragraphs with "
        "specific topic sentences. Reject stale-evidence "
        "prose where method/control names sit next to result ratios that appear "
        "carried over from an older run, or where one section claims no external "
        "LLM/model calls while another reports a hosted/model-backed baseline. "
        "Short introductions should be fixed by adding source-backed problem framing, "
        "literature gap, method intuition, contribution, and evidence preview, not by "
        "deleting content elsewhere. Do not require an isolated causal mechanism when the paper "
        "explicitly scopes itself as an end-to-end policy or comparator result; in that "
        "case, evaluate whether the comparator, task slice, sample size, and quantified "
        "outcome are stated plainly and whether unresolved submechanisms are moved to "
        "analysis or limitations. Return strict JSON only "
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
    evidence_spans: list[dict[str, Any]] | None = None,
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
    if evidence_spans:
        issue["evidence_spans"] = evidence_spans
    return issue


def _regex_match_spans(
    text: str,
    matches: Sequence[re.Match[str]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for match in matches[:limit]:
        line_no = text.count("\n", 0, match.start()) + 1
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end == -1:
            line_end = len(text)
        line = " ".join(text[line_start:line_end].strip().split())
        spans.append(
            {
                "source_path": "paper/main.tex",
                "line": line_no,
                "term": match.group(0),
                "quote": line[:260],
            }
        )
    return spans


def _match_summary(spans: Sequence[Mapping[str, Any]]) -> str:
    if not spans:
        return "the reported source matches"
    fragments = [
        f"line {span.get('line')} `{span.get('term')}`"
        for span in spans[:6]
        if span.get("line") is not None and span.get("term")
    ]
    if len(spans) > 6:
        fragments.append(f"{len(spans) - 6} more")
    return ", ".join(fragments) if fragments else "the reported source matches"


def _line_target(spans: Sequence[Mapping[str, Any]]) -> str:
    lines = [str(span.get("line")) for span in spans[:10] if span.get("line") is not None]
    if not lines:
        return "paper/main.tex"
    suffix = f" and {len(spans) - 10} more" if len(spans) > 10 else ""
    return f"paper/main.tex lines {', '.join(lines)}{suffix}"


def _normalize_action(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return normalized if normalized in ALLOWED_DIRECTIVE_ACTIONS else None


def _expected_effect(action: str) -> str:
    effects = {
        "rewrite_abstract": "write a natural reader-facing abstract with problem, gap, method, result, and implication",
        "rewrite_introduction": "replace generic setup with problem-specific motivation",
        "tighten_contribution_sentence": (
            "make the positive contribution, scoped comparator, and measured effect explicit; "
            "name a mechanism only when isolated by evidence"
        ),
        "calibrate_claim": "align claims with measured evidence and uncertainty",
        "add_evidence_sentence": "tie the headline claim to a concrete result artifact",
        "reorganize_related_work": "group prior work by method and gap rather than chronology",
        "replace_hype_language": "remove salesy or unsupported superlative prose",
        "delete_filler": "remove low-information prose that weakens the paper",
        "clarify_method_mechanism": (
            "explain the evaluated system/runtime or harness, applicable model "
            "identifiers, design lever, and measured comparison without implying "
            "unisolated causality"
        ),
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
        "method_system_clarity": "clarify_method_mechanism",
        "style_and_clarity": "delete_filler",
    }.get(key, "calibrate_claim")


def _check_action(key: str) -> str:
    return {
        "clear_problem_gap_contribution": "tighten_contribution_sentence",
        "evidence_aligned_claims": "add_evidence_sentence",
        "five_sentence_abstract_or_equivalent": "rewrite_abstract",
        "related_work_methodological": "reorganize_related_work",
        "method_system_readable": "clarify_method_mechanism",
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


def _raw_section_text(text: str, title: str) -> str:
    stripped = _strip_latex_comments(text)
    pattern = re.compile(r"\\section\*?(?:\[[^\]]*\])?\s*\{")
    matches = list(pattern.finditer(stripped))
    for index, match in enumerate(matches):
        raw_title = _balanced_brace_content(stripped, match.end() - 1)
        if raw_title is None or _normalize_title(title) not in _normalize_title(raw_title):
            continue
        start = match.end() + len(raw_title) + 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(stripped)
        return stripped[start:end]
    return ""


def _citation_keys_from_latex(tex_text: str) -> set[str]:
    keys: set[str] = set()
    for match in re.finditer(r"\\cite(?:[a-zA-Z]*)?(?:\[[^\]]*\])*\{([^{}]+)\}", tex_text):
        for key in match.group(1).split(","):
            normalized = key.strip()
            if normalized:
                keys.add(normalized)
    return keys


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


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z0-9'/-]*", text))


def find_reader_hostile_abstract_issues(tex_text: str) -> list[tuple[str, str]]:
    """Return abstract-quality issues that remain invalid even if review JSON says PASS."""

    abstract = _extract_environment(tex_text, "abstract")
    return [
        (code, message)
        for code, message, _penalty, _cap in _abstract_quality_issue_specs(abstract)
    ]


def find_introduction_readability_issues(tex_text: str) -> list[tuple[str, str]]:
    """Return introduction issues that make a draft read like agent filler."""

    intro_source = _raw_section_text(tex_text, "introduction")
    intro_plain = _latex_to_plain_text(intro_source)
    if not intro_plain.strip():
        return []

    issues: list[tuple[str, str]] = []
    citation_keys = _citation_keys_from_latex(intro_source)
    if len(citation_keys) < MIN_INTRODUCTION_CITATION_KEYS:
        issues.append(
            (
                "introduction_missing_literature_hooks",
                (
                    "Introduction must situate the problem before Related Work with "
                    f"at least {MIN_INTRODUCTION_CITATION_KEYS} verified cited "
                    "prior-work or benchmark hooks; otherwise the opening reads "
                    "like project-local motivation rather than a conference paper"
                ),
            )
        )

    if not re.search(INTRODUCTION_RESULT_PREVIEW_PATTERN, intro_plain, re.I | re.S):
        issues.append(
            (
                "introduction_missing_quantified_result_preview",
                (
                    "Introduction must preview the main empirical result with a "
                    "verified number or effect size before the Results section; "
                    "otherwise reviewers cannot tell what evidence the paper is "
                    "asking them to evaluate"
                ),
            )
        )

    if not re.search(INTRODUCTION_ROADMAP_PATTERN, intro_plain, re.I):
        issues.append(
            (
                "introduction_missing_contribution_roadmap",
                (
                    "Introduction must include a reader-facing contribution roadmap "
                    "that names the method, evaluated setting, main result, and "
                    "scope before the paper moves to Related Work"
                ),
            )
        )
    return issues


def find_formulaic_prose_issues(tex_text: str) -> list[tuple[str, str]]:
    """Return repeated-template prose issues that remain invalid after JSON review."""

    return [
        (code, message)
        for code, message, _spans, _penalty, _score_cap in _formulaic_prose_issue_specs(tex_text)
    ]


def find_method_system_readability_issues(tex_text: str) -> list[tuple[str, str]]:
    """Return method/setup issues that make the paper unreadable to outside reviewers."""

    context = " ".join(
        _section_text(tex_text, title)
        for title in (
            "method",
            "approach",
            "system",
            "implementation",
            "experimental setup",
            "experiments",
            "evaluation",
        )
    )
    if not context.strip():
        context = _latex_to_plain_text(tex_text)
    issues: list[tuple[str, str]] = []
    for code, pattern, message in INTERNAL_GENERATION_INFRASTRUCTURE_PATTERNS:
        if re.search(pattern, context, re.I):
            issues.append((code, message))
    for code, pattern, message in EVALUATED_SYSTEM_DETAIL_PATTERNS:
        if not re.search(pattern, context, re.I):
            issues.append((code, message))
    if (
        re.search(MODEL_USE_CONTEXT_PATTERN, context, re.I)
        and not re.search(MODEL_IDENTIFIER_PATTERN, context, re.I)
        and not re.search(NO_EXTERNAL_MODEL_PATTERN, context, re.I)
    ):
        issues.append(
            (
                "missing_method_model_identifier",
                (
                    "method/setup mentions external model-style execution but does "
                    "not name the evaluated LLM/model identifier or explicitly state "
                    "that the benchmark loop uses no external LLM/model calls"
                ),
            )
        )
    return issues


def _formulaic_prose_issue_specs(
    tex_text: str,
) -> list[tuple[str, str, list[dict[str, Any]], float, float]]:
    source_without_comments = _strip_latex_comments(tex_text)
    issues: list[tuple[str, str, list[dict[str, Any]], float, float]] = []
    for code, pattern, allowed_count, message in FORMULAIC_PROSE_LIMITS:
        matches = list(re.finditer(pattern, source_without_comments, re.I))
        if len(matches) <= allowed_count:
            continue
        match_spans = _regex_match_spans(source_without_comments, matches)
        issues.append(
            (
                code,
                f"{message}; found {len(matches)} matches, above the {allowed_count}-match limit",
                match_spans,
                min(0.9, 0.35 + (len(matches) - allowed_count) * 0.05),
                3.2,
            )
        )
    return issues


def _abstract_quality_issue_specs(abstract: str) -> list[tuple[str, str, float, float]]:
    if not abstract.strip():
        return []

    issues: list[tuple[str, str, float, float]] = []
    abstract_without_comments = _strip_latex_comments(abstract)
    abstract_plain = _latex_to_plain_text(abstract)

    abstract_words = _word_count(abstract_plain)
    if abstract_words < MIN_REVIEW_ABSTRACT_WORDS:
        issues.append(
            (
                "thin_abstract",
                (
                    f"abstract has {abstract_words} words; final EMNLP abstracts "
                    f"should be at least {MIN_REVIEW_ABSTRACT_WORDS} words and cover "
                    "problem, gap, method, model/benchmark, result, and implication"
                ),
                0.6,
                3.5,
            )
        )

    if re.search(r"(?m)%\s*(?:evidence|artifact|validator|review|gate|source)\s*:", abstract, re.I):
        issues.append(
            (
                "abstract_contains_internal_evidence_comment",
                (
                    "abstract environment contains internal evidence/review comments; "
                    "store evidence in audit artifacts or comments outside the abstract"
                ),
                0.6,
                3.2,
            )
        )

    for code, pattern, message in ABSTRACT_READER_HOSTILE_PATTERNS:
        if re.search(pattern, abstract_without_comments, re.I):
            issues.append((code, message, 0.8, 3.0))

    if _abstract_starts_with_result(abstract_plain):
        issues.append(
            (
                "result_first_abstract",
                (
                    "abstract opens with a numeric/result claim before establishing the "
                    "problem or evaluation gap"
                ),
                0.7,
                3.2,
            )
        )

    caveat_hits = [
        pattern
        for pattern in ABSTRACT_CAVEAT_PATTERNS
        if re.search(pattern, abstract_plain, re.I)
    ]
    if len(caveat_hits) >= 2:
        issues.append(
            (
                "over_defensive_abstract",
                (
                    "abstract overuses scope/caveat language; move most limitations to "
                    "discussion and keep the abstract focused on the supported contribution"
                ),
                0.6,
                3.3,
            )
        )

    return issues


def _abstract_starts_with_result(abstract_plain: str) -> bool:
    first = _first_sentence(abstract_plain).lower()
    if not first:
        return False
    has_result_signal = bool(re.search(ABSTRACT_RESULT_FIRST_PATTERN, first, re.I))
    has_problem_signal = any(term in first for term in ABSTRACT_PROBLEM_TERMS)
    return has_result_signal and not has_problem_signal


def _first_sentence(text: str) -> str:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    return parts[0] if parts else ""


def _has_reader_facing_contribution(text: str) -> bool:
    lower = text.lower()
    has_method_or_artifact = bool(
        re.search(
            r"\b(?:we|this paper|our|the paper)\b.{0,100}"
            r"\b(?:propos\w*|introduc\w*|present\w*|develop\w*|study|"
            r"evaluate\w*|report\w*)\b",
            lower,
            re.S,
        )
        or re.search(
            r"\b(?:method|approach|system|framework|protocol|benchmark|skill|agent)\b",
            lower,
        )
    )
    has_measured_effect = bool(
        _has_quantified_claim(text)
        or re.search(
            r"\b(?:show\w*|find\w*|demonstrat\w*|achiev\w*|improv\w*|"
            r"increas\w*|reduc\w*|outperform\w*|beat\w*|recover\w*|"
            r"rais\w*|yield\w*)\b.{0,120}"
            r"(?:\d+(?:\.\d+)?\s*(?:%|points?|pp)?|\d+\s*/\s*\d+|p\s*[<=>]\s*0?\.\d+)",
            lower,
            re.S,
        )
    )
    has_design_or_scope = bool(
        re.search(
            r"\b(?:because|by|via|through|using|with|under|from|against|"
            r"relative to|compared (?:with|to)|end-to-end|policy|comparator|"
            r"baseline|ablation|benchmark|slice|setting)\b",
            lower,
        )
    )
    return has_method_or_artifact and has_measured_effect and has_design_or_scope


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
    text = plain.replace(r"\%", "%")
    quantity = (
        r"(?:\d+(?:\.\d+)?\s*(?:%|points?|pp)|"
        r"\d+\s*/\s*\d+|p\s*[<=>]\s*0?\.\d+)"
    )
    outcome = (
        r"(?:success|completion|pass rate|accuracy|score|solv\w*|"
        r"repair\w*|recover\w*|win rate|verified completion)"
    )
    return bool(
        re.search(
            r"\b(?:improv\w*|increase\w*|reduce\w*|outperform\w*|beat\w*|"
            r"recover\w*|raise\w*|yield\w*)\b.{0,100}" + quantity,
            text,
            re.I | re.S,
        )
        or re.search(
            quantity
            + r".{0,140}\b(?:vs\.?|versus|compared (?:with|to)|relative to|"
            r"against|over)\b.{0,140}"
            + quantity,
            text,
            re.I | re.S,
        )
        or re.search(r"\b" + outcome + r"\b.{0,140}" + quantity, text, re.I | re.S)
        or re.search(quantity + r".{0,140}\b" + outcome + r"\b", text, re.I | re.S)
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
            spans = issue.get("evidence_spans")
            if isinstance(spans, list):
                for span in spans[:6]:
                    if not isinstance(span, dict):
                        continue
                    line = span.get("line")
                    term = span.get("term")
                    quote = str(span.get("quote") or "").strip()
                    if line is None or not term:
                        continue
                    lines.append(f"  - line {line}: `{term}` in \"{quote[:180]}\"")
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
        "generated_by": result.get("generated_by"),
        "iteration": result.get("iteration"),
        "review_method": result.get("review_method"),
        "verdict": result.get("verdict"),
        "score_1_to_5": result.get("score_1_to_5"),
        "needs_revision": result.get("needs_revision"),
        "model": (result.get("model_review") or {}).get("model")
        if isinstance(result.get("model_review"), dict)
        else None,
        "endpoint": (result.get("model_review") or {}).get("endpoint")
        if isinstance(result.get("model_review"), dict)
        else None,
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
