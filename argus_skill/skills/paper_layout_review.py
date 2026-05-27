"""Generate final paper layout/aesthetic review artifacts."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus_skill.tools.image_tool import (
    ApiError,
    ImageToolError,
    _data_url,
    _json_request,
    _parse_chat_text,
    _parse_responses_text,
    _redact,
    _require_route,
)

from ._review_contract_constants import (
    LAYOUT_REVIEW_GENERATED_BY,
    LAYOUT_REVIEW_HISTORY_PATH,
    REVIEW_INPUT_SHA256_FIELD,
    REVIEW_PROMPT_SHA256_FIELD,
    review_sha256_file,
    review_sha256_json,
    review_sha256_text,
)

PAPER_MAIN_PDF_PATH = Path("paper/main.pdf")
PAPER_MAIN_TEX_PATH = Path("paper/main.tex")
PAPER_MAIN_LOG_PATH = Path("paper/main.log")
LAYOUT_REVIEW_JSON_PATH = Path("paper/LAYOUT_REVIEW.json")
LAYOUT_REVIEW_MD_PATH = Path("paper/LAYOUT_REVIEW.md")
LAYOUT_REVIEW_PAGE_DIR = Path("paper/layout_review/pages")
MIN_LAYOUT_SCORE = 4.0
MAX_DEFAULT_PAGES = 32
DEFAULT_DPI = 120
DEFAULT_TIMEOUT_SECONDS = 500.0
MAX_RESEARCH_MD_OVERFULL_HBOX_PT = 5.0

ALLOWED_DIRECTIVE_ACTIONS = {
    "shorten_section",
    "expand_evidence_content",
    "split_table",
    "merge_tables",
    "move_float",
    "resize_figure",
    "regenerate_figure",
    "replace_code_label",
    "tighten_paragraph",
    "delete_low_value_content",
    "rebalance_columns",
    "fix_overfull_box",
    "fix_bibliography_appendix_order",
    "fix_reference_boundary",
}

MAX_BODY_FIGURES = 5
MAX_BODY_WIDE_FIGURES = 1


class LayoutReviewError(RuntimeError):
    """Raised when a layout review artifact cannot be generated."""


def generate_layout_review(
    project_root: Path,
    *,
    review_mode: str = "vision",
    threshold: float = MIN_LAYOUT_SCORE,
    max_pages: int = MAX_DEFAULT_PAGES,
    dpi: int = DEFAULT_DPI,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    iteration: int | None = None,
    write: bool = True,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review the compiled paper layout and optionally persist review artifacts."""

    root = Path(project_root)
    threshold = max(float(threshold), MIN_LAYOUT_SCORE)
    iteration = iteration or _next_iteration(root)
    issues: list[dict[str, Any]] = []
    pdf_path = root / PAPER_MAIN_PDF_PATH
    tex_path = root / PAPER_MAIN_TEX_PATH
    log_path = root / PAPER_MAIN_LOG_PATH
    page_snapshots: list[dict[str, Any]] = []
    layout_text = ""
    pdf_sha256 = ""
    render_error = ""

    if not pdf_path.is_file():
        issues.append(
            _issue(
                "missing_compiled_pdf",
                "blocking",
                "paper/main.pdf is missing; compile the paper before layout review",
                action="rebalance_columns",
            )
        )
    else:
        pdf_sha256 = review_sha256_file(pdf_path)
        try:
            page_snapshots = _render_pdf_pages(
                root,
                pdf_path,
                max_pages=max_pages,
                dpi=dpi,
                timeout=timeout,
            )
        except LayoutReviewError as exc:
            render_error = str(exc)
            issues.append(
                _issue(
                    "pdf_render_failed",
                    "blocking",
                    f"could not render paper pages for visual review: {exc}",
                    action="rebalance_columns",
                )
            )
        layout_text = _extract_pdf_layout_text(pdf_path, timeout=timeout)

    tex_text = tex_path.read_text(encoding="utf-8", errors="replace") if tex_path.is_file() else ""
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""

    deterministic = _deterministic_assessment(
        tex_text=tex_text,
        log_text=log_text,
        layout_text=layout_text,
        threshold=threshold,
    )
    issues.extend(deterministic["issues"])
    criteria_scores = dict(deterministic["criteria_scores"])
    score = float(deterministic["score_1_to_5"])
    review_method = "heuristic_only"
    vision_review: dict[str, Any] | None = None

    if review_mode == "vision":
        if not page_snapshots:
            issues.append(
                _issue(
                    "missing_page_snapshots",
                    "blocking",
                    "vision layout review requires rendered page snapshots",
                    action="rebalance_columns",
                )
            )
        else:
            try:
                vision_review = _run_vision_review(
                    page_snapshots=page_snapshots,
                    root=root,
                    deterministic=deterministic,
                    threshold=threshold,
                    env=env,
                    timeout=timeout,
                )
            except (ImageToolError, LayoutReviewError) as exc:
                issues.append(
                    _issue(
                        "vision_review_unavailable",
                        "blocking",
                        f"vision model could not score the rendered PDF pages: {_redact(str(exc))}",
                        action="rebalance_columns",
                    )
                )
            else:
                review_method = "hybrid_vision_heuristic"
                vision_score = _float_or_none(vision_review.get("score_1_to_5"))
                if vision_score is None:
                    issues.append(
                        _issue(
                            "vision_review_missing_score",
                            "blocking",
                            "vision review did not return score_1_to_5",
                            action="rebalance_columns",
                        )
                    )
                else:
                    score = min(score, max(1.0, min(5.0, vision_score)))
                criteria_scores.update(_criterion_scores(vision_review.get("criteria_scores")))
                issues.extend(_vision_issues(vision_review))
    elif review_mode != "heuristic":
        raise LayoutReviewError(f"unsupported review_mode {review_mode!r}")

    score = max(1.0, min(5.0, round(score, 2)))
    hard_issues = [issue for issue in issues if issue.get("severity") == "blocking" or issue.get("hard_gate")]
    blocking_issues = [issue for issue in issues if issue.get("severity") == "blocking"]
    needs_revision = bool(hard_issues) or score < threshold
    verdict = "PASS"
    if any(issue.get("severity") == "blocking" for issue in issues):
        verdict = "BLOCKED"
    elif needs_revision:
        verdict = "FAIL"

    directives = _revision_directives(issues)
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": LAYOUT_REVIEW_GENERATED_BY,
        "created_at": datetime.now(UTC).isoformat(),
        "iteration": iteration,
        "review_method": review_method,
        "verdict": verdict,
        "score_1_to_5": score,
        "threshold": threshold,
        "needs_revision": needs_revision,
        "pdf_path": str(PAPER_MAIN_PDF_PATH),
        "pdf_sha256": pdf_sha256,
        "page_snapshots": page_snapshots,
        "render_error": render_error,
        "layout_text_extracted": bool(layout_text.strip()),
        "criteria_scores": criteria_scores,
        "issues": issues,
        "blocking_issues": blocking_issues,
        "revision_directives": directives,
        "review_policy": {
            "pass_requires_vision": True,
            "max_recommended_iterations": 3,
            "allowed_directive_actions": sorted(ALLOWED_DIRECTIVE_ACTIONS),
        },
    }
    if vision_review is not None:
        result["vision_review"] = vision_review

    if write:
        _write_json(root / LAYOUT_REVIEW_JSON_PATH, result)
        _write_text(root / LAYOUT_REVIEW_MD_PATH, _layout_review_markdown(result))
        _append_history(root, LAYOUT_REVIEW_HISTORY_PATH, result)
    return result


