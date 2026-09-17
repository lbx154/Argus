"""Deterministic checks on the figures a manuscript includes.

A reader judges a paper's figures at final size; the checks here catch the
defects that reliably make data figures look unfinished and that a script can
verify without a model:

* every ``\\includegraphics`` target exists;
* a data figure exported by matplotlib is a vector PDF, not a raster;
* a matplotlib PDF embeds TrueType fonts rather than the Type 3 default, which
  is what the shared ``paper_chart_style`` helper configures and what
  camera-ready checks reject;
* a plotting script that writes figures with matplotlib imports the shared
  ``paper_chart_style`` helper, so palette, sizes, and typography are the
  venue's rather than the library defaults.

Composition defects (overlapping legends, missing uncertainty, sentinel values
on log axes) need eyes; those stay with the figure review and the Reviewer's
page inspection. This module runs from the Paper stage completion check and
from the command line.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

_INCLUDE_RE = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
_GRAPHICSPATH_RE = re.compile(r"\\graphicspath\s*\{((?:\s*\{[^}]*\}\s*)+)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")
_GRAPHIC_EXTENSIONS = ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")
_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg"}
_BOX_PATCH = re.compile(r"FancyBboxPatch|patches\.Rectangle|Rectangle\(|FancyArrowPatch|ConnectionPatch")
_TEXT_CALL = re.compile(r"\.(?:text|annotate)\(")
_ARROW_PROPS = re.compile(r"arrowprops\s*=")
_DATA_CALL = re.compile(r"\.(?:plot|bar|barh|scatter|errorbar|imshow|hist|boxplot|fill_between|violinplot|pcolormesh|contourf?)\(")
_FIGURE_ENV_RE = re.compile(r"\\begin\{figure\*?\}(?P<body>.*?)\\end\{figure\*?\}", re.DOTALL)
_CAPTION_RE = re.compile(r"\\caption\s*(?:\[[^\]]*\])?\s*\{(?P<text>[^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", re.DOTALL)
_LABEL_RE = re.compile(r"\\label\s*\{([^}]+)\}")
# Words that make a figure the paper's method figure: the one Method D owns.
_METHOD_FIGURE_WORDS = re.compile(
    r"architect|overview|pipeline|framework|mechanism|schematic|illustrat|workflow|"
    r"system design|our (?:method|approach)|teaser",
    re.IGNORECASE,
)
# A results chart often says "framework" or "mechanism" in passing; measured
# quantities in the caption mean it is a data figure, which keeps its route.
_DATA_FIGURE_WORDS = re.compile(
    r"accuracy|error|rate\b|\d+\s*%|\bvs\.?\b|versus|against|budget|ablation|"
    r"comparison|results?\b|curve|performance|throughput|latency|score|loss|"
    r"precision|recall|\bF1\b|runtime|speedup|convergence|scaling|success",
    re.IGNORECASE,
)
_SKIPPED_DIRS = {
    # A pinned reference clone is somebody else's plotting code; the lint
    # speaks about this project's figures. One report listed three files
    # under third_party/ as the project's only figure findings.
    "third_party",
    ".venv",
    "venv",
    "node_modules",
    "site-packages",
    "__pycache__",
    "build",
    "dist",
    "tests",
    "test",
}
_MAX_TEX_FILES = 60
_MAX_SCRIPTS = 3000
_MAX_SCRIPT_BYTES = 400_000
_MAX_WALK_DEPTH = 6

STYLE_HELPER = "paper_chart_style"
CHART_HELPER = "paper_charts"
HELPER_FILES = {f"{STYLE_HELPER}.py", f"{CHART_HELPER}.py"}
_HEX_COLOR = re.compile(r"[\"']#[0-9A-Fa-f]{6}[\"']")
_PINNED_LEGEND = re.compile(
    r"\.legend\([^)]*(?:frameon\s*=\s*True|facecolor\s*=|loc\s*=\s*[\"'](?:upper|lower|center)[^\"']*[\"'])"
)
_YLIM_LOWER = re.compile(r"set_ylim\(\s*(?:bottom\s*=\s*)?(\d+(?:\.\d+)?)")
_BAR_CALL = re.compile(r"\.barh?\(")
_TITLE_CALL = re.compile(r"\.set_title\(|\.suptitle\(")
HAND_COLOURS = 3
__all__ = ["STYLE_HELPER", "figure_lint_issues", "included_graphics", "main", "method_figures"]


def _strip_comments(text: str) -> str:
    return "\n".join(_COMMENT_RE.sub("", line) for line in text.splitlines())


def _read(path: Path) -> str:
    try:
        return _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def _tex_sources(paper_root: Path) -> list[Path]:
    main = paper_root / "main.tex"
    if not main.is_file():
        return []
    ordered: list[Path] = []
    seen: set[Path] = set()
    queue = [main]
    while queue and len(ordered) < _MAX_TEX_FILES:
        current = queue.pop(0)
        resolved = current.resolve()
        if resolved in seen or not current.is_file():
            continue
        seen.add(resolved)
        ordered.append(current)
        text = _read(current)
        for raw in _INPUT_RE.findall(text):
            name = raw.strip()
            if not name:
                continue
            candidate = paper_root / name
            if candidate.suffix != ".tex" and not candidate.is_file():
                candidate = candidate.with_suffix(".tex")
            queue.append(candidate)
    return ordered


def _graphics_paths(text: str) -> list[str]:
    prefixes: list[str] = []
    for group in _GRAPHICSPATH_RE.findall(text):
        prefixes.extend(
            item.strip() for item in re.findall(r"\{([^}]*)\}", group) if item.strip()
        )
    return prefixes


def included_graphics(paper_root: Path) -> list[tuple[str, Path | None]]:
    """Return ``(raw_reference, resolved_path_or_None)`` for every included graphic."""
    sources = _tex_sources(paper_root)
    prefixes: list[str] = [""]
    references: list[str] = []
    for source in sources:
        text = _read(source)
        prefixes.extend(_graphics_paths(text))
        references.extend(raw.strip() for raw in _INCLUDE_RE.findall(text))
    project_root = paper_root.parent
    results: list[tuple[str, Path | None]] = []
    for raw in dict.fromkeys(references):
        if not raw:
            continue
        resolved: Path | None = None
        for base in (paper_root, project_root):
            for prefix in prefixes:
                for extension in _GRAPHIC_EXTENSIONS:
                    candidate = base / prefix / f"{raw}{extension}"
                    if candidate.is_file():
                        resolved = candidate
                        break
                if resolved is not None:
                    break
            if resolved is not None:
                break
        results.append((raw, resolved))
    return results


def method_figures(paper_root: Path) -> list[tuple[str, Path | None, str]]:
    """``(raw_reference, resolved_path, caption)`` for the manuscript's method figures.

    A figure environment whose caption or label speaks of the architecture,
    overview, pipeline, framework or mechanism; failing any such wording, the
    first figure environment in the main source. These are the figures Method
    D (PPT Master) owns.
    """
    resolved_by_raw = {raw: resolved for raw, resolved in included_graphics(paper_root)}
    found: list[tuple[str, Path | None, str]] = []
    first: tuple[str, Path | None, str] | None = None
    for source in _tex_sources(paper_root):
        text = _strip_comments(_read(source))
        for match in _FIGURE_ENV_RE.finditer(text):
            body = match.group("body")
            refs = [raw.strip() for raw in _INCLUDE_RE.findall(body) if raw.strip()]
            if not refs:
                continue
            caption = " ".join(m.group("text") for m in _CAPTION_RE.finditer(body))
            labels = " ".join(_LABEL_RE.findall(body))
            caption_one_line = re.sub(r"\s+", " ", caption).strip()
            entry = (refs[0], resolved_by_raw.get(refs[0]), caption_one_line[:200])
            # The file name or label saying "mechanism" or "architecture" is the
            # author's own classification and wins; a caption alone counts only
            # when it carries no measured quantity, since results captions say
            # "framework" in passing.
            named_method = _METHOD_FIGURE_WORDS.search(labels + " " + refs[0]) is not None
            data_words = _DATA_FIGURE_WORDS.search(caption + " " + labels) is not None
            if first is None and not data_words:
                first = entry
            if named_method or (_METHOD_FIGURE_WORDS.search(caption) and not data_words):
                found.append(entry)
    if not found and first is not None:
        found.append(first)
    return found


def _native_sources(project_root: Path) -> list[Path]:
    """Every editable PPTX under paper/ (PPT Master's canonical source)."""
    paper_root = project_root / "paper"
    if not paper_root.is_dir():
        return []
    sources: list[Path] = []
    for current, dirs, files in os.walk(paper_root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIPPED_DIRS]
        sources.extend(Path(current) / name for name in files if name.lower().endswith(".pptx"))
    return sources


_PPTX_SHAPE = re.compile(rb"<p:(?:sp|cxnSp|pic|grpSp)>")
_PPTX_CUSTGEOM = re.compile(rb"<a:custGeom>")
_PPTX_TEXT = re.compile(rb"<a:t>([^<]*)</a:t>")
_PDF_PATH_OP = re.compile(rb"(?<=[\s])(?:m|l|c|v|y|re)(?=[\s])")
_WORD = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")
#: A PDF with at least this many path segments is a drawing, not a page of text.
# Producers that read TeX or a plotting script, never a PPTX: a PDF stamped
# with one of these was not exported from the PPTX of the same stem.
_NOT_A_PPTX_EXPORT = re.compile(r"pdfTeX|XeTeX|LuaTeX|LuaHBTeX|dvips|dvipdfm|TikZ|pgf|Graphviz|R \d|grDevices", re.IGNORECASE)
# Producers an export from a PPTX can carry: the vertical's exporter
# (pptx_export.py renders through Chromium, producer Skia/PDF) and the Office
# suites. Anything else beside a PPTX of the same stem came from elsewhere.
_PPTX_EXPORT_PRODUCER = re.compile(r"Skia/PDF|LibreOffice|OpenOffice|PowerPoint|Keynote|WPS", re.IGNORECASE)
PPTX_EXPORT_CLI = "figure_spec_scripts/pptx_export.py --pptx paper/figures/<stem>.pptx"
PDF_DRAWING_SEGMENTS = 150
#: Fewer native shapes than this, with no drawn path, is a caption-and-boxes companion.
PPTX_COMPANION_SHAPES = 20
#: Share of the export's words that must also be in the PPTX text.
PPTX_TEXT_OVERLAP = 0.6


def _pptx_summary(path: Path) -> dict[str, Any]:
    """Shapes, drawn paths and words of every slide in a PPTX, from its XML."""
    import zipfile

    shapes = paths = 0
    words: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not (name.startswith("ppt/slides/slide") and name.endswith(".xml")):
                continue
            xml = archive.read(name)
            shapes += len(_PPTX_SHAPE.findall(xml))
            paths += len(_PPTX_CUSTGEOM.findall(xml))
            text = " ".join(m.group(1).decode("utf-8", "replace") for m in _PPTX_TEXT.finditer(xml))
            words.update(w.lower() for w in _WORD.findall(text))
    return {"shapes": shapes, "paths": paths, "words": words}


def _pdf_summary(path: Path) -> dict[str, Any]:
    """Path segments and words of a PDF's pages."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    segments = 0
    words: set[str] = set()
    for page in reader.pages:
        try:
            contents = page.get_contents()
            data = contents.get_data() if contents is not None else b""
        except Exception:  # noqa: BLE001 - an odd stream counts as no drawing
            data = b""
        segments += len(_PDF_PATH_OP.findall(data))
        try:
            words.update(w.lower() for w in _WORD.findall(page.extract_text() or ""))
        except Exception:  # noqa: BLE001
            pass
    return {"segments": segments, "words": words}


def _export_matches_source(stem: str, source: Path, export: Path) -> list[str]:
    """Is the included PDF plausibly exported from the same-stem PPTX?

    One Engineer drew a detailed figure through an SVG, exported it with
    Ghostscript, and wrote a thirteen-rectangle PPTX beside it so the stem
    rule was met. The PPTX had no drawn path and the machine had no
    LibreOffice to export from it. Two facts tell: a PDF full of path segments
    against a PPTX of a few preset shapes and no custom geometry, and the
    export's words missing from the PPTX text.
    """
    try:
        pptx = _pptx_summary(source)
        pdf = _pdf_summary(export)
    except Exception:  # noqa: BLE001 - unreadable files are reported elsewhere
        return []
    issues: list[str] = []
    if (
        pdf["segments"] >= PDF_DRAWING_SEGMENTS
        and pptx["paths"] == 0
        and pptx["shapes"] < PPTX_COMPANION_SHAPES
    ):
        issues.append(
            f"method figure `{stem}.pdf` has {pdf['segments']} drawn path segments while "
            f"`{stem}.pptx` holds {pptx['shapes']} preset shapes and no drawn path: the PDF "
            "was not exported from this PPTX; Method D keeps the native PPT as the source "
            "(PPT Master's svg_to_pptx produces drawn shapes) and exports the included PDF from it"
        )
    if pdf["words"]:
        overlap = len(pdf["words"] & pptx["words"]) / len(pdf["words"])
        if overlap < PPTX_TEXT_OVERLAP:
            issues.append(
                f"method figure `{stem}.pdf` shares only {overlap:.0%} of its words with "
                f"`{stem}.pptx`: the export and the editable source show different figures"
            )
    return issues


def _method_figure_issues(project_root: Path) -> list[str]:
    """The method figure is composed through Method D or it is a defect.

    One project's Engineer ran ``ppt_master status`` (ready), then drew the
    architecture figure with matplotlib patches; the labels overlapped the
    boxes and nothing editable existed. Two checks, both from the tree: the
    exported PDF's producer, and an editable PPTX with the export's stem.
    """
    issues: list[str] = []
    paper_root = project_root / "paper"
    sources = _native_sources(project_root)
    stems = {source.stem.lower() for source in sources}
    for raw, resolved, _caption in method_figures(paper_root):
        stem = Path(raw).stem.lower()
        shown = raw
        producer = ""
        route_issue = False
        if resolved is not None and resolved.suffix.lower() == ".pdf":
            try:
                producer, _subtypes = _pdf_producer_and_fonts(resolved)
            except Exception:  # noqa: BLE001 - unreadable PDFs are reported elsewhere
                producer = ""
            route_issue = "matplotlib" in producer.lower() or _NOT_A_PPTX_EXPORT.search(producer) is not None
            if "matplotlib" in producer.lower():
                issues.append(
                    f"method figure `{shown}` was exported by matplotlib; the method figure is "
                    "composed through Method D (PPT Master; Method B fallback) per "
                    "engineer/paper-framework-figure-studio.md, never drawn as matplotlib boxes"
                )
            elif _NOT_A_PPTX_EXPORT.search(producer):
                # A TikZ standalone compiled by pdfTeX beside a python-pptx
                # companion of the same stem: the words match, the shape count
                # passes, and no PPTX export chain has ever produced pdfTeX output.
                issues.append(
                    f"method figure `{shown}` was produced by {producer.strip()[:40]}, which no PPTX "
                    f"export chain produces; a `{Path(raw).stem}.pptx` beside it is a companion, not "
                    "the source. Method D exports the included PDF from the native PPTX "
                    "(pptx_to_svg, then the SVG renderer), and the Reviewer returns a TikZ or "
                    "matplotlib drawing with a look-alike PPTX"
                )
        matched = any(stem == s or stem in s or s in stem for s in stems) if stem else False
        if not matched and not (len(stems) == 1 and len(method_figures(paper_root)) == 1):
            issues.append(
                f"method figure `{shown}` has no editable PPT Master source under paper/ "
                f"(expected `{Path(raw).stem}.pptx` beside the export); Method D keeps the "
                "native PPTX as the canonical source and exports the included PDF from it"
            )
            continue
        if resolved is not None and resolved.suffix.lower() == ".pdf":
            source = next((s for s in sources if s.stem.lower() == stem), None) or (
                sources[0] if len(sources) == 1 else None
            )
            if source is not None:
                if producer and not route_issue and _PPTX_EXPORT_PRODUCER.search(producer) is None:
                    # Ghostscript or cairo beside a PPTX: the PDF was rendered from
                    # an SVG or something else, and the PPTX was written to match.
                    issues.append(
                        f"method figure `{shown}` (producer {producer.strip()[:40]}) was not exported from "
                        f"`{source.name}`; write the PDF and PNG from the PPTX with "
                        f"`{PPTX_EXPORT_CLI.replace('<stem>', Path(raw).stem)}` (no Office needed) so the "
                        "included figure is the editable source"
                    )
                issues.extend(_export_matches_source(Path(raw).stem, source, resolved))
    return issues


def _pdf_producer_and_fonts(path: Path) -> tuple[str, set[str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    producer = ""
    try:
        metadata = reader.metadata
        if metadata is not None:
            producer = str(metadata.get("/Producer") or metadata.get("/Creator") or "")
    except Exception:  # noqa: BLE001 - metadata is optional
        producer = ""
    subtypes: set[str] = set()
    for page in reader.pages:
        try:
            resources = page.get("/Resources")
            fonts = resources.get_object().get("/Font") if resources is not None else None
            if fonts is None:
                continue
            for key in fonts.get_object():
                font = fonts.get_object()[key].get_object()
                subtypes.add(str(font.get("/Subtype") or ""))
        except Exception:  # noqa: BLE001 - malformed resources are reported below
            continue
    return producer, subtypes


def _png_text_chunks(path: Path) -> dict[str, str]:
    """Return the tEXt/zTXt/iTXt key-value pairs of a PNG without a decoder."""
    chunks: dict[str, str] = {}
    try:
        with path.open("rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                return chunks
            while True:
                header = handle.read(8)
                if len(header) < 8:
                    break
                length, kind = struct.unpack(">I4s", header)
                data = handle.read(length)
                handle.read(4)  # CRC
                if kind == b"IDAT" or kind == b"IEND":
                    break
                if kind == b"tEXt":
                    key, _, value = data.partition(b"\x00")
                    chunks[key.decode("latin-1")] = value.decode("latin-1", "replace")
                elif kind == b"zTXt":
                    key, _, rest = data.partition(b"\x00")
                    try:
                        chunks[key.decode("latin-1")] = zlib.decompress(rest[1:]).decode(
                            "latin-1", "replace"
                        )
                    except zlib.error:
                        continue
                elif kind == b"iTXt":
                    key, _, rest = data.partition(b"\x00")
                    parts = rest.split(b"\x00", 3)
                    if len(parts) == 4:
                        chunks[key.decode("latin-1")] = parts[3].decode("utf-8", "replace")
    except OSError:
        return chunks
    return chunks


def _graphic_issues(raw: str, resolved: Path | None, project_root: Path) -> list[str]:
    if resolved is None:
        return [f"figure `{raw}` is included by the manuscript but the file is missing"]
    try:
        shown = resolved.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        shown = str(resolved)
    suffix = resolved.suffix.lower()
    if suffix == ".pdf":
        try:
            producer, subtypes = _pdf_producer_and_fonts(resolved)
        except Exception as exc:  # noqa: BLE001 - a broken figure is an issue
            return [f"figure `{shown}` is not a readable PDF: {exc}"]
        if "matplotlib" in producer.lower() and "/Type3" in subtypes:
            return [
                f"figure `{shown}` embeds Type 3 fonts (plain matplotlib defaults); "
                f"draw data figures through the shared {STYLE_HELPER} helper so text is "
                "embedded as TrueType at publication size"
            ]
        return []
    if suffix in _RASTER_SUFFIXES:
        software = ""
        if suffix == ".png":
            software = _png_text_chunks(resolved).get("Software", "")
        if "matplotlib" in software.lower():
            return [
                f"figure `{shown}` is a raster export from matplotlib; include the vector "
                "PDF export instead"
            ]
    return []


def _iter_scripts(project_root: Path):
    root = project_root.resolve()
    base_depth = len(root.parts)
    count = 0
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).parts) - base_depth
        dirs[:] = sorted(
            name
            for name in dirs
            if not name.startswith(".")
            and name not in _SKIPPED_DIRS
            and depth < _MAX_WALK_DEPTH
            and not (Path(current) / name).is_symlink()
        )
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            count += 1
            if count > _MAX_SCRIPTS:
                return
            yield Path(current) / name


def _plot_script_issues(project_root: Path) -> list[str]:
    """Data figures may be drawn with matplotlib through the shared style helper;
    a box-and-arrow diagram drawn with matplotlib patches is a method figure on
    the wrong route and is named separately so the repair is obvious."""
    issues: list[str] = []
    for script in _iter_scripts(project_root):
        try:
            if script.stat().st_size > _MAX_SCRIPT_BYTES:
                continue
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "savefig(" not in text:
            continue
        if "matplotlib" not in text and "pyplot" not in text:
            continue
        shown = script.relative_to(project_root.resolve()).as_posix()
        if script.name in HELPER_FILES:
            continue  # the helpers' own demo code is not a figure script
        boxes = len(_BOX_PATCH.findall(text))
        arrows = len(_ARROW_PROPS.findall(text))
        labels = len(_TEXT_CALL.findall(text))
        if boxes >= 1 and boxes + arrows >= 3 and labels >= 6 and not _DATA_CALL.search(text):
            issues.append(
                f"`{shown}` draws a box-and-arrow diagram with matplotlib patches; "
                "conceptual and architecture figures follow "
                "engineer/paper-framework-figure-studio.md (reference figures, a design "
                "blueprint, an editable PPT Master reconstruction), not matplotlib boxes"
            )
        if CHART_HELPER in text:
            continue  # the helper made the drawing decisions
        facts = _hand_drawing_facts(text)
        if STYLE_HELPER in text:
            if facts:
                issues.append(
                    f"`{shown}` draws by hand beside the {STYLE_HELPER} theme: " + "; ".join(facts)
                    + f"; the {CHART_HELPER} helper (bars/lines/dots/save) makes these choices "
                    "the same way for every figure"
                )
            continue
        issues.append(
            f"`{shown}` saves matplotlib figures without the shared {STYLE_HELPER}/{CHART_HELPER} "
            "helpers" + (": " + "; ".join(facts) if facts else "") + "; data figures pass their "
            f"data to {CHART_HELPER} (bars/lines/dots/save, engineer/paper-chart-styling.md), and "
            "conceptual figures follow the Figure Studio workflow rather than matplotlib boxes"
        )
    return issues


def _hand_drawing_facts(text: str) -> list[str]:
    """What a plotting script decided by hand: the choices that made past figures look wrong."""
    facts: list[str] = []
    colours = len(set(c.lower() for c in _HEX_COLOR.findall(text)))
    if colours >= HAND_COLOURS:
        facts.append(f"sets {colours} colours by hand")
    if _PINNED_LEGEND.search(text):
        facts.append("pins a framed legend inside the axes")
    lower = [float(v) for v in _YLIM_LOWER.findall(text) if float(v) > 0]
    if lower and _BAR_CALL.search(text):
        facts.append(f"starts a bar axis at {lower[0]:g} instead of zero")
    if _TITLE_CALL.search(text):
        facts.append("writes an in-plot title beside the caption")
    return facts


def _figure_facts_issues(project_root: Path) -> list[str]:
    """Facts the helper recorded at export (paper/figures/src/<stem>/facts.json)."""
    issues: list[str] = []
    src = project_root / "paper" / "figures" / "src"
    if not src.is_dir():
        return issues
    for facts_path in sorted(src.glob("*/facts.json")):
        try:
            payload = json.loads(facts_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stem = str(payload.get("figure") or facts_path.parent.name)
        for panel in payload.get("panels") or []:
            if not isinstance(panel, dict):
                continue
            if panel.get("kind") == "bars" and panel.get("axis_from_zero") is False:
                reason = str(panel.get("truncated_reason") or "").strip()
                issues.append(
                    f"figure `{stem}` has bars that do not start at zero (reason recorded: "
                    f"{reason or 'none'}); the caption says so, or the axis returns to zero"
                )
            if panel.get("legend") == "inside":
                issues.append(f"figure `{stem}` places its legend inside the axes; check at final size that it covers no data")
    return issues


def figure_lint_issues(project_root: Path | str) -> tuple[str, ...]:
    """Return every deterministic figure defect for the manuscript under ``paper/``."""
    root = Path(project_root)
    paper_root = root / "paper"
    if not (paper_root / "main.tex").is_file():
        return ()
    issues: list[str] = []
    for raw, resolved in included_graphics(paper_root):
        issues.extend(_graphic_issues(raw, resolved, root))
    issues.extend(_method_figure_issues(root))
    issues.extend(_plot_script_issues(root))
    issues.extend(_figure_facts_issues(root))
    return tuple(dict.fromkeys(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus.verticals.research.figure_lint",
        description="Deterministic figure checks for the manuscript under paper/.",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    issues = figure_lint_issues(args.project_root)
    for issue in issues:
        print(issue, file=sys.stderr)
    if issues:
        return 1
    print("figures pass the deterministic checks")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
