"""Deterministic figure checks: the defects a script can catch before a reader does."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.verticals.research import figure_lint as mod

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _draw(path: Path, *, fonttype: int) -> None:
    with plt.rc_context({"pdf.fonttype": fonttype}):
        fig, ax = plt.subplots(figsize=(3, 2))
        ax.plot([0, 1], [0, 1], label="ours")
        ax.set_title("relative error")
        ax.legend()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path)
        plt.close(fig)


def _paper(root: Path, body: str) -> None:
    paper = root / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\usepackage{graphicx}\\graphicspath{{figures/}}\n"
        "\\begin{document}\n" + body + "\n\\end{document}\n",
        encoding="utf-8",
    )


def test_missing_and_type3_figures_are_reported(tmp_path: Path) -> None:
    _draw(tmp_path / "paper" / "figures" / "plain.pdf", fonttype=3)
    _draw(tmp_path / "paper" / "figures" / "styled.pdf", fonttype=42)
    _paper(
        tmp_path,
        "\\includegraphics[width=\\linewidth]{plain}\n"
        "\\includegraphics{figures/styled.pdf}\n"
        "% \\includegraphics{commented_out}\n"
        "\\includegraphics{absent}\n",
    )
    issues = mod.figure_lint_issues(tmp_path)
    assert any("plain.pdf` embeds Type 3 fonts" in issue for issue in issues)
    assert any("`absent` is included by the manuscript but the file is missing" in issue for issue in issues)
    assert not any("styled" in issue for issue in issues)
    assert not any("commented_out" in issue for issue in issues)


def test_raster_matplotlib_exports_are_reported(tmp_path: Path) -> None:
    png = tmp_path / "paper" / "figures" / "curve.png"
    png.parent.mkdir(parents=True)
    fig, ax = plt.subplots(figsize=(2, 2))
    ax.plot([0, 1])
    fig.savefig(png)  # matplotlib stamps Software=Matplotlib into the PNG
    plt.close(fig)
    _paper(tmp_path, "\\includegraphics{curve.png}")
    issues = mod.figure_lint_issues(tmp_path)
    assert any("raster export from matplotlib" in issue for issue in issues)


def test_plot_scripts_without_the_shared_style_helper_are_reported(tmp_path: Path) -> None:
    _paper(tmp_path, "no figures")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "plain.py").write_text(
        "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('x.pdf')\n",
        encoding="utf-8",
    )
    (scripts / "styled.py").write_text(
        "from paper_chart_style import set_pub_style\nimport matplotlib.pyplot as plt\n"
        "set_pub_style()\nplt.savefig('y.pdf')\n",
        encoding="utf-8",
    )
    (scripts / "analysis.py").write_text("import json\n", encoding="utf-8")
    hidden = tmp_path / ".venv" / "lib"
    hidden.mkdir(parents=True)
    (hidden / "vendored.py").write_text("import matplotlib\nsavefig(\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_plot.py").write_text("import matplotlib\nsavefig(\n", encoding="utf-8")
    issues = mod.figure_lint_issues(tmp_path)
    assert len(issues) == 1
    assert "`scripts/plain.py` saves matplotlib figures without the shared paper_chart_style" in issues[0]


def test_lint_is_silent_without_a_manuscript(tmp_path: Path) -> None:
    assert mod.figure_lint_issues(tmp_path) == ()


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert mod.main(["--project-root", str(tmp_path)]) == 0
    _paper(tmp_path, "\\includegraphics{absent}")
    assert mod.main(["--project-root", str(tmp_path)]) == 1
    assert "absent" in capsys.readouterr().err


def test_reference_clones_and_virtualenvs_are_not_linted(tmp_path: Path) -> None:
    # The lint is about this project's figures; a pinned clone under
    # third_party/ and the interpreter's own packages are someone else's code.
    for rel in ("third_party/ref/plot.py", ".venv/lib/python3.12/site-packages/x/plot.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        path.write_text("import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('a.pdf')\n", encoding="utf-8")
    own = tmp_path / "src" / "plot.py"
    own.parent.mkdir()
    own.write_text("import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('a.pdf')\n", encoding="utf-8")
    _paper(tmp_path, "text without figures")

    issues = mod.figure_lint_issues(tmp_path)

    assert all("third_party" not in issue and ".venv" not in issue for issue in issues)
    assert any("src/plot.py" in issue for issue in issues)


def test_box_and_arrow_diagrams_drawn_in_matplotlib_are_reported(tmp_path: Path) -> None:
    diagram = tmp_path / "scripts" / "architecture.py"
    diagram.parent.mkdir()
    diagram.write_text(
        "import matplotlib.pyplot as plt\nfrom matplotlib.patches import FancyBboxPatch\n"
        "fig, ax = plt.subplots()\n"
        + "".join(f"ax.add_patch(FancyBboxPatch(({i}, 0), 1, 1))\nax.text({i}, 0.5, 'box {i}')\nax.annotate('a', ({i}, 0))\n" for i in range(4))
        + "fig.savefig('architecture.pdf')\n",
        encoding="utf-8",
    )
    chart = tmp_path / "scripts" / "results.py"
    chart.write_text(
        "import matplotlib.pyplot as plt\nfrom matplotlib.patches import Rectangle\n"
        "fig, ax = plt.subplots()\nax.plot([1, 2], [3, 4])\n"
        + "".join(f"ax.add_patch(Rectangle(({i}, 0), 1, 1))\nax.text({i}, 0.5, 'x')\nax.annotate('a', ({i}, 0))\n" for i in range(4))
        + "fig.savefig('results.pdf')\n",
        encoding="utf-8",
    )
    _paper(tmp_path, "text")

    issues = mod.figure_lint_issues(tmp_path)

    assert any("scripts/architecture.py" in i and "box-and-arrow diagram" in i for i in issues)
    assert not any("scripts/results.py" in i and "box-and-arrow diagram" in i for i in issues)


def test_method_figure_needs_a_native_ppt_source_and_not_a_matplotlib_export(tmp_path: Path) -> None:
    # The Engineer that drew the architecture figure with matplotlib patches
    # had run `ppt_master status` (ready) minutes earlier. Two facts from the
    # tree catch it: the export's producer and the missing editable PPTX.
    _draw(tmp_path / "paper" / "figures" / "fig1_mechanism.pdf", fonttype=42)
    _draw(tmp_path / "paper" / "figures" / "fig2_results.pdf", fonttype=42)
    _paper(
        tmp_path,
        "\\begin{figure}\\includegraphics{fig1_mechanism}\\caption{Overview of the PBIS architecture.}\\label{fig:arch}\\end{figure}\n"
        "\\begin{figure}\\includegraphics{fig2_results}\\caption{Accuracy against budget.}\\end{figure}\n",
    )

    issues = mod.figure_lint_issues(tmp_path)

    assert any("method figure `fig1_mechanism` was exported by matplotlib" in i for i in issues)
    assert any("method figure `fig1_mechanism` has no editable PPT Master source" in i and "fig1_mechanism.pptx" in i for i in issues)
    assert not any("fig2_results" in i and "method figure" in i for i in issues)

    (tmp_path / "paper" / "figures" / "fig1_mechanism.pptx").write_bytes(b"PK")
    issues = mod.figure_lint_issues(tmp_path)
    assert not any("has no editable PPT Master source" in i for i in issues)
    assert any("was exported by matplotlib" in i for i in issues)  # the export itself is still wrong


def test_first_figure_is_the_method_figure_when_no_caption_says_so(tmp_path: Path) -> None:
    _draw(tmp_path / "paper" / "figures" / "teaser.pdf", fonttype=42)
    _paper(tmp_path, "\\begin{figure}\\includegraphics{teaser}\\caption{Our contribution at a glance.}\\end{figure}\n")

    figures = mod.method_figures(tmp_path / "paper")

    assert [raw for raw, _resolved, _caption in figures] == ["teaser"]


def _fake_pptx(path: Path, *, shapes: int, custom_paths: int, text: str) -> None:
    import zipfile

    body = "".join(
        f"<p:sp><p:txBody><a:p><a:r><a:t>{word}</a:t></a:r></a:p></p:txBody>"
        + ("<a:custGeom><a:pathLst/></a:custGeom>" if k < custom_paths else "")
        + "</p:sp>"
        for k, word in enumerate((text.split() + ["box"] * shapes)[:shapes])
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", f"<p:sld>{body}</p:sld>")


def test_a_companion_pptx_that_did_not_produce_the_export_is_reported(tmp_path: Path) -> None:
    # Thirteen preset rectangles beside a PDF full of drawn curves: the PDF came
    # from somewhere else and the stem rule was met in form only.
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    fig, ax = plt.subplots(figsize=(4, 2))
    for k in range(40):
        ax.plot(range(50), [((i * k) % 7) for i in range(50)])
    ax.set_title("Radial quadrature versus Monte Carlo sampling on the sphere")
    fig.savefig(figures / "fig1_mechanism.pdf")
    plt.close(fig)
    _fake_pptx(figures / "fig1_mechanism.pptx", shapes=13, custom_paths=0, text="Radial quadrature versus Monte Carlo sampling sphere")
    _paper(tmp_path, "\\begin{figure}\\includegraphics{fig1_mechanism}\\caption{Mechanism overview.}\\end{figure}")

    issues = mod.figure_lint_issues(tmp_path)

    assert any("was not exported from this PPTX" in i and "13 preset shapes and no drawn path" in i for i in issues)

    _fake_pptx(figures / "fig1_mechanism.pptx", shapes=60, custom_paths=40, text="Radial quadrature versus Monte Carlo sampling sphere")
    issues = mod.figure_lint_issues(tmp_path)
    assert not any("was not exported from this PPTX" in i for i in issues)


def test_an_export_whose_words_are_not_in_the_pptx_is_reported(tmp_path: Path) -> None:
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    fig, ax = plt.subplots(figsize=(4, 2))
    ax.text(0.1, 0.5, "Gauss Laguerre radial nodes Stiefel frames Gegenbauer cancellation feature map")
    fig.savefig(figures / "overview.pdf")
    plt.close(fig)
    _fake_pptx(figures / "overview.pptx", shapes=30, custom_paths=10, text="Completely different words about another topic entirely")
    _paper(tmp_path, "\\begin{figure}\\includegraphics{overview}\\caption{Architecture.}\\end{figure}")

    issues = mod.figure_lint_issues(tmp_path)

    assert any("shares only" in i and "the export and the editable source show different figures" in i for i in issues)


def test_a_tex_compiled_method_figure_beside_a_look_alike_pptx_is_reported(tmp_path: Path) -> None:
    # One paper's framework figure was a TikZ standalone compiled by pdfTeX;
    # a python-pptx companion of the same stem carried the same labels and
    # enough shapes to pass the fidelity comparison. The producer settles it.
    from pypdf import PdfWriter

    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=200)
    writer.add_metadata({"/Producer": "pdfTeX-1.40.25", "/Creator": "TeX"})
    with (figures / "framework.pdf").open("wb") as handle:
        writer.write(handle)
    _fake_pptx(figures / "framework.pptx", shapes=30, custom_paths=10, text="offline rotation quantizer residual")
    _paper(tmp_path, "\\begin{figure}\\includegraphics{framework}\\caption{Overview of the architecture.}\\end{figure}")

    issues = mod.figure_lint_issues(tmp_path)

    assert any("was produced by pdfTeX-1.40.25, which no PPTX export chain produces" in i for i in issues)
    assert not any("was exported by matplotlib" in i for i in issues)


def test_a_pdf_beside_the_pptx_must_carry_an_export_producer(tmp_path: Path) -> None:
    # Six PPTX-paired method figures on one machine: producers pdfTeX, cairo,
    # Ghostscript, none from the PPTX. The exporter renders through Chromium
    # (Skia/PDF); Office suites are the other legitimate producers.
    from pypdf import PdfWriter

    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    _fake_pptx(figures / "overview.pptx", shapes=30, custom_paths=10, text="rotation quantizer residual stream")
    _paper(tmp_path, "\\begin{figure}\\includegraphics{overview}\\caption{Overview of the mechanism.}\\end{figure}")

    for producer, expected in (("GPL Ghostscript 10.02.1", True), ("cairo 1.18.0", True), ("Skia/PDF m151", False), ("LibreOffice 24.2", False)):
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=200)
        writer.add_metadata({"/Producer": producer})
        with (figures / "overview.pdf").open("wb") as handle:
            writer.write(handle)
        issues = mod.figure_lint_issues(tmp_path)
        flagged = any("was not exported from `overview.pptx`" in i and "pptx_export.py --pptx paper/figures/overview.pptx" in i for i in issues)
        assert flagged is expected, (producer, issues)


def test_hand_drawing_choices_are_named_as_facts(tmp_path: Path) -> None:
    _paper(tmp_path, "no figures")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "by_hand.py").write_text(
        "from paper_chart_style import set_pub_style\nimport matplotlib.pyplot as plt\n"
        "PALETTE = {'a': '#0173B2', 'b': '#DE8F05', 'c': '#029E73'}\n"
        "fig, ax = plt.subplots()\nax.bar([0, 1], [90, 91], color=PALETTE['a'])\n"
        "ax.set_ylim(80, 95)\nax.legend(frameon=True, facecolor='white', loc='upper right')\n"
        "ax.set_title('Results')\nfig.savefig('x.pdf')\n",
        encoding="utf-8",
    )
    (scripts / "through_helper.py").write_text(
        "from paper_charts import bars, save\nimport matplotlib.pyplot as plt\n"
        "fig, ax = bars(['a'], {'x': [1.0]})\nfig.savefig('y.pdf')\n",
        encoding="utf-8",
    )
    issues = mod.figure_lint_issues(tmp_path)
    assert len(issues) == 1
    issue = issues[0]
    assert "`scripts/by_hand.py` draws by hand beside the paper_chart_style theme" in issue
    assert "sets 3 colours by hand" in issue
    assert "pins a framed legend inside the axes" in issue
    assert "starts a bar axis at 80 instead of zero" in issue
    assert "writes an in-plot title" in issue
    assert "through_helper" not in " ".join(issues)


def test_facts_recorded_by_the_helper_are_reported(tmp_path: Path) -> None:
    _paper(tmp_path, "no figures")
    src = tmp_path / "paper" / "figures" / "src" / "cut"
    src.mkdir(parents=True)
    (src / "facts.json").write_text(
        json.dumps(
            {
                "helper": "paper_charts",
                "figure": "cut",
                "panels": [
                    {"kind": "bars", "axis_from_zero": False, "truncated_reason": "all above 80", "legend": "inside"},
                    {"kind": "lines", "legend": "above"},
                ],
            }
        ),
        encoding="utf-8",
    )
    issues = mod.figure_lint_issues(tmp_path)
    assert any("figure `cut` has bars that do not start at zero (reason recorded: all above 80)" in i for i in issues)
    assert any("figure `cut` places its legend inside the axes" in i for i in issues)
    assert len(issues) == 2
