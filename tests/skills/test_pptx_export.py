"""The export step of Method D/B: PPTX in, PDF and PNG out, through PPT Master and the browser.

No machine in this fleet has PowerPoint or LibreOffice, so until this script
existed every "exported from the PPTX" PDF had actually come from TikZ, cairo
or Ghostscript. The tests stub the two external steps (pptx_to_svg and the
browser) and check the page, the sizes, the outputs and the provenance.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "argus" / "verticals" / "research" / "skills" / "engineer" / "figure_spec_scripts" / "pptx_export.py"
)

SLIDE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="960" height="432" viewBox="0 0 960 432">'
    '<g id="shape-2"><rect x="10" y="10" width="300" height="200"/><text x="20" y="40">Rotation</text></g></svg>'
)


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("pptx_export", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_slide_size_comes_from_the_svg_root(mod) -> None:
    assert mod.slide_size_px(SLIDE_SVG) == (960.0, 432.0)
    assert mod.slide_size_px('<svg width="10in" height="5in"><g/></svg>') == (10.0, 5.0)
    with pytest.raises(ValueError):
        mod.slide_size_px("<html/>")


def test_export_writes_pdf_png_page_and_provenance_from_the_pptx(mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    (figures / "fig1_method.pptx").write_bytes(b"PK stub")

    def fake_pptx_to_svg(pptx: Path, out_dir: Path) -> list[Path]:
        assert pptx == figures / "fig1_method.pptx"
        (out_dir / "svg").mkdir(parents=True)
        first = out_dir / "svg" / "slide_01.svg"
        first.write_text(SLIDE_SVG, encoding="utf-8")
        (out_dir / "svg" / "slide_02.svg").write_text(SLIDE_SVG.replace("Rotation", "Second"), encoding="utf-8")
        return [first, out_dir / "svg" / "slide_02.svg"]

    calls: list[list[str]] = []

    class _Renderer:
        @staticmethod
        def main(argv: list[str]) -> int:
            calls.append(argv)
            output = Path(argv[argv.index("--output") + 1])
            output.write_bytes(b"%PDF-1.4 stub" if output.suffix == ".pdf" else b"\x89PNG stub")
            output.with_name(output.name + ".render.json").write_text("{}", encoding="utf-8")
            return 0

    monkeypatch.setattr(mod, "_run_pptx_to_svg", fake_pptx_to_svg)
    monkeypatch.setattr(mod, "_load_browser_render", lambda: _Renderer)

    written = mod.export_pptx("paper/figures/fig1_method.pptx", project_root=tmp_path, png_width_mm=170)

    assert Path(written["pdf"]) == figures / "fig1_method.pdf" and (figures / "fig1_method.pdf").is_file()
    assert Path(written["png"]) == figures / "fig1_method.png" and (figures / "fig1_method.png").is_file()
    assert written["slides"] == 2 and written["slide"] == 1
    assert (written["width_mm"], written["height_mm"]) == (254.0, 114.3)
    page = (tmp_path / "paper" / "figures" / "src" / "fig1_method" / "index.html").read_text(encoding="utf-8")
    assert 'data-figure-root data-figure-ready="true"' in page and "Rotation" in page and "Second" not in page
    assert (tmp_path / "paper" / "figures" / "src" / "fig1_method" / "slide.svg").read_text(encoding="utf-8").startswith("<svg")
    # Both renders use the slide's own pixel size; the PNG is scaled so that
    # 170 mm of manuscript width comes out at PNG_DPI.
    assert [Path(c[c.index("--output") + 1]).suffix for c in calls] == [".pdf", ".png"]
    assert all(c[c.index("--width") + 1] == "960" and c[c.index("--height") + 1] == "432" for c in calls)
    scale = float(calls[1][calls[1].index("--device-scale-factor") + 1])
    assert scale == round((170 / 25.4 * mod.PNG_DPI) / 960, 2)
    manifest = json.loads((figures / "FIGURE_PROVENANCE.json").read_text(encoding="utf-8"))
    entry = manifest["figures"][0]
    assert entry["figure_id"] == "fig1_method" and entry["renderer"] == "pptx-browser"
    assert entry["output_path"] == "paper/figures/fig1_method.pdf"
    assert entry["command"] == "pptx_export.py --pptx paper/figures/fig1_method.pptx"


def test_slide_out_of_range_and_missing_pptx_are_errors(mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        mod.export_pptx("paper/figures/absent.pptx", project_root=tmp_path)
    (figures / "one.pptx").write_bytes(b"PK stub")

    def one_slide(pptx: Path, out_dir: Path) -> list[Path]:
        (out_dir / "svg").mkdir(parents=True)
        path = out_dir / "svg" / "slide_01.svg"
        path.write_text(SLIDE_SVG, encoding="utf-8")
        return [path]

    monkeypatch.setattr(mod, "_run_pptx_to_svg", one_slide)
    with pytest.raises(ValueError, match="has 1 slide"):
        mod.export_pptx("paper/figures/one.pptx", project_root=tmp_path, slide=2)


def test_ppt_master_script_is_located_from_an_override(mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ppt-master"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "pptx_to_svg.py").write_text("print('stub')\n", encoding="utf-8")
    monkeypatch.setenv("ARGUS_PPT_MASTER_SKILL_ROOT", str(root))
    assert mod._ppt_master_script() == root / "scripts" / "pptx_to_svg.py"
