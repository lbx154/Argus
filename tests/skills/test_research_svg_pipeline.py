from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.verticals.research import pipeline_figure as pipeline
from argus_skill.verticals.research.prompt_policy import render_role_prompt_fragment
from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

DRAWING = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900">
<title>Method</title><defs><marker id="arrow" markerWidth="6" markerHeight="6"
refX="6" refY="3" orient="auto"><path d="M0 0L6 3L0 6" fill="#334155"/></marker></defs>
<g id="pipeline-content">
<rect x="200" y="100" width="160" height="90" fill="#e8f0fa"/>
<text x="218" y="153" font-family="Arial" font-size="26">Input &amp; code</text>
<path d="M360 145L400 145L400 185L440 185" fill="none" stroke="#334155"
stroke-width="2" marker-end="url(#arrow)"/>
<rect x="440" y="140" width="170" height="90" fill="#fcebdc"/>
<text x="460" y="193" font-size="26"><tspan font-family="sans-serif">Output</tspan></text>
</g></svg>'''


def test_brief_reads_both_sources_and_only_explicit_dependencies(tmp_path: Path) -> None:
    (tmp_path / "paper.tex").write_text("The method uses gated routing.", encoding="utf-8")
    (tmp_path / "method.py").write_text("def route(x): return x.gate()", encoding="utf-8")
    (tmp_path / "old.md").write_text("STALE-HISTORY", encoding="utf-8")
    prompt = pipeline.build_pipeline_prompt(
        tmp_path, papers=[Path("paper.tex")], code=[Path("method.py")],
    )
    assert 'BEGIN manuscript "paper.tex"' in prompt
    assert "1: The method uses gated routing." in prompt
    assert 'BEGIN executed code "method.py"' in prompt
    assert "1: def route(x): return x.gate()" in prompt
    assert "STALE-HISTORY" not in prompt
    assert "Times New Roman" in prompt and "staggered" in prompt


def test_brief_does_not_silently_truncate_or_follow_outside_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "method.py"
    source.write_text("x" * (pipeline._MAX_SOURCE_BYTES + 1))
    paper = tmp_path / "paper.tex"
    paper.write_text("method")
    with pytest.raises(ValueError, match="source too large"):
        pipeline.build_pipeline_prompt(tmp_path, papers=[paper], code=[source])
    with pytest.raises(ValueError, match="at least one"):
        pipeline.build_pipeline_prompt(tmp_path, papers=[paper], code=[])
    project = tmp_path / "project"
    project.mkdir()
    (project / "paper.tex").symlink_to(paper)
    with pytest.raises(ValueError, match="inside project"):
        pipeline.build_pipeline_prompt(project, papers=[Path("paper.tex")], code=[Path("x.py")])


@pytest.mark.parametrize("extra", [
    '<script>alert(1)</script>',
    '<image href="https://example.invalid/image.png"/>',
    '<use href="file:///etc/passwd"/>',
    '<rect onclick="alert(1)"/>',
    '<style>@import "https://example.invalid/x.css";</style>',
    '<rect fill="url(https://example.invalid/x.svg)"/>',
    '<foreignObject><div>HTML</div></foreignObject>',
    '<rect width="100%" height="100%"/>',
])
def test_static_svg_contract_rejects_unrenderable_or_external_content(extra: str) -> None:
    with pytest.raises(ValueError):
        pipeline.validate_svg_source(DRAWING.replace('</g>', extra + '</g>'))


def test_svg_requires_editable_labels_and_a_crop_group() -> None:
    pipeline.validate_svg_source(DRAWING)
    with pytest.raises(ValueError, match="pipeline-content"):
        pipeline.validate_svg_source(DRAWING.replace('id="pipeline-content"', 'id="other"'))
    with pytest.raises(ValueError, match="finite viewBox"):
        pipeline.validate_svg_source(DRAWING.replace("0 0 1600 900", "0 0 nan 900"))
    with pytest.raises(ValueError, match="visible geometry"):
        pipeline.validate_svg_source(DRAWING.replace('</svg>', '<rect width="10"/></svg>'))


def test_renderer_preserves_source_and_cli_reports_input_errors(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.svg"
    source.write_text(DRAWING)
    with pytest.raises(ValueError, match="source separate"):
        pipeline.render_pipeline(source, source)
    assert pipeline.main(["render", "--input", str(source), "--output", "x.txt"]) == 2
    assert "must end in .svg" in capsys.readouterr().err
    assert source.read_text() == DRAWING


def test_research_paper_and_review_receive_svg_route() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    skill = "engineer/research-svg-pipeline.md"
    assert skill in texts
    assert skill in texts["research-paper-playbook.md"]
    assert skill in texts["research-review-playbook.md"]
    prompt = render_role_prompt_fragment(
        role="engineer", operation="author_draft", stage="paper", scope="",
        project_root=None,
    )
    assert skill in prompt
    assert "pipeline_figure" in prompt
    assert "Reuse an existing suitable figure" in prompt
    assert "after the Introduction" in prompt
    assert "page 2 or 3" in prompt
    for stage in ("paper", "review"):
        checklist = " ".join(item.statement for item in STAGE_CHECKLISTS[stage])
        assert "Times New Roman" in checklist
        assert "staggered" in checklist
    for stage in ("idea", "experiment", "review"):
        prompt = render_role_prompt_fragment(
            role="engineer", operation="", stage=stage, scope="", project_root=None,
        )
        assert "pipeline_figure" not in prompt
    for stage in ("paper", "review"):
        prompt = render_role_prompt_fragment(
            role="engineer", operation="narrative_edit", stage=stage, scope="",
            project_root=None,
        )
        assert "pipeline_figure" not in prompt
        assert "Fresh-context Narrative Editor" in prompt


@pytest.fixture
def browser_factory():
    browser_api = pytest.importorskip("playwright.sync_api")
    return browser_api.sync_playwright


@pytest.fixture
def browser_page(browser_factory):
    with browser_factory() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("optional Chromium is not installed")
        browser = p.chromium.launch(headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


@pytest.mark.integration
def test_real_browser_detects_font_substitution(browser_page) -> None:
    browser_page.set_content('<svg><g id="pipeline-content"><text style="font-family:monospace">ABC</text></g></svg>')
    with pytest.raises(ValueError, match="browser used"):
        pipeline._check_actual_fonts(browser_page)


def _require_times(page) -> None:
    page.set_content('<svg><g id="pipeline-content"><text style="font-family:Times New Roman">ABC</text></g></svg>')
    try:
        pipeline._check_actual_fonts(page)
    except ValueError:
        pytest.skip("Times New Roman is not installed")


@pytest.fixture
def times_font(browser_factory):
    # Close the probe's sync context before the renderer opens its own browser.
    with browser_factory() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("optional Chromium is not installed")
        browser = p.chromium.launch(headless=True)
        try:
            _require_times(browser.new_page())
        finally:
            browser.close()


@pytest.mark.integration
def test_real_svg_pdf_png_exports_are_tight_editable_and_times(
    tmp_path: Path, times_font, capsys,
) -> None:
    source = tmp_path / "source.svg"
    source.write_text(DRAWING)
    output = tmp_path / "export.svg"
    assert pipeline.main([
        "render", "--input", str(source), "--output", str(output), "--pdf", "--png",
    ]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["fonts"] == ["Times New Roman"]
    assert info["width"] == 624
    assert info["height"] < 240  # original 900-high whitespace is removed
    x, y, width, height = map(float, info["viewBox"].split())
    assert (x, y, width, height) == (188, 88, 434, 154)
    assert source.read_text() == DRAWING
    root = ET.fromstring(output.read_text())
    labels = root.findall(f".//{{{pipeline.SVG_NS}}}text")
    assert len(labels) == 2
    assert all("Times New Roman" in e.get("style", "") for e in labels)
    assert output.with_suffix(".png").read_bytes().startswith(b"\x89PNG")
    from pypdf import PdfReader

    pdf = PdfReader(output.with_suffix(".pdf"))
    assert len(pdf.pages) == 1
    assert float(pdf.pages[0].mediabox.width) == pytest.approx(468, abs=1)
    assert "Input & code" in pdf.pages[0].extract_text()
    fonts = pdf.pages[0]["/Resources"]["/Font"].get_object()
    assert all("TimesNewRoman" in font.get_object()["/BaseFont"] for font in fonts.values())


@pytest.mark.integration
def test_tiny_labels_fail_without_replacing_existing_exports(tmp_path: Path, times_font) -> None:
    source = tmp_path / "source.svg"
    source.write_text(DRAWING.replace('font-size="26"', 'font-size="3"'))
    output = tmp_path / "export.svg"
    output.write_text("previous good SVG")
    with pytest.raises(ValueError, match="minimum 8 pt"):
        pipeline.render_pipeline(source, output)
    assert output.read_text() == "previous good SVG"


@pytest.mark.integration
def test_vertical_layout_requires_revision(tmp_path: Path, times_font) -> None:
    source = tmp_path / "source.svg"
    source.write_text(DRAWING.replace('x="440" y="140"', 'x="240" y="740"'))
    with pytest.raises(Exception, match="must be horizontal"):
        pipeline.render_pipeline(source, tmp_path / "out.svg")
