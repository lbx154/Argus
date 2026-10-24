from __future__ import annotations

import json
import os
from pathlib import Path

from argus.verticals.research.figure_provenance import (
    FIGURE_PROVENANCE_PATH,
    _normalized_resolved_path,
    register_figure,
    validate_figure_provenance,
)


def test_windows_extended_path_prefix_is_normalized() -> None:
    if os.name != "nt":
        return
    assert _normalized_resolved_path(Path(r"\\?\C:\work\figure.svg")) == Path(
        r"C:\work\figure.svg"
    )


def _supporting_records(root: Path) -> dict[str, object]:
    input_path = root / "input.json"
    review_path = root / "review.md"
    metadata_path = root / "render.json"
    input_path.write_text("{}\n", encoding="utf-8")
    review_path.write_text("reviewed\n", encoding="utf-8")
    metadata_path.write_text("{}\n", encoding="utf-8")
    return {
        "inputs": [input_path],
        "review_path": review_path,
        "render_metadata_path": metadata_path,
        "command": "render figure",
    }


def test_validate_detects_a_changed_output(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    output = tmp_path / "figure.svg"
    source.write_text("source\n", encoding="utf-8")
    output.write_text("<svg viewBox='0 0 1 1'/>\n", encoding="utf-8")
    support = _supporting_records(tmp_path)
    register_figure(
        project_root=tmp_path,
        figure_id="figure",
        role="method",
        renderer="figure-spec",
        source_path=source,
        output_path=output,
        **support,
    )
    output.write_text("<svg viewBox='0 0 2 2'/>\n", encoding="utf-8")

    report = validate_figure_provenance(tmp_path)

    assert not report.ok
    assert any(issue.code == "file_changed" for issue in report.issues)


def test_validate_reports_manifest_symlink_escape(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    outside = tmp_path.parent / "outside-figure-manifest.json"
    outside.write_text('{"schema_version": 1, "figures": []}\n', encoding="utf-8")
    manifest = tmp_path / FIGURE_PROVENANCE_PATH
    manifest.parent.mkdir(parents=True)
    manifest.symlink_to(outside)

    report = validate_figure_provenance(tmp_path)

    assert not report.ok
    assert report.issues[0].code == "path_escape"


def test_validate_reports_non_list_inputs(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    output = tmp_path / "figure.svg"
    source.write_text("source\n", encoding="utf-8")
    output.write_text("<svg viewBox='0 0 1 1'/>\n", encoding="utf-8")
    register_figure(
        project_root=tmp_path,
        figure_id="figure",
        role="data",
        renderer="svg",
        source_path=source,
        output_path=output,
        **_supporting_records(tmp_path),
    )
    manifest = tmp_path / FIGURE_PROVENANCE_PATH
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["figures"][0]["inputs"] = True
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_figure_provenance(tmp_path)

    assert not report.ok
    assert any(issue.code == "invalid_inputs" for issue in report.issues)
