from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from argus_skill.verticals.research.figure_provenance import (
    FIGURE_PROVENANCE_PATH,
    register_figure,
    validate_figure_provenance,
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


def test_register_and_validate_renderer_neutral_figure(tmp_path: Path) -> None:
    source = tmp_path / "paper" / "figures" / "src" / "chart.py"
    output = tmp_path / "paper" / "figures" / "chart.svg"
    data = tmp_path / "paper" / "artifacts" / "results.tsv"
    review = tmp_path / "paper" / "figures" / "chart.review.md"
    metadata = output.with_suffix(".svg.render.json")
    for path, content in (
        (source, "print('chart')\n"),
        (output, "<svg viewBox='0 0 100 100'/>\n"),
        (data, "method\tvalue\nours\t1\n"),
        (review, "PASS\n"),
        (metadata, "{}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    entry = register_figure(
        project_root=tmp_path,
        figure_id="main_result",
        role="data",
        renderer="echarts-svg-ssr",
        source_path=source,
        output_path=output,
        inputs=[data],
        review_path=review,
        render_metadata_path=metadata,
        command="node render.mjs",
    )
    report = validate_figure_provenance(tmp_path)

    assert entry["renderer"] == "echarts-svg-ssr"
    assert report.ok
    assert report.output_paths == {"paper/figures/chart.svg"}
    payload = json.loads((tmp_path / FIGURE_PROVENANCE_PATH).read_text())
    assert payload["schema_version"] == 1


def test_validate_detects_output_hash_drift(tmp_path: Path) -> None:
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
    assert any(issue.code == "hash_mismatch" for issue in report.issues)


def test_register_rejects_path_outside_project(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.svg"
    inside = tmp_path / "inside.svg"
    outside.write_text("<svg/>\n", encoding="utf-8")
    inside.write_text("<svg/>\n", encoding="utf-8")

    try:
        register_figure(
            project_root=tmp_path,
            figure_id="escape",
            role="method",
            renderer="svg",
            source_path=outside,
            output_path=inside,
            **_supporting_records(tmp_path),
        )
    except ValueError as exc:
        assert "escapes project root" in str(exc)
    else:
        raise AssertionError("outside source path was accepted")


def test_validate_reports_manifest_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-figure-manifest.json"
    outside.write_text('{"schema_version": 1, "figures": []}\n', encoding="utf-8")
    manifest = tmp_path / FIGURE_PROVENANCE_PATH
    manifest.parent.mkdir(parents=True)
    manifest.symlink_to(outside)

    report = validate_figure_provenance(tmp_path)

    assert not report.ok
    assert report.issues[0].code == "path_escape"


def test_concurrent_registration_preserves_every_entry(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    output = tmp_path / "figure.svg"
    source.write_text("source\n", encoding="utf-8")
    output.write_text("<svg viewBox='0 0 1 1'/>\n", encoding="utf-8")
    support = _supporting_records(tmp_path)

    def register(index: int) -> None:
        register_figure(
            project_root=tmp_path,
            figure_id=f"figure-{index}",
            role="data",
            renderer="svg",
            source_path=source,
            output_path=output,
            **support,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(register, range(16)))

    report = validate_figure_provenance(tmp_path)
    assert report.ok
    assert {str(entry["figure_id"]) for entry in report.entries} == {
        f"figure-{index}" for index in range(16)
    }


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
