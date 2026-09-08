from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from argus_skill.verticals.research.academic_figure import FigureCanvas


def test_publication_canvas_exports_readable_math_as_vectors(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    canvas = FigureCanvas(width=396, height=120)
    canvas.panel(10, 12, "a", "Exact verification")
    canvas.box(12, 40, 145, 42, "Candidate set\n" + r"$C_t = \{i : U_i \geq L_{q_t}\}$")
    canvas.arrow([(157, 61), (216, 61)])
    canvas.box(216, 40, 155, 42, "Winner separation\n" + r"$L_j > \max_{i\ne j} U_i$")
    outputs = canvas.export(tmp_path / "method")
    with pymupdf.open(outputs["pdf"]) as pdf:
        assert pdf[0].rect.width == pytest.approx(396)
        assert pdf[0].rect.height == pytest.approx(120)
        assert pdf[0].get_images() == []
        assert "Candidate set" in pdf[0].get_text()
    assert "<text" in outputs["svg"].read_text()
    assert outputs["png"].read_bytes().startswith(b"\x89PNG")


def test_crowded_labels_fail_before_replacing_good_exports(tmp_path: Path) -> None:
    canvas = FigureCanvas(width=200, height=100)
    canvas.box(80, 20, 35, 25, "This label does not fit")
    existing = tmp_path / "method.pdf"
    existing.write_bytes(b"previous figure")
    with pytest.raises(ValueError, match="enlarge the box"):
        canvas.export(tmp_path / "method")
    assert existing.read_bytes() == b"previous figure"


def test_canvas_rejects_clipped_and_tiny_type() -> None:
    canvas = FigureCanvas(width=200, height=100)
    with pytest.raises(ValueError, match="8 pt"):
        canvas.text(10, 10, "tiny", size=7)
    canvas.text(195, 40, "clipped")
    with pytest.raises(ValueError, match="leaves the canvas"):
        canvas.validate()


def test_publication_labels_cannot_overprint_each_other() -> None:
    canvas = FigureCanvas(width=200, height=100)
    canvas.text(20, 40, "Large set")
    canvas.text(42, 40, "Unresolved")
    with pytest.raises(ValueError, match="labels overlap"):
        canvas.validate()
