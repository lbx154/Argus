from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.literature_ledger import (
    MATRIX_PATH,
    main,
    render_lit_matrix,
    sync_literature_ledger,
    validate_literature_ledger,
)


def _paper(title: str, arxiv_id: str) -> dict:
    return {
        "title": title,
        "year": 2025,
        "venue": "arXiv",
        "arxiv_id": arxiv_id,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "retrieved_via": f"direct arXiv fetch {arxiv_id}",
        "raw_response": f"research/_search/{arxiv_id}.html",
        "method": "method",
        "implication": "project implication",
    }


def test_small_claim_complete_ledger_has_no_paper_count_gate() -> None:
    payload = {"recent_high_quality_papers": [_paper("One decisive paper", "2501.00001")]}

    assert validate_literature_ledger(payload) == []


def test_matrix_is_generated_from_recent_and_classic_groups() -> None:
    payload = {
        "recent_high_quality_papers": [_paper("Recent", "2501.00001")],
        "classic_papers": [_paper("Classic", "1701.00001")],
        "benchmark_papers": [_paper("Benchmark", "2301.00001")],
    }

    matrix = render_lit_matrix(payload)

    assert matrix.count("\n") == 4
    assert matrix.splitlines()[0].startswith("id\tkey\tcategory")
    assert "\trelevance\tretrieved_via\traw_source" in matrix.splitlines()[0]
    assert "2501.00001\t2501.00001\trecent\tRecent" in matrix
    assert "1701.00001\t1701.00001\tclassic\tClassic" in matrix
    assert "2301.00001\t2301.00001\tbenchmark\tBenchmark" in matrix


def test_flat_paper_shape_preserves_category_flags() -> None:
    classic = _paper("Classic", "1701.00001")
    classic["classic_anchor"] = True
    recent = _paper("Recent", "2501.00001")
    recent["recent_high_quality"] = True

    matrix = render_lit_matrix({"papers": [classic, recent]})

    assert "1701.00001\t1701.00001\tclassic\tClassic" in matrix
    assert "2501.00001\t2501.00001\trecent\tRecent" in matrix


def test_duplicate_source_identity_is_rejected() -> None:
    paper = _paper("Same", "2501.00001")

    issues = validate_literature_ledger({"papers": [paper, dict(paper)]})

    assert [issue.code for issue in issues] == ["paper_duplicate"]


def test_duplicate_doi_is_rejected_even_with_different_display_keys() -> None:
    first = _paper("Same source A", "2501.00001")
    first.update({"key": "first", "doi": "10.1000/example"})
    second = _paper("Same source B", "2501.00002")
    second.update({"key": "second", "doi": "https://doi.org/10.1000/example"})

    issues = validate_literature_ledger({"papers": [first, second]})

    assert [issue.code for issue in issues] == ["paper_duplicate"]


def test_duplicate_arxiv_is_rejected_when_only_one_record_has_doi() -> None:
    first = _paper("Same source A", "2501.00001")
    first["doi"] = "10.1000/example"
    second = _paper("Same source B", "2501.00001")
    second.pop("doi", None)

    issues = validate_literature_ledger({"papers": [first, second]})

    assert [issue.code for issue in issues] == ["paper_duplicate"]


def test_provenance_and_project_relevance_are_required() -> None:
    paper = _paper("Ungrounded", "2501.00001")
    paper.pop("retrieved_via")
    paper.pop("raw_response")
    paper.pop("implication")

    codes = {issue.code for issue in validate_literature_ledger({"papers": [paper]})}

    assert codes == {
        "provenance_missing",
        "raw_source_missing",
        "relevance_missing",
    }


def test_sync_is_idempotent_and_does_not_churn_matrix_mtime(tmp_path: Path) -> None:
    ledger = tmp_path / "research" / "LITERATURE_GROUNDING.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"papers": [_paper("Recent", "2501.00001")]}),
        encoding="utf-8",
    )

    changed, issues = sync_literature_ledger(tmp_path)
    assert changed is True
    assert issues == []
    matrix = tmp_path / MATRIX_PATH
    before = matrix.stat().st_mtime_ns

    changed, issues = sync_literature_ledger(tmp_path)
    assert changed is False
    assert issues == []
    assert matrix.stat().st_mtime_ns == before


def test_cli_sync_writes_matrix(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "research" / "LITERATURE_GROUNDING.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"papers": [_paper("Recent", "2501.00001")]}),
        encoding="utf-8",
    )

    assert main(["sync", "--project-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["papers"] == 1
    assert (tmp_path / MATRIX_PATH).exists()
