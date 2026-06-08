"""Regression: page snapshots must be ordered by numeric page, not lexically."""
from __future__ import annotations

from pathlib import Path

from argus_skill.skills.paper_layout_review import (
    _collect_page_snapshots,
    _page_number_from_snapshot,
)


def test_page_number_parsed_from_name():
    assert _page_number_from_snapshot(Path("page-1.png")) == 1
    assert _page_number_from_snapshot(Path("page-10.png")) == 10
    assert _page_number_from_snapshot(Path("page-02.png")) == 2


def test_snapshots_ordered_numerically(tmp_path: Path):
    out = tmp_path / "pages"
    out.mkdir()
    # Unpadded names: lexicographic sort would put page-10 before page-2.
    for n in (1, 2, 3, 10, 11, 12):
        (out / f"page-{n}.png").write_bytes(b"\x89PNG")

    snaps = _collect_page_snapshots(tmp_path, out, renderer="pdftoppm")

    assert [s["page"] for s in snaps] == [1, 2, 3, 4, 5, 6]
    # The 4th snapshot must correspond to page-10.png, not page-12.png.
    assert snaps[3]["path"].endswith("page-10.png")
    assert snaps[-1]["path"].endswith("page-12.png")
