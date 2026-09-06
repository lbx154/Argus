from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

import pytest

from argus_skill.verticals.medical.dossier import (
    build_target_disease_dossier,
    main,
    validate_dossier,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = "2026-08-10T12:00:00Z"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _build(root: Path) -> dict:
    return build_target_disease_dossier(
        root,
        target="EGFR",
        disease="non-small cell lung cancer",
        disease_aliases=("NSCLC",),
        population="biomarker-selected adults",
        pubmed_payload=_fixture("pubmed_esummary.json"),
        clinical_trials_payload=_fixture("clinical_trials_v2.json"),
        retrieved_at=NOW,
    )


def test_fixture_dossier_writes_auditable_artifacts(tmp_path: Path) -> None:
    result = _build(tmp_path)
    medical = tmp_path / "medical"

    assert result["evidence_count"] == 3
    assert result["usable_source_count"] == 2
    assert result["infrastructure_failure_count"] == 0
    expected = {
        "scope.json",
        "queries.jsonl",
        "evidence_history.jsonl",
        "evidence.jsonl",
        "evidence_matrix.csv",
        "target_disease_memo.md",
        "review.json",
    }
    assert expected <= {path.name for path in medical.iterdir() if path.is_file()}
    assert len(list((medical / "raw" / "pubmed").glob("*.json"))) == 1
    assert len(list((medical / "raw" / "clinical_trials").glob("*.json"))) == 1

    evidence = _jsonl(medical / "evidence.jsonl")
    assert [row["record_id"] for row in evidence] == [
        "pubmed:12345678",
        "pubmed:87654321",
        "clinical_trials:NCT01234567",
    ]
    for row in evidence:
        raw_path = tmp_path / row["raw_artifact"]
        assert row["raw_artifact"].startswith("medical/raw/")
        assert raw_path.is_file()
    queries = _jsonl(medical / "queries.jsonl")
    assert len(queries) == 2
    for row in queries:
        assert row["raw_artifacts"]
        assert all((tmp_path / path).is_file() for path in row["raw_artifacts"])
    matrix_header = (medical / "evidence_matrix.csv").read_text(
        encoding="utf-8"
    ).splitlines()[0]
    assert "scope_population" in matrix_header
    assert "registry_last_update" in matrix_header
    assert "raw_artifact" in matrix_header
    memo = (medical / "target_disease_memo.md").read_text(encoding="utf-8")
    assert "not diagnosis or treatment advice" in " ".join(memo.casefold().split())
    assert "Pending independent Argus review" in memo
    review = json.loads((medical / "review.json").read_text(encoding="utf-8"))
    assert review == {
        "schema_version": 1,
        "status": "pending_review",
        "certified": False,
    }
    assert validate_dossier(tmp_path) == ()


def test_update_retains_raw_query_and_evidence_history_without_current_duplicates(
    tmp_path: Path,
) -> None:
    _build(tmp_path)
    _build(tmp_path)
    medical = tmp_path / "medical"

    assert len(_jsonl(medical / "queries.jsonl")) == 4
    assert len(_jsonl(medical / "evidence_history.jsonl")) == 6
    assert len(_jsonl(medical / "evidence.jsonl")) == 3
    assert len(list((medical / "raw" / "pubmed").glob("*.json"))) == 2
    assert len(list((medical / "raw" / "clinical_trials").glob("*.json"))) == 2


def test_different_scope_cannot_mix_into_existing_dossier(tmp_path: Path) -> None:
    _build(tmp_path)

    with pytest.raises(ValueError, match="existing medical scope"):
        build_target_disease_dossier(
            tmp_path,
            target="KRAS",
            disease="pancreatic cancer",
            pubmed_payload=_fixture("pubmed_esummary.json"),
            retrieved_at=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("population", "pediatric patients"),
        ("date_from", "2025-01-01"),
        ("date_to", "2026-01-01"),
        ("decision_question", "Should this target advance to clinical validation?"),
        ("output_language", "zh-CN"),
        ("intervention_class", "small molecule"),
        ("disease_aliases", ("NSCLC", "lung adenocarcinoma")),
    ],
)
def test_material_scope_change_cannot_reuse_existing_history(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _build(tmp_path)
    changed_scope = {
        "target": "EGFR",
        "disease": "non-small cell lung cancer",
        "disease_aliases": ("NSCLC",),
        "population": "biomarker-selected adults",
        field: value,
    }

    with pytest.raises(ValueError, match="requested scope"):
        build_target_disease_dossier(
            tmp_path,
            **changed_scope,
            pubmed_payload=_fixture("pubmed_esummary.json"),
            retrieved_at=NOW,
        )


def test_transport_failures_are_persisted_but_not_counted_as_evidence(
    tmp_path: Path,
) -> None:
    def opener(_request, _timeout):
        raise URLError("offline")

    result = build_target_disease_dossier(
        tmp_path,
        target="EGFR",
        disease="non-small cell lung cancer",
        retrieved_at=NOW,
        live=True,
        opener=opener,
    )
    medical = tmp_path / "medical"

    assert result["evidence_count"] == 0
    assert result["usable_source_count"] == 0
    assert result["infrastructure_failure_count"] == 2
    assert _jsonl(medical / "evidence.jsonl") == []
    failures = _jsonl(medical / "infrastructure_failures.jsonl")
    assert {row["source_type"] for row in failures} == {
        "pubmed",
        "clinical_trials",
    }
    assert "evidence.jsonl contains no records" in validate_dossier(tmp_path)


def test_memo_keeps_cumulative_infrastructure_failure_count(tmp_path: Path) -> None:
    def opener(_request, _timeout):
        raise URLError("offline")

    build_target_disease_dossier(
        tmp_path,
        target="EGFR",
        disease="non-small cell lung cancer",
        disease_aliases=("NSCLC",),
        population="biomarker-selected adults",
        retrieved_at=NOW,
        live=True,
        opener=opener,
    )
    result = _build(tmp_path)

    assert result["new_infrastructure_failure_count"] == 0
    assert result["infrastructure_failure_count"] == 2
    memo = (tmp_path / "medical" / "target_disease_memo.md").read_text(
        encoding="utf-8"
    )
    assert "- Infrastructure failures: 2" in memo


def test_validator_rejects_missing_raw_artifact(tmp_path: Path) -> None:
    _build(tmp_path)
    medical = tmp_path / "medical"
    evidence = _jsonl(medical / "evidence.jsonl")
    missing = tmp_path / evidence[0]["raw_artifact"]
    missing.unlink()

    issues = validate_dossier(tmp_path)

    assert any("raw_artifact does not exist" in issue for issue in issues)


def test_validator_rejects_query_raw_symlink_outside_project(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    _build(tmp_path)
    medical = tmp_path / "medical"
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    link = medical / "raw" / "pubmed" / "outside-link.json"
    link.symlink_to(outside)
    queries = _jsonl(medical / "queries.jsonl")
    queries[0]["raw_artifacts"] = [link.relative_to(tmp_path).as_posix()]
    (medical / "queries.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in queries),
        encoding="utf-8",
    )

    issues = validate_dossier(tmp_path)

    assert any("raw_artifacts entry is outside project" in issue for issue in issues)


def test_validator_reports_missing_and_invalid_artifacts(tmp_path: Path) -> None:
    medical = tmp_path / "medical"
    medical.mkdir()
    (medical / "scope.json").write_text("not-json", encoding="utf-8")

    issues = validate_dossier(tmp_path)

    assert "scope.json is not valid JSON" in issues
    assert "missing queries.jsonl" in issues
    assert "missing evidence.jsonl" in issues
    assert "missing evidence_matrix.csv" in issues
    assert "missing target_disease_memo.md" in issues
    assert "missing review.json" in issues


def test_cli_builds_fixture_dossier(tmp_path: Path) -> None:
    rc = main(
        [
            "--project-root",
            str(tmp_path),
            "--target",
            "EGFR",
            "--disease",
            "non-small cell lung cancer",
            "--pubmed-fixture",
            str(FIXTURES / "pubmed_esummary.json"),
            "--clinical-trials-fixture",
            str(FIXTURES / "clinical_trials_v2.json"),
            "--retrieved-at",
            NOW,
        ]
    )

    assert rc == 0
    assert (tmp_path / "medical" / "target_disease_memo.md").is_file()
