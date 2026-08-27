"""Generate final paper layout/aesthetic review artifacts."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus_skill.tools.image_api import (
    ApiError,
    ImageToolError,
    _data_url,
    _json_request,
    _parse_chat_text,
    _parse_responses_text,
    _redact,
    _require_route,
)

from ...core.manuscript_snapshot import bind_manuscript_snapshot, manuscript_sha256
from ._review_contract_constants import (
    LAYOUT_REVIEW_GENERATED_BY,
    LAYOUT_REVIEW_HISTORY_PATH,
    REVIEW_INPUT_SHA256_FIELD,
    REVIEW_PROMPT_SHA256_FIELD,
    review_sha256_file,
    review_sha256_json,
    review_sha256_text,
)
from .venue_profiles import VenueProfile, resolve_venue_profile

PAPER_MAIN_PDF_PATH = Path("paper/main.pdf")
PAPER_MAIN_TEX_PATH = Path("paper/main.tex")
PAPER_MAIN_LOG_PATH = Path("paper/main.log")
LAYOUT_REVIEW_JSON_PATH = Path("paper/LAYOUT_REVIEW.json")
LAYOUT_REVIEW_MD_PATH = Path("paper/LAYOUT_REVIEW.md")
LAYOUT_REVIEW_PAGE_DIR = Path("paper/layout_review/pages")
MIN_LAYOUT_SCORE = 3.5
DEFAULT_DPI = 120
DEFAULT_TIMEOUT_SECONDS: float | None = None
MAX_RESEARCH_MD_OVERFULL_HBOX_PT = 5.0
LAYOUT_HEADING_LINE_NUMBER_PREFIX = r"(?:\d{1,5}\s+)?"
LAYOUT_TOP_LEVEL_NUMBER_PREFIX = r"(?:(?:\d{1,5}\.?)\s+){0,2}"
LAYOUT_CONCLUSION_HEADING_PATTERN = (
    rf"(?im)(?:^[ \t]*|[ \t]{{6,}}){LAYOUT_TOP_LEVEL_NUMBER_PREFIX}"
    r"Conclusion(?=[ \t]{6,}|[ \t]*$)"
)
LAYOUT_REFERENCES_HEADING_PATTERN = (
    rf"(?im)(?:^[ \t]*|[ \t]{{6,}}){LAYOUT_TOP_LEVEL_NUMBER_PREFIX}"
    r"(?:References|Bibliography)(?=[ \t]{6,}|[ \t]*$)"
)

ALLOWED_DIRECTIVE_ACTIONS = {
    "shorten_section",
    "expand_evidence_content",
    "trim_or_move_content",
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

# Figures whose ROLE is an overview/teaser/pipeline should span both columns
# (``figure*``) on a two-column venue; when one is placed in a single-column
# ``figure`` it reads as cramped. Matched against the graphic filename and the
# figure label only (not caption prose) to avoid false positives.
_WIDE_FIGURE_ROLE_RE = re.compile(
    r"teaser|pipeline|overview|framework|architecture|\bsystem\b", re.IGNORECASE
)


class LayoutReviewError(RuntimeError):
    """Raised when a layout review artifact cannot be generated."""


def generate_layout_review(
    project_root: Path,
    *,
    review_mode: str = "vision",
    threshold: float = MIN_LAYOUT_SCORE,
    max_pages: int | None = None,
    dpi: int = DEFAULT_DPI,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    iteration: int | None = None,
    write: bool = True,
    env: Mapping[str, str] | None = None,
    venue: VenueProfile | None = None,
) -> dict[str, Any]:
    """Review the compiled paper layout and optionally persist review artifacts."""

    root = Path(project_root)
    reviewed_manuscript_sha = manuscript_sha256(root)
    profile = venue or resolve_venue_profile(root)
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

    # The deterministic layout heuristic is ADVISORY context for the vision
    # reviewer only. The harness no longer scores or gates paper layout from it:
    # whether the layout is acceptable (e.g. where the Conclusion sits) is the
    # reviewer agent's call against the stage checklist. We surface neutral page
    # facts and feed the heuristic to the vision model, but emit no quality verdict.
    deterministic = _deterministic_assessment(
        tex_text=tex_text,
        log_text=log_text,
        layout_text=layout_text,
        threshold=threshold,
        venue=profile,
    )
    review_method = "facts_only"
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
                    venue=profile,
                )
            except (ImageToolError, LayoutReviewError) as exc:
                issues.append(
                    _issue(
                        "vision_review_unavailable",
                        "blocking",
                        f"vision model could not review the rendered PDF pages: {_redact(str(exc))}",
                        action="rebalance_columns",
                    )
                )
            else:
                review_method = "vision_advisory"
    elif review_mode != "heuristic":
        raise LayoutReviewError(f"unsupported review_mode {review_mode!r}")

    page_flow = (
        deterministic.get("page_flow_contract", {}) if isinstance(deterministic, dict) else {}
    )
    facts = {
        "page_count": page_flow.get("page_count"),
        "conclusion_page": page_flow.get("conclusion_page"),
        "references_page": page_flow.get("references_page"),
        "layout_text_extracted": bool(layout_text.strip()),
    }
    # ``structural_status`` reflects only whether the tool could produce facts
    # (missing/un-renderable PDF, vision model unavailable). It is NOT a quality
    # verdict.
    blocking_issues = [issue for issue in issues if issue.get("severity") == "blocking"]
    structural_status = "blocked" if blocking_issues else "ok"

    result: dict[str, Any] = {
        "schema_version": 2,
        "generated_by": LAYOUT_REVIEW_GENERATED_BY,
        "created_at": datetime.now(UTC).isoformat(),
        "iteration": iteration,
        "review_method": review_method,
        "decision_authority": "agent_checklist",
        "harness_verdict": None,
        "no_harness_quality_verdict": True,
        "structural_status": structural_status,
        "pdf_path": str(PAPER_MAIN_PDF_PATH),
        "pdf_sha256": pdf_sha256,
        "page_snapshots": page_snapshots,
        "render_error": render_error,
        "facts": facts,
        "issues": issues,
        "blocking_issues": blocking_issues,
        "review_policy": {
            "decision_authority": "reviewer agent decides against the stage checklist; "
            "the harness reports page facts and relays the vision reviewer's advisory "
            "findings, and emits no quality verdict",
            "allowed_directive_actions": sorted(ALLOWED_DIRECTIVE_ACTIONS),
        },
    }
    if vision_review is not None:
        result["vision_review"] = vision_review
    bind_manuscript_snapshot(
        result,
        root,
        recorded_at=result["created_at"],
        sha256=reviewed_manuscript_sha,
    )

    if write:
        _write_json(root / LAYOUT_REVIEW_JSON_PATH, result)
        _write_text(root / LAYOUT_REVIEW_MD_PATH, _layout_review_markdown(result))
        _append_history(root, LAYOUT_REVIEW_HISTORY_PATH, result)
    return result


def _render_pdf_pages(
    root: Path,
    pdf_path: Path,
    *,
    max_pages: int | None,
    dpi: int,
    timeout: float | None,
) -> list[dict[str, Any]]:
    output_dir = root / LAYOUT_REVIEW_PAGE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    render_errors: list[str] = []
    if shutil.which("pdftoppm") is not None:
        try:
            _render_pdf_pages_with_pdftoppm(
                output_dir,
                pdf_path,
                max_pages=max_pages,
                dpi=dpi,
                timeout=timeout,
            )
            snapshots = _collect_page_snapshots(root, output_dir, renderer="pdftoppm")
            if snapshots and not _has_suspicious_blank_pages(root, snapshots):
                return snapshots
            if snapshots:
                render_errors.append("pdftoppm produced blank-looking page images")
        except LayoutReviewError as exc:
            render_errors.append(str(exc))
    else:
        render_errors.append("pdftoppm is not installed")

    if shutil.which("mutool") is not None:
        try:
            _render_pdf_pages_with_mutool(
                output_dir,
                pdf_path,
                max_pages=max_pages,
                dpi=dpi,
                timeout=timeout,
            )
            snapshots = _collect_page_snapshots(root, output_dir, renderer="mutool")
            if snapshots:
                return snapshots
        except LayoutReviewError as exc:
            render_errors.append(str(exc))

    detail = "; ".join(error for error in render_errors if error)
    raise LayoutReviewError(detail or "no PDF renderer produced page images")


def _clear_rendered_pages(output_dir: Path) -> None:
    for old_page in output_dir.glob("page-*.png"):
        old_page.unlink()


def _render_pdf_pages_with_pdftoppm(
    output_dir: Path,
    pdf_path: Path,
    *,
    max_pages: int | None,
    dpi: int,
    timeout: float | None,
) -> None:
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise LayoutReviewError("pdftoppm is not installed")

    _clear_rendered_pages(output_dir)
    command = [pdftoppm, "-png", "-r", str(int(dpi))]
    if max_pages is not None:
        command.extend(["-f", "1", "-l", str(max(1, int(max_pages)))])
    command.extend([str(pdf_path), str(output_dir / "page")])
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


def _render_pdf_pages_with_mutool(
    output_dir: Path,
    pdf_path: Path,
    *,
    max_pages: int | None,
    dpi: int,
    timeout: float | None,
) -> None:
    mutool = shutil.which("mutool")
    if mutool is None:
        raise LayoutReviewError("mutool is not installed")

    _clear_rendered_pages(output_dir)
    command = [
        mutool,
        "draw",
        "-r",
        str(int(dpi)),
        "-F",
        "png",
        "-o",
        str(output_dir / "page-%02d.png"),
        str(pdf_path),
    ]
    if max_pages is not None:
        command.append(f"1-{max(1, int(max_pages))}")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise LayoutReviewError(stderr[:500] or f"mutool exited {completed.returncode}")


def _page_number_from_snapshot(path: Path) -> int:
    """Extract the integer page number from a ``page-<n>.png`` snapshot name.

    pdftoppm zero-pads inconsistently across versions / page counts, so sorting
    the globbed filenames lexicographically can order ``page-10`` before
    ``page-2``. Sort by the parsed number instead.
    """
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def _collect_page_snapshots(root: Path, output_dir: Path, *, renderer: str) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    pages = sorted(output_dir.glob("page-*.png"), key=_page_number_from_snapshot)
    for index, path in enumerate(pages, start=1):
        snapshots.append(
            {
                "page": index,
                "path": path.relative_to(root).as_posix(),
                "sha256": review_sha256_file(path),
                "renderer": renderer,
            }
        )
    if not snapshots:
        raise LayoutReviewError(f"{renderer} produced no page images")
    return snapshots


def _has_suspicious_blank_pages(root: Path, snapshots: Sequence[Mapping[str, Any]]) -> bool:
    # A real paper can have a blank trailing page, but a run where most pages are pure
    # white is usually a renderer failure. Fall back before sending bad screenshots to
    # the vision reviewer.
    blank_pages = 0
    for snapshot in snapshots:
        path_value = snapshot.get("path")
        if not isinstance(path_value, str):
            continue
        if _png_is_nearly_blank(root / path_value):
            blank_pages += 1
    return blank_pages >= 2 or (len(snapshots) > 1 and blank_pages == len(snapshots) - 1)


def _png_is_nearly_blank(path: Path) -> bool:
    try:
        width, height, color_type, pixels = _read_png_pixels(path)
    except (OSError, ValueError, zlib.error, struct.error):
        return False
    if width <= 0 or height <= 0 or not pixels:
        return False

    if color_type == 0:
        return all(value >= 250 for value in pixels)
    if color_type == 2:
        return all(value >= 250 for value in pixels)
    if color_type in {4, 6}:
        step = 2 if color_type == 4 else 4
        for offset in range(0, len(pixels), step):
            alpha = pixels[offset + step - 1]
            color_values = pixels[offset : offset + step - 1]
            if alpha > 10 and any(value < 250 for value in color_values):
                return False
        return True
    return False


def _read_png_pixels(path: Path) -> tuple[int, int, int, bytes]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")

    offset = 8
    width = 0
    height = 0
    bit_depth = 0
    color_type = 0
    compressed = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break

    if bit_depth != 8 or color_type not in {0, 2, 4, 6}:
        raise ValueError("unsupported PNG color format")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    rows: list[bytes] = []
    previous = bytes(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        _unfilter_png_row(row, previous, filter_type, channels)
        rows.append(bytes(row))
        previous = rows[-1]
    return width, height, color_type, b"".join(rows)


def _unfilter_png_row(row: bytearray, previous: bytes, filter_type: int, bpp: int) -> None:
    if filter_type == 0:
        return
    if filter_type == 1:
        for index in range(len(row)):
            left = row[index - bpp] if index >= bpp else 0
            row[index] = (row[index] + left) & 0xFF
        return
    if filter_type == 2:
        for index, value in enumerate(previous):
            row[index] = (row[index] + value) & 0xFF
        return
    if filter_type == 3:
        for index in range(len(row)):
            left = row[index - bpp] if index >= bpp else 0
            up = previous[index]
            row[index] = (row[index] + ((left + up) // 2)) & 0xFF
        return
    if filter_type == 4:
        for index in range(len(row)):
            left = row[index - bpp] if index >= bpp else 0
            up = previous[index]
            up_left = previous[index - bpp] if index >= bpp else 0
            row[index] = (row[index] + _paeth(left, up, up_left)) & 0xFF
        return
    raise ValueError(f"unsupported PNG filter {filter_type}")


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    up_left_distance = abs(estimate - up_left)
    if left_distance <= up_distance and left_distance <= up_left_distance:
        return left
    if up_distance <= up_left_distance:
        return up
    return up_left


def _extract_pdf_layout_text(pdf_path: Path, *, timeout: float | None) -> str:
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
    venue: VenueProfile,
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
                action="fix_bibliography_appendix_order",
            )
        )

    if venue.has_fixed_page_budget and _forced_break_before_conclusion(tex_text):
        penalty += 1.0
        issues.append(
            _issue(
                "forced_page_break_before_conclusion",
                "major",
                (
                    f"manual page break immediately before Conclusion can strand page {venue.conclusion_max_page} "
                    f"mostly blank or push Conclusion to page {venue.conclusion_max_page + 1}; rebalance body content and "
                    "floats instead of forcing the section break"
                ),
                action="rebalance_columns",
                target="pre-Conclusion page break",
            )
        )

    body_tex = tex_text.split(r"\appendix", 1)[0]

    # A sixth figure used to be a hard gate: six body figures failed the layout
    # review outright, while accepted work in these areas carries between four
    # and twenty-six. Whether a figure earns its space is what the argument
    # needs it to show, which is the reviewing Agent's to judge and was never a
    # number the host could hold.

    # Advisory (two-column venues only): a teaser/pipeline/overview graphic in a
    # single-column ``figure`` should usually span both columns via ``figure*``.
    if venue.two_column:
        misplaced = _single_column_wide_role_figures(body_tex)
        if misplaced:
            penalty += 0.3
            issues.append(
                _issue(
                    "wide_role_figure_single_column",
                    "major",
                    (
                        f"{len(misplaced)} overview/teaser/pipeline figure(s) "
                        f"({', '.join(misplaced)}) sit in a single-column `figure`; a "
                        "teaser or pipeline/architecture overview should span both columns "
                        "via `figure*` (`[t]`, width=\\textwidth). Sub-module/detail figures "
                        "stay single-column"
                    ),
                    action="rebalance_columns",
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
    conclusion_page = _first_layout_page_matching(
        layout_pages, LAYOUT_CONCLUSION_HEADING_PATTERN
    )
    if (
        venue.has_fixed_page_budget
        and conclusion_page is not None
        and conclusion_page < venue.conclusion_underfill_page
    ):
        # Advisory, not a gate. The venue page count is a *limit*, not a quota:
        # a complete, well-argued paper that ends early is not deficient for
        # ending early. Earlier harness scoring rejected short papers on
        # arithmetic and pushed authors to pad the body. Reviewers
        # should block only when a page count that low means something material
        # is actually missing — which they judge by reading the paper, not from
        # this signal.
        penalty += 0.2
        issues.append(
            _issue(
                "rendered_main_body_underfilled",
                "advisory",
                (
                    f"Conclusion starts before page {venue.conclusion_underfill_page} of the "
                    f"{venue.body_page_limit}-page {venue.display_name} budget. That is allowed "
                    "— the limit is a ceiling, not a quota. Check whether any method, evidence, "
                    "or argument a reader needs is actually missing; if nothing is missing, the "
                    "length is fine and no padding is warranted"
                ),
                page=conclusion_page,
                action="expand_evidence_content",
                target=f"page {conclusion_page} early Conclusion",
            )
        )
    elif (
        venue.has_fixed_page_budget
        and conclusion_page is not None
        and conclusion_page > venue.conclusion_max_page
    ):
        penalty += 0.7
        issues.append(
            _issue(
                "conclusion_after_page_8",
                "major",
                (
                    f"Conclusion starts after page {venue.conclusion_max_page}, so the paper "
                    f"exceeds the {venue.display_name} main-body page budget; move low-value "
                    "body material to the appendix or tighten prose without deleting evidence"
                ),
                page=conclusion_page,
                action="trim_or_move_content",
                target=f"page {conclusion_page} late Conclusion",
            )
        )

    references_page = _first_layout_page_matching(
        layout_pages,
        LAYOUT_REFERENCES_HEADING_PATTERN,
    )
    appendix_page = _first_layout_page_matching(
        layout_pages,
        rf"(?im)^[ \t]*{LAYOUT_TOP_LEVEL_NUMBER_PREFIX}"
        r"(?:Reproducibility\s+Appendix|Appendix)[ \t]*$",
    )
    conclusion_within_budget = (
        True
        if not venue.has_fixed_page_budget
        else conclusion_page is None or conclusion_page <= venue.conclusion_max_page
    )
    references_after_body = (
        True
        if not venue.has_fixed_page_budget
        else references_page is None or references_page >= venue.references_min_page
    )
    # NOTE: the dict keys ``conclusion_by_page_8`` / ``references_on_or_after_page_9``
    # are read by name downstream (advisory/whitespace helpers); their names are
    # kept stable for compatibility, but the VALUES are now venue-relative
    # (page 8/9 for EMNLP, 7/8 for AAAI).
    page_flow_contract = {
        "page_count": len(layout_pages),
        "conclusion_page": conclusion_page,
        "references_page": references_page,
        "appendix_page": appendix_page,
        "conclusion_by_page_8": conclusion_within_budget,
        "references_on_or_after_page_9": references_after_body,
        "post_body_pages_uncapped": True,
    }
    # Keep the historical EMNLP deterministic payload byte-compatible. New
    # venue metadata is needed only by non-EMNLP prompts.
    if venue.key != "EMNLP":
        page_flow_contract.update(
            {
                "fixed_page_budget_enforced": venue.has_fixed_page_budget,
                "main_text_word_limit": venue.main_text_word_limit,
            }
        )
    if references_page is not None:
        reference_page_text = layout_pages[references_page - 1]
        has_conclusion_on_reference_page = bool(
            re.search(LAYOUT_CONCLUSION_HEADING_PATTERN, reference_page_text)
        )
        end_matter_pattern = venue.end_matter_boundary_pattern()
        has_body_end_matter_on_reference_page = bool(
            end_matter_pattern and re.search(end_matter_pattern, reference_page_text)
        )
        formal_boundary_passes = bool(
            page_flow_contract["conclusion_by_page_8"]
            and page_flow_contract["references_on_or_after_page_9"]
        )
        if (
            venue.has_fixed_page_budget
            and has_conclusion_on_reference_page
        ) or (
            has_body_end_matter_on_reference_page and not formal_boundary_passes
        ):
            penalty += 0.9
            issues.append(
                _issue(
                    "references_share_body_page",
                    "major",
                    (
                        "References begin on the same rendered page as body end matter; "
                        "fix the body/reference boundary without generic shortening. "
                        "Do not hard-separate post-conclusion end matter from References "
                        + (
                            f"when Conclusion is by page {venue.conclusion_max_page} and "
                            f"References start on page {venue.references_min_page} or later"
                            if venue.has_fixed_page_budget
                            else "for a venue with no fixed body-page boundary"
                        )
                    ),
                    page=references_page,
                    action="fix_reference_boundary",
                    target=f"page {references_page} References boundary",
                )
            )
        elif (
            venue.has_fixed_page_budget
            and references_page < venue.references_min_page
        ):
            penalty += 0.7
            issues.append(
                _issue(
                    "references_before_full_body",
                    "major",
                    (
                        "References begin before the paper visibly fills the body budget; "
                        f"a {venue.body_page_limit}-page {venue.display_name} body should push "
                        f"references to page {venue.references_min_page} or later; "
                        "expand from verified evidence instead of padding"
                    ),
                    page=references_page,
                    action="expand_evidence_content",
                    target=f"page {references_page} early References",
                )
            )
    if venue.has_fixed_page_budget and _forced_break_before_references(tex_text) and (
        (references_page is not None and references_page < venue.references_min_page)
        or (conclusion_page is not None and conclusion_page < venue.conclusion_underfill_page)
    ):
        penalty += 0.8
        issues.append(
            _issue(
                "forced_reference_break_with_underfilled_body",
                "major",
                (
                    "manual page break immediately before References is masking an underfilled "
                    "body; remove the break and add source-backed body or post-conclusion scope "
                    f"content until References naturally start on page {venue.references_min_page} or later"
                ),
                page=references_page,
                action="expand_evidence_content",
                target="pre-References page break",
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
        "page_flow": max(
            1.0,
            5.0 - sum(0.8 for issue in issues if issue.get("severity") == "blocking"),
        ),
    }
    return {
        "score_1_to_5": round(score, 2),
        "criteria_scores": {key: round(value, 2) for key, value in criteria_scores.items()},
        "page_flow_contract": page_flow_contract,
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
    timeout: float | None,
    venue: VenueProfile,
) -> dict[str, Any]:
    route = _require_route("image_review", env)
    selected = page_snapshots
    prompt = _vision_prompt(deterministic=deterministic, threshold=threshold, venue=venue)
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
    parsed = _parse_review_text(raw_text)
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


def _venue_neutral_signals(deterministic: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the deterministic signals with the kept-name page-flow
    booleans renamed to venue-neutral keys.

    ``page_flow_contract`` keeps the historical EMNLP-literal key names
    ``conclusion_by_page_8`` / ``references_on_or_after_page_9`` (internal
    readers depend on them), but for a non-EMNLP venue those names are
    misleading when serialized into the vision-model hint (e.g. a page-8 AAAI
    references paper would read ``references_on_or_after_page_9: true``). This
    renames them only in the copy fed to the prompt, so the model sees a hint
    consistent with the venue page numbers in the prose.
    """
    import copy

    det = copy.deepcopy(deterministic)
    pfc = det.get("page_flow_contract") if isinstance(det, dict) else None
    if isinstance(pfc, dict):
        if "conclusion_by_page_8" in pfc:
            pfc["conclusion_within_budget"] = pfc.pop("conclusion_by_page_8")
        if "references_on_or_after_page_9" in pfc:
            pfc["references_after_body"] = pfc.pop("references_on_or_after_page_9")
    return det


