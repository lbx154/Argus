"""Tests for argus_skill.skills.evidence_chain (F4)."""
from __future__ import annotations

from pathlib import Path

from argus_skill.skills.evidence_chain import (
    main as evidence_chain_main,
)
from argus_skill.skills.evidence_chain import (
    validate_evidence_chain,
)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _write_claims_tsv(root: Path, rows: list[dict[str, str]]) -> Path:
    cols = ["claim_id", "status", "claim", "evidence_1", "evidence_2", "evidence_3", "notes"]
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(row.get(c, "") for c in cols))
    path = root / "paper" / "claims_to_evidence.tsv"
    _write(path, "\n".join(lines) + "\n")
    return path


def _write_bundle(root: Path, bundle_rel: str, *, tainted: bool = False) -> None:
    bundle = root / bundle_rel
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "summary.tsv").write_text("row_kind\nfake\n", encoding="utf-8")
    build_info_lines = [
        "# Build Info",
        "- Source run id: fake",
        "- Source status: completed",
    ]
    if tainted:
        build_info_lines.append("TAINTED — DO NOT CITE AS PERFORMANCE.")
    (bundle / "BUILD_INFO.md").write_text(
        "\n".join(build_info_lines) + "\n", encoding="utf-8"
    )


def test_clean_chain_passes(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "benchmarks/evidence/clean-bundle")
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "demo claim",
                "evidence_1": "benchmarks/evidence/clean-bundle/summary.tsv",
            }
        ],
    )

    report = validate_evidence_chain(tmp_path)

    assert report.ok, [i.detail for i in report.issues]
    assert report.claims_checked == 1
    assert report.evidence_paths_checked == 1
    assert report.bundles_checked == 1


def test_missing_evidence_path_flagged(tmp_path: Path) -> None:
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "broken",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/does-not-exist/summary.tsv",
            }
        ],
    )

    report = validate_evidence_chain(tmp_path)

    assert not report.ok
    codes = {i.code for i in report.issues}
    assert "evidence_path_missing" in codes


def test_bundle_missing_build_info_flagged(tmp_path: Path) -> None:
    bundle = tmp_path / "benchmarks" / "evidence" / "no-build-info"
    bundle.mkdir(parents=True)
    (bundle / "summary.tsv").write_text("x\n", encoding="utf-8")
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "no-bi",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/no-build-info/summary.tsv",
            }
        ],
    )

    report = validate_evidence_chain(tmp_path)

    assert not report.ok
    codes = {i.code for i in report.issues}
    assert "bundle_missing_build_info" in codes


def test_tainted_bundle_blocks_current_evidence_claim(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "benchmarks/evidence/tainted-bundle", tainted=True)
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "cites-tainted",
                "status": "current_evidence",
                "claim": "should not cite tainted",
                "evidence_1": "benchmarks/evidence/tainted-bundle/summary.tsv",
            }
        ],
    )

    report = validate_evidence_chain(tmp_path)

    assert not report.ok
    codes = {i.code for i in report.issues}
    assert "tainted_bundle_cited" in codes


def test_tainted_bundle_ok_under_historical_status(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "benchmarks/evidence/tainted-bundle", tainted=True)
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "historical-ok",
                "status": "historical_only",
                "claim": "historical contrast — tainted citation allowed",
                "evidence_1": "benchmarks/evidence/tainted-bundle/summary.tsv",
            }
        ],
    )

    report = validate_evidence_chain(tmp_path)

    assert report.ok, [i.detail for i in report.issues]


def test_claim_with_no_evidence_flagged(tmp_path: Path) -> None:
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "empty",
                "status": "current_evidence",
                "claim": "no evidence cited",
            }
        ],
    )

    report = validate_evidence_chain(tmp_path)

    assert not report.ok
    codes = {i.code for i in report.issues}
    assert "claim_has_no_evidence" in codes


def test_missing_tsv_returns_single_issue(tmp_path: Path) -> None:
    report = validate_evidence_chain(tmp_path)

    assert not report.ok
    assert len(report.issues) == 1
    assert report.issues[0].code == "claims_tsv_missing"


def test_paper_artifact_path_does_not_require_build_info(tmp_path: Path) -> None:
    (tmp_path / "paper" / "artifacts").mkdir(parents=True)
    (tmp_path / "paper" / "artifacts" / "tb2_comparison.tsv").write_text(
        "row_id\ndemo\n", encoding="utf-8"
    )
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "artifact-only",
                "status": "current_evidence",
                "claim": "raw artifact, no bundle",
                "evidence_1": "paper/artifacts/tb2_comparison.tsv",
            }
        ],
    )

    report = validate_evidence_chain(tmp_path)

    assert report.ok, [i.detail for i in report.issues]
    # paper/artifacts/ is not a bundle, so bundles_checked stays at 0.
    assert report.bundles_checked == 0


def test_cli_exits_nonzero_on_broken_chain(tmp_path: Path, capsys) -> None:
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "broken",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/missing/summary.tsv",
            }
        ],
    )

    rc = evidence_chain_main(["--project-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out
    assert "evidence_path_missing" in out


def test_cli_emits_json(tmp_path: Path, capsys) -> None:
    _write_bundle(tmp_path, "benchmarks/evidence/clean")
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/clean/summary.tsv",
            }
        ],
    )

    rc = evidence_chain_main(["--project-root", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    assert rc == 0

    import json as _json
    payload = _json.loads(out)
    assert payload["ok"] is True
    assert payload["claims_checked"] == 1
    assert payload["issue_count"] == 0