def _render_pdf_pages(
    root: Path,
    pdf_path: Path,
    *,
    max_pages: int,
    dpi: int,
    timeout: float,
) -> list[dict[str, Any]]:
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise LayoutReviewError("pdftoppm is not installed")

    output_dir = root / LAYOUT_REVIEW_PAGE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_page in output_dir.glob("page-*.png"):
        old_page.unlink()

    prefix = output_dir / "page"
    command = [
        pdftoppm,
        "-png",
        "-r",
        str(int(dpi)),
        "-f",
        "1",
        "-l",
        str(max(1, int(max_pages))),
        str(pdf_path),
        str(prefix),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise LayoutReviewError(stderr[:500] or f"pdftoppm exited {completed.returncode}")

    snapshots: list[dict[str, Any]] = []
    for index, path in enumerate(sorted(output_dir.glob("page-*.png")), start=1):
        snapshots.append(
            {
                "page": index,
                "path": path.relative_to(root).as_posix(),
                "sha256": review_sha256_file(path),
            }
        )
    if not snapshots:
        raise LayoutReviewError("pdftoppm produced no page images")
    return snapshots


def _extract_pdf_layout_text(pdf_path: Path, *, timeout: float) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return ""
    completed = subprocess.run(
        [pdftotext, "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout


def _deterministic_assessment(
    *,
    tex_text: str,
    log_text: str,
    layout_text: str,
    threshold: float,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    penalty = 0.0
    overfulls = [
        float(match.group(1))
        for match in re.finditer(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", log_text)
    ]
    severe = [amount for amount in overfulls if amount > MAX_RESEARCH_MD_OVERFULL_HBOX_PT]
    if severe:
        penalty += 1.2
        issues.append(
            _issue(
                "severe_overfull_hbox",
                "major",
                (
                    f"LaTeX log reports overfull boxes up to {max(severe):.1f}pt; "
                    f"research.md requires no Overfull \\hbox > "
                    f"{MAX_RESEARCH_MD_OVERFULL_HBOX_PT:g}pt"
                ),
                hard_gate=True,
                action="fix_overfull_box",
            )
        )

    if _references_after_appendix(tex_text):
        penalty += 1.0
        issues.append(
            _issue(
                "appendix_before_references",
                "major",
                "references appear after appendix material",
                hard_gate=True,
                action="fix_bibliography_appendix_order",
            )
        )

    body_tex = tex_text.split(r"\appendix", 1)[0]
    body_figures = len(re.findall(r"\\begin\s*\{\s*figure\s*\}", body_tex))
    if body_figures > MAX_BODY_FIGURES:
        penalty += 0.8
        issues.append(
            _issue(
                "too_many_body_figures",
                "major",
                (
                    f"body contains {body_figures} figure environments; research.md limits "
                    f"body figures to {MAX_BODY_FIGURES}"
                ),
                hard_gate=True,
                action="move_float",
            )
        )

    body_wide_figures = len(re.findall(r"\\begin\s*\{\s*figure\*\s*\}", body_tex))
    if body_wide_figures > MAX_BODY_WIDE_FIGURES:
        penalty += 0.8
        issues.append(
            _issue(
                "too_many_wide_figures",
                "major",
                (
                    f"body contains {body_wide_figures} figure* environments; research.md allows "
                    f"only {MAX_BODY_WIDE_FIGURES} full-width body figure"
                ),
                hard_gate=True,
                action="move_float",
            )
        )

    tiny_font_count = len(re.findall(r"\\(?:tiny|scriptsize)\b", tex_text))
    if tiny_font_count:
        penalty += min(0.7, 0.25 + tiny_font_count * 0.1)
        issues.append(
            _issue(
                "tiny_table_or_caption_font",
                "minor" if tiny_font_count <= 2 else "major",
                f"paper uses tiny/scriptsize font {tiny_font_count} time(s); split dense tables instead",
                action="split_table",
            )
        )

    resizebox_count = len(re.findall(r"\\resizebox\s*\{(?:\\columnwidth|\\textwidth|[0-9.]+\\(?:columnwidth|textwidth))\}", tex_text))
    if resizebox_count > 2:
        penalty += 0.4
        issues.append(
            _issue(
                "excessive_resizebox_tables",
                "minor",
                f"paper uses resizebox {resizebox_count} times; avoid unreadably compressed tables",
                action="split_table",
            )
        )

    layout_pages = _layout_pages(layout_text)
    references_page = _first_layout_page_matching(
        layout_pages,
        r"(?m)^\s*(?:References|Bibliography)\s*$",
    )
    if references_page is not None:
        reference_page_text = layout_pages[references_page - 1]
        if re.search(
            r"\b(?:Conclusion|Limitations|Ethical Considerations|Ethics|Release and Reproducibility)\b",
            reference_page_text,
        ):
            penalty += 0.9
            issues.append(
                _issue(
                    "references_share_body_page",
                    "major",
                    (
                        "References begin on the same rendered page as body end matter; "
                        "fix the body/reference boundary without generic shortening"
                    ),
                    page=references_page,
                    hard_gate=True,
                    action="fix_reference_boundary",
                    target=f"page {references_page} References boundary",
                )
            )
        elif references_page < 8:
            penalty += 0.7
            issues.append(
                _issue(
                    "references_before_full_body",
                    "major",
                    (
                        "References begin before the paper visibly fills the long-paper body budget; "
                        "expand from verified evidence instead of padding"
                    ),
                    page=references_page,
                    hard_gate=True,
                    action="expand_evidence_content",
                    target=f"page {references_page} early References",
                )
            )

    page_stats = _layout_page_stats(layout_text)
    for stat in page_stats:
        if stat["table_captions"] >= 4:
            penalty += 1.3
            issues.append(
                _issue(
                    "crowded_table_float_page",
                    "major",
                    f"page {stat['page']} contains {stat['table_captions']} table captions",
                    page=stat["page"],
                    hard_gate=True,
                    action="split_table",
                )
            )
        elif stat["table_captions"] >= 3:
            penalty += 0.8
            issues.append(
                _issue(
                    "dense_table_float_page",
                    "major",
                    f"page {stat['page']} contains {stat['table_captions']} table captions",
                    page=stat["page"],
                    hard_gate=True,
                    action="move_float",
                )
            )
        if stat["float_captions"] >= 5:
            penalty += 0.8
            issues.append(
                _issue(
                    "crowded_float_page",
                    "major",
                    f"page {stat['page']} contains {stat['float_captions']} figure/table captions",
                    page=stat["page"],
                    hard_gate=True,
                    action="move_float",
                )
            )
        if stat["caption_only"] and stat["page"] < max(1, len(page_stats)):
            penalty += 0.7
            issues.append(
                _issue(
                    "caption_only_or_float_dump_page",
                    "major",
                    f"page {stat['page']} is dominated by captions/floats rather than readable prose",
                    page=stat["page"],
                    hard_gate=True,
                    action="move_float",
                )
            )
        if stat["long_lines"] >= 6:
            penalty += 0.3
            issues.append(
                _issue(
                    "many_long_layout_lines",
                    "minor",
                    f"page {stat['page']} has {stat['long_lines']} very long extracted lines",
                    page=stat["page"],
                    action="rebalance_columns",
                )
            )

    score = max(1.0, threshold + 1.0 - penalty)
    criteria_scores = {
        "float_balance": max(1.0, 5.0 - sum(1.0 for issue in issues if "float" in issue["code"])),
        "table_readability": max(1.0, 5.0 - sum(0.8 for issue in issues if "table" in issue["code"])),
        "typography": max(1.0, 5.0 - sum(0.6 for issue in issues if issue["code"] in {"tiny_table_or_caption_font", "severe_overfull_hbox"})),
        "page_flow": max(1.0, 5.0 - sum(0.8 for issue in issues if issue.get("hard_gate"))),
    }
    return {
        "score_1_to_5": round(score, 2),
        "criteria_scores": {key: round(value, 2) for key, value in criteria_scores.items()},
        "issues": issues,
    }


def _layout_page_stats(layout_text: str) -> list[dict[str, Any]]:
    pages = _layout_pages(layout_text)
    stats: list[dict[str, Any]] = []
    for index, page in enumerate(pages, start=1):
        lines = [line.rstrip() for line in page.splitlines() if line.strip()]
        table_captions = len(re.findall(r"\bTable\s+\d+\s*:", page))
        figure_captions = len(re.findall(r"\bFigure\s+\d+\s*:", page))
        body_lines = [
            line
            for line in lines
            if not re.search(r"\b(?:Table|Figure)\s+\d+\s*:", line)
            and not re.fullmatch(r"\s*\d+\s*", line)
        ]
        long_lines = sum(1 for line in lines if len(line) >= 130)
        caption_only = table_captions + figure_captions >= 2 and len(body_lines) < 15
        stats.append(
            {
                "page": index,
                "line_count": len(lines),
                "body_line_count": len(body_lines),
                "table_captions": table_captions,
                "figure_captions": figure_captions,
                "float_captions": table_captions + figure_captions,
                "long_lines": long_lines,
                "caption_only": caption_only,
            }
        )
    return stats


def _layout_pages(layout_text: str) -> list[str]:
    return [page for page in layout_text.split("\f") if page.strip()]


def _first_layout_page_matching(pages: Sequence[str], pattern: str) -> int | None:
    compiled = re.compile(pattern)
    for index, page in enumerate(pages, start=1):
        if compiled.search(page):
            return index
    return None


def _run_vision_review(
    *,
    page_snapshots: list[dict[str, Any]],
    root: Path,
    deterministic: dict[str, Any],
    threshold: float,
    env: Mapping[str, str] | None,
    timeout: float,
) -> dict[str, Any]:
    route = _require_route("image_review", env)
    selected = page_snapshots
    prompt = _vision_prompt(deterministic=deterministic, threshold=threshold)
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": prompt,
        }
    ]
    for snapshot in selected:
        path = root / str(snapshot["path"])
        content.append({"type": "input_image", "image_url": _data_url(path), "detail": "high"})
    payload = {"model": route.model, "input": [{"role": "user", "content": content}]}
    endpoint = "/responses"
    try:
        data = _json_request(route, endpoint, payload, timeout=timeout)
        raw_text = _parse_responses_text(data)
    except ApiError as exc:
        if exc.status not in (400, 404):
            raise
        endpoint = "/chat/completions"
        chat_content: list[dict[str, Any]] = [{"type": "text", "text": content[0]["text"]}]
        for snapshot in selected:
            path = root / str(snapshot["path"])
            chat_content.append({"type": "image_url", "image_url": {"url": _data_url(path), "detail": "high"}})
        data = _json_request(
            route,
            endpoint,
            {"model": route.model, "messages": [{"role": "user", "content": chat_content}]},
            timeout=timeout,
        )
        raw_text = _parse_chat_text(data)
    if not raw_text:
        raise LayoutReviewError("vision model returned no text")
    parsed = _parse_json_object_from_text(raw_text)
    parsed["raw_review_text"] = raw_text
    parsed["model"] = route.model
    parsed["endpoint"] = endpoint
    parsed["reviewed_pages"] = [snapshot["page"] for snapshot in selected]
    prompt_sha256 = review_sha256_text(prompt)
    parsed[REVIEW_PROMPT_SHA256_FIELD] = prompt_sha256
    parsed[REVIEW_INPUT_SHA256_FIELD] = review_sha256_json(
        {
            "prompt_sha256": prompt_sha256,
            "page_snapshots": selected,
            "threshold": threshold,
        }
    )
    return parsed


def _vision_prompt(*, deterministic: dict[str, Any], threshold: float) -> str:
    allowed_actions = ", ".join(sorted(ALLOWED_DIRECTIVE_ACTIONS))
    return (
        "Role: You are an independent visual reviewer for an EMNLP 2026 paper that is being "
        "prepared for submission. Your job is to judge the rendered PDF screenshots as a polished, "
        "standard two-column conference paper: visual beauty, professional layout, readability, "
        "and compliance with EMNLP/ACL paper norms. Do not act as the author and do not excuse "
        "ugly artifacts; be as strict as a proceedings layout reviewer.\n\n"
        "Review task: inspect the screenshots page by page, using the deterministic signals below "
        "as concrete hints. Penalize any page that looks non-submission-ready: large blank lower-page "
        "regions, float-dump pages, cramped or plain audit-style tables, table/body overlap, tiny "
        "unreadable fonts, awkward two-column imbalance, captions detached from content, weak page "
        "flow, square or low-quality figures, non-human code-like labels, snake_case labels, heavy "
        "gradients, photorealism, or visuals that look like debug artifacts rather than EMNLP paper "
        "figures. A page with only a couple of small tables and a large empty area is a hard visual "
        "failure even if LaTeX compiles.\n\n"
        "Make the feedback concrete for the next engineer/tool call: every blocking or major issue "
        "must name the page number when visible, the visual target (for example: page 6 lower half, "
        "Table 3, Figure 1 labels, references page), the visual evidence you saw, and the specific "
        "source-level action needed. Prefer fixes that rewrite/rebalance manuscript flow, merge or "
        "remove low-value floats, split unreadable tables, or regenerate poor figures; do not suggest "
        "cosmetic page-break shuffling when the real defect is weak prose/float integration.\n\n"
        "Complete improvement guidance is mandatory, not optional. For every blocking or major issue, "
        "provide enough repair guidance that an engineer can act without re-interpreting the screenshot: "
        "root_cause, source_targets (LaTeX/generator/table/figure files or section names to edit), "
        "specific_edits (ordered concrete edits, not vague advice), visual_goal, and verification "
        "steps after recompilation. The guidance must say whether to delete filler, merge/split/move "
        "specific floats, rewrite nearby prose, regenerate a figure, or change table styling. If the "
        "page is ugly because the paper is underfilled or padded with audit-like content, say exactly "
        "which body section should be expanded with evidence-bearing narrative and which low-value "
        "artifact/table should move to appendix or be deleted.\n\n"
        "Reference boundary guidance: if References or Bibliography starts on the same rendered page as "
        "Conclusion, Limitations, Ethics, or release/reproducibility body text, do not automatically call "
        "the body overlong and do not ask for generic section shortening. Determine the direction from "
        "the page: if the body is visibly underfilled or References start before the final body page is "
        "full, require evidence-backed body expansion, a meaningful late visual anchor, or a clean "
        "reference-page break; if body content actually runs past page 8, then require trimming. "
        "Shortening an underfilled body makes the early-References defect worse.\n\n"
        "Submission contract to enforce: conclusion by page 8, Limitations/Ethics after conclusion, "
        "References before Appendix, no Overfull hbox above 5pt, <=5 body figures, at most one "
        "full-width figure*, at least one meaningful figure/table anchor on each of pages 4-7, table "
        "captions with numerical headlines, readable research-style tables, adaptive/landscape "
        "conceptual figures rather than 1024x1024 squares, and no weird fonts, tiny labels, heavy "
        "gradients, photorealism, or code-like labels in paper-facing visuals.\n\n"
        "Return strict JSON only, no markdown. Use this schema: score_1_to_5 (number), "
        "criteria_scores object with typography/table_readability/float_balance/page_flow/"
        "figure_quality/submission_standardness, blocking_issues list, major_issues list, "
        "revision_directives list, and pass_or_revise as pass or revise. Each blocking_issues and "
        "major_issues item must be an object with issue, page, target, visual_evidence, action, and "
        "guidance. The guidance object must include root_cause, source_targets, specific_edits, "
        "visual_goal, and verification. Each revision_directives item must have action, target, "
        "rationale, expected_effect, and implementation_guidance with the same concrete fields. "
        f"Allowed action values: {allowed_actions}. A score below {threshold:g} or any major "
        "visual defect means revise.\n\n"
        f"Deterministic layout signals:\n{json.dumps(deterministic, ensure_ascii=False)[:6000]}"
    )


def _vision_issues(vision_review: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for field, severity in (("blocking_issues", "major"), ("major_issues", "major")):
        raw_items = vision_review.get(field)
        if not isinstance(raw_items, list):
            continue
        for index, item in enumerate(raw_items):
            if isinstance(item, dict):
                text = str(item.get("issue") or item.get("description") or item.get("rationale") or "").strip()
                action = _normalize_action(item.get("action")) or "rebalance_columns"
                page = _int_or_none(item.get("page"))
                target = str(item.get("target") or "").strip() or None
                visual_evidence = str(item.get("visual_evidence") or "").strip() or None
                guidance = _guidance_from_vision_item(item)
            else:
                text = str(item).strip()
                action = "rebalance_columns"
                page = None
                target = None
                visual_evidence = None
                guidance = None
            if text:
                issue = _issue(
                    f"vision_{field}_{index}",
                    severity,
                    text,
                    page=page,
                    hard_gate=True,
                    action=action,
                    target=target,
                )
                if visual_evidence:
                    issue["visual_evidence"] = visual_evidence
                if guidance:
                    issue["guidance"] = guidance
                issues.append(issue)
    return issues


def _revision_directives(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    directives: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        if issue.get("severity") == "minor" and not issue.get("hard_gate"):
            continue
        action = _normalize_action(issue.get("action")) or "rebalance_columns"
        target = str(issue.get("target") or issue.get("page") or "paper/main.tex")
        key = (action, target)
        if key in seen:
            continue
        seen.add(key)
        directives.append(
            {
                "action": action,
                "target": target,
                "rationale": issue["message"],
                "expected_effect": _expected_effect(action),
                "implementation_guidance": _implementation_guidance(
                    issue=issue,
                    action=action,
                    target=target,
                ),
            }
        )
    return directives


def _guidance_from_vision_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_guidance = item.get("guidance")
    guidance = raw_guidance if isinstance(raw_guidance, Mapping) else {}
    root_cause = _first_text(
        guidance.get("root_cause"),
        guidance.get("why_it_matters"),
        item.get("root_cause"),
        item.get("visual_evidence"),
    )
    source_targets = _text_list(
        guidance.get("source_targets"),
        guidance.get("latex_targets"),
        item.get("source_targets"),
        item.get("latex_targets"),
    )
    specific_edits = _text_list(
        guidance.get("specific_edits"),
        guidance.get("concrete_edits"),
        guidance.get("repair_steps"),
        item.get("specific_edits"),
        item.get("concrete_edits"),
        item.get("repair_steps"),
    )
    visual_goal = _first_text(
        guidance.get("visual_goal"),
        guidance.get("expected_visual_result"),
        item.get("visual_goal"),
    )
    verification = _text_list(
        guidance.get("verification"),
        guidance.get("verification_steps"),
        item.get("verification"),
        item.get("verification_steps"),
    )

    parsed: dict[str, Any] = {}
    if root_cause:
        parsed["root_cause"] = root_cause
    if source_targets:
        parsed["source_targets"] = source_targets
    if specific_edits:
        parsed["specific_edits"] = specific_edits
    if visual_goal:
        parsed["visual_goal"] = visual_goal
    if verification:
        parsed["verification"] = verification
    return parsed or None


def _implementation_guidance(
    *,
    issue: Mapping[str, Any],
    action: str,
    target: str,
) -> dict[str, Any]:
    raw_guidance = issue.get("guidance")
    guidance: Mapping[str, Any] = raw_guidance if isinstance(raw_guidance, Mapping) else {}
    root_cause = _first_text(
        guidance.get("root_cause"),
        issue.get("visual_evidence"),
        issue.get("message"),
    ) or "The rendered PDF page does not meet EMNLP/ACL visual submission standards."
    source_targets = _text_list(guidance.get("source_targets"))
    if not source_targets:
        source_targets = _default_source_targets(target)
    specific_edits = _text_list(guidance.get("specific_edits"))
    if not specific_edits:
        specific_edits = [_default_specific_edit(action, target)]
    visual_goal = _first_text(guidance.get("visual_goal")) or _expected_effect(action)
    verification = _text_list(guidance.get("verification"))
    if not verification:
        verification = [
            "Rebuild paper/main.pdf, rerun paper_layout_review in vision mode, and ensure validate-layout-review passes."
        ]
    return {
        "root_cause": root_cause,
        "source_targets": source_targets,
        "specific_edits": specific_edits,
        "visual_goal": visual_goal,
        "verification": verification,
    }


def _default_source_targets(target: str) -> list[str]:
    normalized = target.strip()
    if normalized and normalized != "paper/main.tex":
        return ["paper/main.tex", normalized]
    return ["paper/main.tex", "the generator/source file that owns the affected section, table, or figure"]


def _default_specific_edit(action: str, target: str) -> str:
    target_text = target or "the affected page/object"
    edits = {
        "shorten_section": f"Rewrite or trim low-value prose around {target_text}; keep only evidence-bearing narrative and move audit detail to appendix.",
        "expand_evidence_content": f"Expand the underfilled body around {target_text} with verified analysis, ablations, failure cases, or robustness evidence; do not pad with generic prose.",
        "split_table": f"Split the dense table at {target_text} into smaller reader-facing tables or move secondary rows to appendix.",
        "merge_tables": f"Merge redundant low-density tables around {target_text} into one stronger reader-facing table with a numerical takeaway caption.",
        "move_float": f"Move the float around {target_text} next to the paragraph that discusses it, or rewrite the nearby prose/float order so the page is not a float dump.",
        "resize_figure": f"Resize or recrop the figure at {target_text} so labels remain readable and columns stay balanced.",
        "regenerate_figure": f"Regenerate the figure at {target_text} with a cleaner EMNLP-style layout, readable labels, and no debug/code-facing visual artifacts.",
        "replace_code_label": f"Replace code-like labels around {target_text} with human-readable paper labels in the figure/table source and caption.",
        "tighten_paragraph": f"Tighten paragraphs around {target_text} without adding unsupported claims; use the freed space to restore balanced page flow.",
        "delete_low_value_content": f"Delete or move low-value audit/checklist content around {target_text}; replace body space only with exemplar-aligned evidence narrative if needed.",
        "rebalance_columns": f"Rebalance text and floats around {target_text} by editing source order, paragraph length, and float placement rather than adding filler.",
        "fix_overfull_box": f"Fix the source line/table/figure causing overflow at {target_text}; do not hide it with unreadably small fonts.",
        "fix_bibliography_appendix_order": f"Move References before Appendix and keep Limitations/Ethics after Conclusion around {target_text}.",
        "fix_reference_boundary": f"Separate References from body text at {target_text}; if the body is underfilled, add evidence-backed body content or a meaningful late visual anchor before using a clean reference break.",
    }
    return edits.get(action, f"Revise {target_text} so the rendered page has polished EMNLP/ACL layout.")


def _first_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _text_list(*values: object) -> list[str]:
    items: list[str] = []
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                items.append(text)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for entry in value:
                if isinstance(entry, str):
                    text = entry.strip()
                    if text:
                        items.append(text)
    return items


def _expected_effect(action: str) -> str:
    effects = {
        "shorten_section": "reduce overflow and restore balanced page flow",
        "expand_evidence_content": "fill the long-paper body budget with verified evidence",
        "split_table": "replace dense unreadable tables with smaller reviewable tables",
        "merge_tables": "reduce float clutter by combining redundant tables",
        "move_float": "avoid float-only pages and attach captions to nearby prose",
        "resize_figure": "fit figures cleanly without crowding text",
        "regenerate_figure": "replace low-quality visual material",
        "replace_code_label": "use human-readable paper labels",
        "tighten_paragraph": "free space without adding unsupported claims",
        "delete_low_value_content": "remove filler that damages layout",
        "rebalance_columns": "improve visual balance across columns/pages",
        "fix_overfull_box": "remove visible text/table overflow",
        "fix_bibliography_appendix_order": "restore ACL/EMNLP section order",
        "fix_reference_boundary": "keep references on a clean page after the body",
    }
    return effects.get(action, "improve final paper layout")


def _criterion_scores(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    scores: dict[str, float] = {}
    for key, raw in value.items():
        score = _float_or_none(raw)
        if score is not None:
            scores[str(key)] = max(1.0, min(5.0, round(score, 2)))
    return scores


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    page: int | None = None,
    hard_gate: bool = False,
    action: str = "rebalance_columns",
    target: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
        "action": _normalize_action(action) or "rebalance_columns",
    }
    if page is not None:
        issue["page"] = page
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


def _references_after_appendix(tex_text: str) -> bool:
    appendix = re.search(r"\\appendix\b", tex_text)
    bibliography = re.search(
        r"\\(?:bibliography\s*\{|printbibliography\b|begin\s*\{\s*thebibliography\s*\})",
        tex_text,
    )
    return appendix is not None and bibliography is not None and appendix.start() < bibliography.start()


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
            raise LayoutReviewError("vision review did not contain a JSON object")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LayoutReviewError(f"vision review JSON was invalid: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise LayoutReviewError("vision review JSON must be an object")
    return value


def _layout_review_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Layout Review",
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
            page = f" page {issue['page']}:" if "page" in issue else ""
            lines.append(f"- `{issue.get('severity', 'unknown')}`{page} {issue.get('message', '')}")
        lines.append("")
    directives = result.get("revision_directives")
    if isinstance(directives, list) and directives:
        lines.extend(["## Revision directives", ""])
        for directive in directives:
            if not isinstance(directive, dict):
                continue
            lines.append(
                f"- `{directive.get('action', 'revise')}` on `{directive.get('target', 'paper/main.tex')}`: "
                f"{directive.get('rationale', '')}"
            )
            guidance = directive.get("implementation_guidance")
            if isinstance(guidance, dict):
                root_cause = guidance.get("root_cause")
                visual_goal = guidance.get("visual_goal")
                source_targets = guidance.get("source_targets")
                specific_edits = guidance.get("specific_edits")
                verification = guidance.get("verification")
                if root_cause:
                    lines.append(f"  - Root cause: {root_cause}")
                if isinstance(source_targets, list) and source_targets:
                    lines.append("  - Source targets: " + "; ".join(str(item) for item in source_targets))
                if isinstance(specific_edits, list) and specific_edits:
                    lines.append("  - Specific edits: " + "; ".join(str(item) for item in specific_edits))
                if visual_goal:
                    lines.append(f"  - Visual goal: {visual_goal}")
                if isinstance(verification, list) and verification:
                    lines.append("  - Verification: " + "; ".join(str(item) for item in verification))
        lines.append("")
    return "\n".join(lines)


def _next_iteration(root: Path) -> int:
    history = root / LAYOUT_REVIEW_HISTORY_PATH
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
        "pdf_sha256": result.get("pdf_sha256"),
        "vision_model": (result.get("vision_review") or {}).get("model")
        if isinstance(result.get("vision_review"), dict)
        else None,
        "vision_endpoint": (result.get("vision_review") or {}).get("endpoint")
        if isinstance(result.get("vision_review"), dict)
        else None,
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


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.skills.paper_layout_review",
        description="Render and score final paper layout aesthetics.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--review-mode", choices=("vision", "heuristic"), default="vision")
    parser.add_argument("--threshold", type=float, default=MIN_LAYOUT_SCORE)
    parser.add_argument("--max-pages", type=int, default=MAX_DEFAULT_PAGES)
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--iteration", type=int)
    parser.add_argument("--write", action="store_true", help="write paper/LAYOUT_REVIEW.json and .md")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = generate_layout_review(
            args.project_root,
            review_mode=args.review_mode,
            threshold=args.threshold,
            max_pages=args.max_pages,
            dpi=args.dpi,
            timeout=args.timeout,
            iteration=args.iteration,
            write=bool(args.write),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus-skill paper-layout-review: {_redact(str(exc))}\n")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