def _vision_prompt(
    *, deterministic: dict[str, Any], threshold: float, venue: VenueProfile
) -> str:
    if venue.key == "EMNLP":
        # EMNLP keeps a venue-specific literal; policy changes intentionally
        # invalidate the persisted prompt/input hashes.
        return _vision_prompt_emnlp_literal(
            deterministic=deterministic, threshold=threshold
        )
    # Non-EMNLP: feed the model venue-neutral page-flow key names so the hint
    # is not self-contradictory with the venue page numbers in the prose.
    deterministic = _venue_neutral_signals(deterministic)
    vn = venue.display_name
    if not venue.has_fixed_page_budget:
        word_limit = (
            f"{venue.main_text_word_limit:,}-word main-text limit"
            if venue.main_text_word_limit is not None
            else "main-text word limit"
        )
        return (
            f"Role: You are an independent visual reviewer for a {vn} paper. "
            f"Judge the rendered screenshots as a polished {venue.layout_format_persona}, "
            "not as a two-column conference paper.\n\n"
            f"Venue contract: there is no fixed body-page limit. Enforce the {word_limit}, "
            "single spacing, page numbers, review line numbers, readable editable tables, "
            "real single-anonymized author metadata, journal-compliant public AI disclosure, "
            "and distinct alt text for every figure. Do not manufacture an underfill or "
            "overflow defect from the Conclusion or References page number. Still report their "
            "actual pages and flag genuine clipping, overlap, unreadable typography, forced "
            "blank pages, detached captions, or poor visual flow.\n\n"
            "Figure policy: judge the actual visible figure for clarity and "
            "good-enough aesthetics. Optional FIGURE_PROVENANCE.json may help locate "
            "the source but is not a blocker. Do not request repeated regeneration "
            "for minor stylistic preferences.\n\n"
            "Every blocking or major issue must name the page, target, visual evidence, root "
            "cause, concrete source edits, visual goal, and verification steps. Do not ask the "
            "author to pad the manuscript to resemble an exemplar or to move References to an "
            "arbitrary page.\n\n"
            "Write a prose review, not JSON. Order material findings by severity. For each "
            "finding give the page or visual target, the evidence visible in the rendering, "
            "and a concrete source-level fix with a verification step. If there is no material "
            "defect, say so plainly. This review is advisory to the agent checklist.\n\n"
            f"Deterministic layout signals:\n"
            f"{json.dumps(deterministic, ensure_ascii=False)[:6000]}"
        )
    cmax = venue.conclusion_max_page         # Conclusion must land by this page
    cmin = venue.conclusion_underfill_page   # before this => underfilled body
    rmin = venue.references_min_page          # References on/after this page
    bpl = venue.body_page_limit
    end_matter = venue.end_matter_prose()
    review_lines = venue.review_linenumber_prose()
    return (
        f"Role: You are an independent visual reviewer for an {vn} paper that is being "
        "prepared for submission. Your job is to judge the rendered PDF screenshots as a polished, "
        "standard two-column conference paper: visual beauty, professional layout, readability, "
        f"and compliance with {vn} paper norms. Do not act as the author and do not excuse "
        "ugly artifacts; be as strict as a proceedings layout reviewer.\n\n"
        "Review task: inspect the screenshots page by page, using the deterministic signals below "
        "as concrete hints. Penalize any page that looks non-submission-ready: large blank lower-page "
        "regions before the body boundary, float-dump pages, cramped or plain audit-style tables, table/body overlap, tiny "
        "unreadable fonts, awkward two-column imbalance, captions detached from content, weak page "
        "flow, square or low-quality figures, non-human code-like labels, snake_case labels, heavy "
        f"gradients, photorealism, or visuals that look like debug artifacts rather than {vn} paper "
        "figures. A pre-body-boundary page with only a couple of small tables and a large empty area "
        "is a hard visual failure even if LaTeX compiles. Final References/Appendix pages are "
        f"post-body pages: when Conclusion is by page {cmax} and References/Appendix start on page {rmin} or "
        "later, natural trailing whitespace on the last appendix/reference page is advisory unless "
        "there is a separate readability defect such as overlap, detached captions, missing required "
        f"content, or unreadably tiny tables. {review_lines} "
        "Penalize only nonstandard duplicate line-number overlays, margin counters "
        "unrelated to review mode, or post-processing artifacts. Do not turn a small amount of "
        "post-body whitespace into repeated revision churn when the formal page contract already "
        f"passes: conclusion by page {cmax}, {end_matter}, and References/Appendix "
        f"on page {rmin} or later.\n\n"
        "Make the feedback concrete for the next engineer/tool call: every blocking or major issue "
        "must name the page number when visible, the visual target (for example: page 6 lower half, "
        "Table 3, Figure 1 labels, references page), the visual evidence you saw, and the specific "
        "source-level action needed. Prefer fixes that rewrite/rebalance manuscript flow, merge or "
        "remove low-value floats, split unreadable tables, or regenerate poor figures; do not suggest "
        "cosmetic page-break shuffling when the real defect is weak prose/float integration. "
        "Figure repair policy: judge visible clarity and aesthetics, not provenance. "
        "Pass a readable, coherent, factually correct, good-looking-enough figure. "
        "Recommend at most one targeted aesthetic repair; a second regeneration needs "
        "a concrete remaining defect such as unreadable text, wrong content, broken "
        "rendering, or severe mismatch. Optional renderer metadata may help locate source. "
        f"Never repair the {bpl}-page body boundary by inserting `\\clearpage`, `\\newpage`, "
        f"`\\pagebreak`, or `\\FloatBarrier` immediately before Conclusion; that can leave page {cmax} "
        f"mostly blank and then push Conclusion to page {cmax + 1} after minor float changes. Use section "
        "ordering, prose tightening/expansion, and float placement instead.\n\n"
        "Complete improvement guidance is mandatory, not optional. For every blocking or major issue, "
        "provide enough repair guidance that an engineer can act without re-interpreting the screenshot: "
        "root_cause, source_targets (LaTeX/generator/table/figure files or section names to edit), "
        "specific_edits (ordered concrete edits, not vague advice), visual_goal, and verification "
        "steps after recompilation. The guidance must say whether to delete filler, merge/split/move "
        "specific floats, rewrite nearby prose, regenerate a figure, or change table styling. If the "
        "page is ugly because the paper is underfilled or padded with audit-like content, say exactly "
        "which body section should be expanded with source-backed narrative and which low-value "
        "artifact/table should move to appendix or be deleted. Valid expansion targets include "
        "literature-grounded Introduction/Related Work framing, benchmark or Method detail, and "
        "evidence-backed Results/Analysis/Ablation material; generic motivation is filler. For any "
        "single table cluster, choose one dominant repair action: merge low-density redundant tables "
        "or split an unreadably dense table, but do not issue contradictory merge and split directives "
        "for the same appendix/table target in the same review.\n\n"
        "Reference boundary guidance: if References or Bibliography starts on the same rendered page as "
        "Conclusion or post-conclusion body end matter, do not automatically call "
        "the body overlong and do not ask for generic section shortening. Determine the direction from "
        f"the page: if the body is visibly underfilled, References start before page {rmin}, "
        f"or Appendix material starts before page {rmin}, "
        "require source-backed body expansion, a meaningful late visual anchor, or a clean "
        f"reference/appendix-page break after the body; if body content actually runs past page {cmax}, then require trimming. "
        "A manual `\\clearpage`, `\\newpage`, `\\pagebreak`, or `\\FloatBarrier` immediately before "
        f"References is not an acceptable fix while the Conclusion starts before page {cmin} or References "
        f"still start before page {rmin}; remove that break and fix content/page flow first. "
        "Shortening an underfilled body makes the early-References defect worse. Do not require "
        f"References to begin exactly on page {rmin}: page {rmin + 1} or later is acceptable when the body and "
        f"body-adjacent end matter occupy page {rmin} naturally, and the total page count after the body "
        f"is uncapped. Treat page-{rmin} whitespace after end matter as at most a minor style note "
        f"unless it reflects a forced break, Conclusion after page {cmax}, or References/Appendix before "
        f"page {rmin}.\n\n"
        f"Submission contract to enforce: conclusion by page {cmax}, {end_matter}, "
        f"References before Appendix, References/Appendix on page {rmin} or later with no total-page cap, "
        "no Overfull hbox above 5pt, a figure count and width that follow what the "
        "argument has to show rather than a quota, "
        "full-width figure*, meaningful figure/table anchors across the middle body when they improve readability, table "
        "captions with numerical headlines, readable research-style tables, adaptive/landscape "
        "conceptual figures rather than cramped squares, and no weird fonts, tiny labels, heavy "
        "gradients, photorealism, or code-like labels in paper-facing visuals.\n\n"
        "Write a prose review, not JSON. Order material findings by severity. For every finding, "
        "name the page or target, cite the visible evidence, and suggest a concrete source-level "
        "fix plus a verification step. If no material defect remains, say so plainly. The review "
        "is advisory to the agent checklist.\n\n"
        f"Deterministic layout signals:\n{json.dumps(deterministic, ensure_ascii=False)[:6000]}"
    )


