from __future__ import annotations

from pathlib import Path

from argus.verticals.argus_maintenance.architecture_audit import scan_repository


def test_architecture_audit_surfaces_candidates_without_calling_them_defects(
    tmp_path: Path,
) -> None:
    source = tmp_path / "argus" / "core" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from argus.verticals.research.tool import run\n"
        "GPU = 'B200'\n"
        "HOME = '/home/alice/work'\n"
        "DIGEST = '0123456789abcdef0123456789abcdef'\n"
        "def wrapper(value):\n"
        "    return run(value)\n"
        "def choose(a, b, c):\n"
        "    assert a is not None\n"
        "    try:\n"
        "        return a or b or c\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )
    test_source = tmp_path / "tests" / "test_sample.py"
    test_source.parent.mkdir()
    test_source.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    report = scan_repository(tmp_path)
    categories = report["counts"]["by_category"]

    assert categories["concrete_vertical_import"] == 1
    assert categories["domain_literal_outside_vertical"] == 1
    assert categories["fallback_chain"] == 1
    assert categories["hardcoded_digest"] == 1
    assert categories["machine_specific_path"] == 1
    assert categories["runtime_assert"] == 1
    assert categories["silent_broad_exception"] == 1
    assert categories["thin_wrapper"] == 1
    assert all(row["path"] != "tests/test_sample.py" for row in report["findings"])
