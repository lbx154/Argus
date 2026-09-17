"""Deterministic figure checks: the defects a script can catch before a reader does."""
from __future__ import annotations

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
    assert not any("styled" in issue and "Type 3" in issue for issue in issues)
    # Any export by a plotting library is off-route now, styled or not.
    assert any("styled.pdf` was exported by matplotlib, which is not a figure route" in issue for issue in issues)
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


def test_plot_scripts_are_off_route_wherever_they_live(tmp_path: Path) -> None:
    # Every script that saves matplotlib figures outside tests/ and the
    # interpreter's own directories is off-route; a style import saves nothing.
    _paper(tmp_path, "no figures")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "plain.py").write_text(
        "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('x.pdf')\n",
        encoding="utf-8",
    )
    (scripts / "styled.py").write_text(
        "from some_style import set_pub_style\nimport matplotlib.pyplot as plt\n"
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
    assert len(issues) == 2
    assert all("is not a figure route in this vertical" in issue for issue in issues)
    assert {issue.split("`")[1] for issue in issues} == {"scripts/plain.py", "scripts/styled.py"}
    assert all("echarts_figure.py" in issue and "PPT Master" in issue for issue in issues)


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

    assert any("fig1_mechanism.pdf` was exported by matplotlib, which is not a figure route" in i for i in issues)
    assert any("method figure `fig1_mechanism` has no editable PPT Master source" in i and "fig1_mechanism.pptx" in i for i in issues)
    assert not any("fig2_results" in i and "has no editable PPT Master source" in i for i in issues)

    (tmp_path / "paper" / "figures" / "fig1_mechanism.pptx").write_bytes(b"PK")
    issues = mod.figure_lint_issues(tmp_path)
    assert not any("has no editable PPT Master source" in i for i in issues)
    assert any("fig1_mechanism.pdf` was exported by matplotlib" in i for i in issues)  # the export itself is still wrong


def test_first_figure_is_the_method_figure_when_no_caption_says_so(tmp_path: Path) -> None:
    _draw(tmp_path / "paper" / "figures" / "teaser.pdf", fonttype=42)
    _paper(tmp_path, "\\begin{figure}\\includegraphics{teaser}\\caption{Our contribution at a glance.}\\end{figure}\n")

    figures = mod.method_figures(tmp_path / "paper")

    assert [raw for raw, _resolved, _caption in figures] == ["teaser"]