def _vision_prompt_emnlp_literal(
    *, deterministic: dict[str, Any], threshold: float
) -> str:
    """Build the EMNLP-specific visual-review prompt."""
    return (
        "Role: You are an independent visual reviewer for an EMNLP 2026 paper that is being "
        "prepared for submission. Your job is to judge the rendered PDF screenshots as a polished, "
        "standard two-column conference paper: visual beauty, professional layout, readability, "
        "and compliance with EMNLP/ACL paper norms. Do not act as the author and do not excuse "
        "ugly artifacts; be as strict as a proceedings layout reviewer.\n\n"
        "Review task: inspect the screenshots page by page, using the deterministic signals below "
        "as concrete hints. Penalize any page that looks non-submission-ready: large blank lower-page "
        "regions before the body boundary, float-dump pages, cramped or plain audit-style tables, table/body overlap, tiny "
        "unreadable fonts, awkward two-column imbalance, captions detached from content, weak page "
        "flow, square or low-quality figures, non-human code-like labels, snake_case labels, heavy "
        "gradients, photorealism, or visuals that look like debug artifacts rather than EMNLP paper "
        "figures. A pre-body-boundary page with only a couple of small tables and a large empty area "
        "is a hard visual failure even if LaTeX compiles. Final References/Appendix pages are "
        "post-body pages: when Conclusion is by page 8 and References/Appendix start on page 9 or "
        "later, natural trailing whitespace on the last appendix/reference page is advisory unless "
        "there is a separate readability defect such as overlap, detached captions, missing required "
        "content, or unreadably tiny tables. Official ACL/EMNLP anonymous review-mode line numbers from "
        "`\\usepackage[review]{acl}` are acceptable submission artifacts and must not be treated as "
        "debug gutters. Penalize only nonstandard duplicate line-number overlays, margin counters "
        "unrelated to ACL review mode, or post-processing artifacts. Do not turn a small amount of "
        "post-body whitespace into repeated revision churn when the formal page contract already "
        "passes: conclusion by page 8, Limitations/Ethics after conclusion, and References/Appendix "
        "on page 9 or later.\n\n"
        "Make the feedback concrete for the next engineer/tool call: every blocking or major issue "
        "must name the page number when visible, the visual target (for example: page 6 lower half, "
        "Table 3, Figure 1 labels, references page), the visual evidence you saw, and the specific "
        "source-level action needed. Prefer fixes that rewrite/rebalance manuscript flow, merge or "
        "remove low-value floats, split unreadable tables, or regenerate poor figures; do not suggest "
        "cosmetic page-break shuffling when the real defect is weak prose/float integration. "
        "Figure repair policy: judge visible clarity and aesthetics, not provenance. "
        "Pass a readable, coherent, factually correct, good-looking-enough figure. "
        "Recommend at most one targeted aesthetic repair; a second regeneration needs "
        "a concrete remaining defect such as unreadable text, wrong content, broken "
        "rendering, or severe mismatch. Optional renderer metadata may help locate source. "
        "Never repair the eight-page body boundary by inserting `\\clearpage`, `\\newpage`, "
        "`\\pagebreak`, or `\\FloatBarrier` immediately before Conclusion; that can leave page 8 "
        "mostly blank and then push Conclusion to page 9 after minor float changes. Use section "
        "ordering, prose tightening/expansion, and float placement instead.\n\n"
        "Complete improvement guidance is mandatory, not optional. For every blocking or major issue, "
        "provide enough repair guidance that an engineer can act without re-interpreting the screenshot: "
        "root_cause, source_targets (LaTeX/generator/table/figure files or section names to edit), "
        "specific_edits (ordered concrete edits, not vague advice), visual_goal, and verification "
        "steps after recompilation. The guidance must say whether to delete filler, merge/split/move "
        "specific floats, rewrite nearby prose, regenerate a figure, or change table styling. If the "
        "page is ugly because the paper is underfilled or padded with audit-like content, say exactly "
        "which body section should be expanded with source-backed narrative and which low-value "
        "artifact/table should move to appendix or be deleted. Valid expansion targets include "
        "literature-grounded Introduction/Related Work framing, benchmark or Method detail, and "
        "evidence-backed Results/Analysis/Ablation material; generic motivation is filler. For any "
        "single table cluster, choose one dominant repair action: merge low-density redundant tables "
        "or split an unreadably dense table, but do not issue contradictory merge and split directives "
        "for the same appendix/table target in the same review.\n\n"
        "Reference boundary guidance: if References or Bibliography starts on the same rendered page as "
        "Conclusion, Limitations, Ethics, or release/reproducibility body text, do not automatically call "
        "the body overlong and do not ask for generic section shortening. Determine the direction from "
        "the page: if the body is visibly underfilled, References start before page 9, "
        "or Appendix material starts before page 9, "
        "require source-backed body expansion, a meaningful late visual anchor, or a clean "
        "reference/appendix-page break after the body; if body content actually runs past page 8, then require trimming. "
        "A manual `\\clearpage`, `\\newpage`, `\\pagebreak`, or `\\FloatBarrier` immediately before "
        "References is not an acceptable fix while the Conclusion starts before page 7 or References "
        "still start before page 9; remove that break and fix content/page flow first. "
        "Shortening an underfilled body makes the early-References defect worse. Do not require "
        "References to begin exactly on page 9: page 10 or later is acceptable when the body and "
        "body-adjacent end matter occupy page 9 naturally, and the total page count after the body "
        "is uncapped. Treat page-9 whitespace after Limitations/Ethics as at most a minor style note "
        "unless it reflects a forced break, Conclusion after page 8, or References/Appendix before "
        "page 9.\n\n"
        "Submission contract to enforce: conclusion by page 8, Limitations/Ethics after conclusion, "
        "References before Appendix, References/Appendix on page 9 or later with no total-page cap, "
        "no Overfull hbox above 5pt, a figure count and width that follow what the "
        "argument has to show rather than a quota, "
        "full-width figure*, meaningful figure/table anchors across the middle body when they improve readability, table "
        "captions with numerical headlines, readable research-style tables, adaptive/landscape "
        "conceptual figures rather than cramped squares, and no weird fonts, tiny labels, heavy "
        "gradients, photorealism, or code-like labels in paper-facing visuals.\n\n"
        "Write a prose review, not JSON. Order material findings by severity. For every finding, "
        "name the page or target, cite the visible evidence, and suggest a concrete source-level "
        "fix plus a verification step. If no material defect remains, say so plainly. The review "
        "is advisory to the agent checklist.\n\n"
        f"Deterministic layout signals:\n{json.dumps(deterministic, ensure_ascii=False)[:6000]}"
    )










def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    page: int | None = None,
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
    if target:
        issue["target"] = target
    return issue


def _normalize_action(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return normalized if normalized in ALLOWED_DIRECTIVE_ACTIONS else None


def _single_column_wide_role_figures(body_tex: str) -> list[str]:
    """Identifiers of overview/teaser/pipeline figures placed in a single-column
    ``figure`` (rather than a full-width ``figure*``).

    Only the ``\\includegraphics`` path(s) and ``\\label`` of each single-column
    figure block are matched against the role regex, so caption prose that
    merely mentions "our pipeline" does not trigger a false positive. Returns a
    short identifier per offending figure (its label, else the graphic
    basename), de-duplicated in document order.
    """
    found: list[str] = []
    seen: set[str] = set()
    # Non-greedy match of each single-column figure block; ``figure\b`` excludes
    # the ``figure*`` star form.
    for match in re.finditer(
        r"\\begin\s*\{\s*figure\s*\}(.*?)\\end\s*\{\s*figure\s*\}", body_tex, re.S
    ):
        block = match.group(1)
        graphics = re.findall(r"\\includegraphics[^{}]*\{([^}]*)\}", block)
        labels = re.findall(r"\\label\s*\{([^}]*)\}", block)
        targets = " ".join(graphics + labels)
        if not _WIDE_FIGURE_ROLE_RE.search(targets):
            continue
        ident = (labels[0] if labels else (graphics[0] if graphics else "figure"))
        ident = ident.rsplit("/", 1)[-1]
        if ident not in seen:
            seen.add(ident)
            found.append(ident)
    return found


def _references_after_appendix(tex_text: str) -> bool:
    appendix = re.search(r"\\appendix\b", tex_text)
    bibliography = re.search(
        r"\\(?:bibliography\s*\{|printbibliography\b|begin\s*\{\s*thebibliography\s*\})",
        tex_text,
    )
    return appendix is not None and bibliography is not None and appendix.start() < bibliography.start()


def _forced_break_before_conclusion(tex_text: str) -> bool:
    return bool(
        re.search(
            r"\\(?:clearpage|newpage|pagebreak(?:\[[^\]]+\])?|FloatBarrier)\s*"
            r"\\section\*?\s*\{\s*Conclusion\s*\}",
            tex_text,
        )
    )


def _forced_break_before_references(tex_text: str) -> bool:
    return bool(
        re.search(
            r"\\(?:clearpage|newpage|pagebreak(?:\[[^\]]+\])?|FloatBarrier)\s*"
            r"\\(?:bibliography\s*\{|printbibliography\b|begin\s*\{\s*thebibliography\s*\})",
            tex_text,
        )
    )


def _parse_review_text(text: str) -> dict[str, Any]:
    """Preserve an ordinary review; accept volunteered legacy JSON without requiring it."""
    from ...core.role_reply import legacy_json_object

    legacy = legacy_json_object(text)
    parsed = dict(legacy) if legacy is not None else {}
    parsed["review_text"] = text
    return parsed


def _layout_review_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Layout Review",
        "",
        "- Decision authority: `agent_checklist` (the reviewer agent decides; "
        "the harness emits no quality verdict)",
        f"- Structural status: `{result.get('structural_status', 'ok')}`",
        f"- Review method: `{result.get('review_method', 'facts_only')}`",
        "",
    ]
    facts = result.get("facts")
    if isinstance(facts, dict) and facts:
        lines.extend(["## Facts", ""])
        for key in sorted(facts):
            lines.append(f"- `{key}`: {facts[key]}")
        lines.append("")
    issues = result.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["## Structural issues", ""])
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            page = f" page {issue['page']}:" if "page" in issue else ""
            lines.append(f"- `{issue.get('severity', 'unknown')}`{page} {issue.get('message', '')}")
        lines.append("")
    review = result.get("vision_review")
    if isinstance(review, dict) and str(review.get("review_text") or "").strip():
        lines.extend(["## Advisory visual review", "", str(review["review_text"]), ""])
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
        "structural_status": result.get("structural_status"),
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



def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.verticals.research.paper_layout_review",
        description="Render and score final paper layout aesthetics.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--review-mode", choices=("vision", "heuristic"), default="vision")
    parser.add_argument("--threshold", type=float, default=MIN_LAYOUT_SCORE)
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="explicit page ceiling; omitted reviews the complete PDF",
    )
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
    return 0 if result.get("structural_status") == "ok" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
