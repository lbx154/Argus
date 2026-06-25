"""Tests for argus_skill.skills.automated_gates (advisory + structural).

Post-c6b11d3 rewrite: gates are now tagged ``structural`` (anti-fraud,
allowed to block via exit code) or ``advisory`` (facts surfaced to
reviewer, never block). The tests verify that distinction is honoured
end-to-end through stage_check.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from argus_skill.skills.automated_gates import (
    GATE_KINDS,
    STAGE_GATES,
    GateResult,
    any_blocking_failure,
    format_results,
    gates_for_stage,
    run_stage_gates,
)
from argus_skill.skills.automated_gates import (
    main as automated_gates_main,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _seed_minimal_paper(root: Path) -> None:
    """Seed the bare minimum paper that satisfies paper_structural_minimums,
    so tests targeting evidence_chain / mediocrity_finding aren't blocked
    by the new structural-floor gate that lives at draft+ stages."""
    import os

    from argus_skill.verticals.research.paper_structural_minimums import MIN_INTEXT_CITES
    from argus_skill.verticals.research.reviewer_simulation import (
        MIN_QUESTIONS,
        QUESTIONS_FILENAME,
    )
    paper = root / "paper"
    figs = paper / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    (figs / "fig1.pdf").write_bytes(b"%PDF-1.4 stub\n")
    (figs / "teaser.png").write_bytes(b"\x89PNG\r\n")
    (figs / "pipeline.png").write_bytes(b"\x89PNG\r\n")
    (figs / "IMAGE2_FIGURES.json").write_text(
        json.dumps({"figures": [
            {"name": "teaser_hero", "file": "paper/figures/teaser.png"},
            {"name": "pipeline_overview", "file": "paper/figures/pipeline.png"},
        ]}),
        encoding="utf-8",
    )
    cite_block = ", ".join(f"\\cite{{w{i}}}" for i in range(MIN_INTEXT_CITES))
    main_tex = paper / "main.tex"
    main_tex.write_text(
        r"\documentclass{article}\begin{document}" + "\n"
        + r"\includegraphics{figures/fig1.pdf}" + "\n"
        + cite_block + "\n"
        + r"\section{Related Work}" + "\n"
        + ("Prior work. " * 120) + "\n"
        + r"\section{Conclusion}" + "\nEnd.\n"
        + r"\appendix" + "\n"
        + r"\section{Reproducibility}" + "\nDetails.\n"
        + r"\end{document}" + "\n",
        encoding="utf-8",
    )
    (paper / "refs.bib").write_text(
        "\n".join(
            f"@article{{w{i}, title={{T}}, author={{A}}, year={{2024}}}}"
            for i in range(MIN_INTEXT_CITES)
        ),
        encoding="utf-8",
    )
    qpath = paper / QUESTIONS_FILENAME
    qpath.write_text(
        json.dumps({
            "schema_version": 1,
            "questions": [
                {
                    "id": f"Q{i}",
                    "question": f"placeholder reviewer question {i}",
                    "severity": ("critical", "major", "minor")[i % 3],
                    "addressed_in_section": f"section {i % 3 + 1}",
                    "addressed_evidence": "see paragraph",
                }
                for i in range(MIN_QUESTIONS)
            ],
        }),
        encoding="utf-8",
    )
    # Ensure questions mtime >= main.tex mtime (freshness check)
    later = main_tex.stat().st_mtime + 5
    os.utime(qpath, (later, later))
    # Minimal experiment-audit artifacts
    (paper / "EXPERIMENT_AUDIT.md").write_text("# audit stub\n", encoding="utf-8")
    (paper / "EXPERIMENT_AUDIT.json").write_text(
        json.dumps({
            "auditor": "reviewer-route-xhigh",
            "integrity_status": "pass",
            "checks": {
                "gt_provenance":      {"status": "pass", "details": "dataset GT"},
                "score_normalization": {"status": "pass", "details": "raw scores only"},
                "result_existence":    {"status": "pass", "details": "all match"},
                "dead_code":           {"status": "pass", "details": "all called"},
                "scope":               {"status": "pass", "details": "sufficient"},
                "eval_type": "real_gt",
            },
        }),
        encoding="utf-8",
    )
    # Minimal exemplar-grounding artifacts (study top-venue exemplars).
    import hashlib as _hl
    style = paper / "style_ref"
    style.mkdir(parents=True, exist_ok=True)
    exemplars = []
    for slug in ("best2024-x", "samedir2024-y"):
        d = style / "exemplars" / slug
        d.mkdir(parents=True, exist_ok=True)
        body = f"%PDF-1.4 fake {slug}\n".encode()
        (d / "paper.pdf").write_bytes(body)
        exemplars.append({
            "slug": slug,
            "title": f"Toy {slug}",
            "url": f"https://arxiv.org/abs/0000.{slug}",
            "venue": "EMNLP",
            "year": 2024,
            "source_type": "arxiv",
            "open_access": True,
            "license": "arxiv",
            "pdf_storage_policy": "local",
            "usage": "structural_style_only",
            "no_prose_copy": True,
            "local_pdf": f"paper/style_ref/exemplars/{slug}/paper.pdf",
            "pdf_sha256": _hl.sha256(body).hexdigest(),
            "text_extract": "",
            "structural_profile": {
                "figure_inventory": [
                    {"id": "fig1", "type": "teaser"},
                    {"id": "fig2", "type": "pipeline"},
                ],
                "section_count": 6,
            },
            "format_facts": {
                "total_pages": 8, "section_count": 6,
                "figure_count": 3, "table_count": 2,
                "citations_per_page": 5.0,
                "body_pages_before_references": 7,
            },
        })
    (style / "EXEMPLAR.json").write_text(
        json.dumps({"exemplar_schema_version": 2, "exemplars": exemplars}),
        encoding="utf-8",
    )
    (style / "STYLE_PROFILE.md").write_text(
        "# Style Profile\n\n" + ("Structural lesson. " * 200),
        encoding="utf-8",
    )
    (style / "EXEMPLAR_SUITABILITY.json").write_text(
        json.dumps({
            "verdict": "PASS",
            "primary_exemplar": "best2024-x",
            "no_prose_copy_attestation": True,
        }),
        encoding="utf-8",
    )
    (style / "PAPER_STRUCTURE_BLUEPRINT.md").write_text(
        "# Blueprint\n\n" + ("Section role and page budget. " * 80),
        encoding="utf-8",
    )
    (style / "STRUCTURE_CONFORMANCE.json").write_text(
        json.dumps({
            "conformance_schema_version": 1,
            "verdict": "PASS",
            "no_prose_copy_attestation": True,
            "exemplar_lessons": ["L1", "L2"],
            "section_mappings": [
                {"section": "Introduction",
                 "maps_to_exemplar_phase": "intro",
                 "evidence_sources": ["x"], "exemplar_lesson": "y"},
            ],
        }),
        encoding="utf-8",
    )
    (paper / "PAPER_FORMAT_FACTS.json").write_text(
        json.dumps({
            "total_pages": 7, "section_count": 6,
            "figure_count": 3, "table_count": 2,
            "citations_per_page": 4.5,
            "body_pages_before_references": 6,
        }),
        encoding="utf-8",
    )


def _write_bundle(
    root: Path, name: str, *,
    condition: str = "argus", reward: float = 0.7,
    dataset_id: str = "harbor-bench@1.0",
    total: int = 89, errored: int = 0,
    tainted: bool = False,
) -> None:
    bundle = root / "benchmarks" / "evidence" / name
    bundle.mkdir(parents=True, exist_ok=True)
    header = (
        "row_kind\tcondition\treward\tn_total_trials\t"
        "n_completed_trials\tn_errored_trials\n"
    )
    body = f"aggregate\t{condition}\t{reward}\t{total}\t{total - errored}\t{errored}\n"
    (bundle / "summary.tsv").write_text(header + body, encoding="utf-8")
    build_info = "# Build Info\n- status: completed\n"
    if tainted:
        build_info += "TAINTED — DO NOT CITE AS PERFORMANCE.\n"
    (bundle / "BUILD_INFO.md").write_text(build_info, encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"dataset_id": dataset_id, "condition": condition}),
        encoding="utf-8",
    )


def _write_claims_tsv(root: Path, rows: list[dict[str, str]]) -> None:
    cols = [
        "claim_id", "status", "claim",
        "evidence_1", "evidence_2", "evidence_3", "notes",
    ]
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(row.get(c, "") for c in cols))
    _write(root / "paper" / "claims_to_evidence.tsv", "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# STAGE_GATES / GATE_KINDS — single source of truth
# ---------------------------------------------------------------------------


def test_stage_gates_map_covers_canonical_stages() -> None:
    for stage in (
        "research", "plan", "benchmark",
        "run", "analysis", "draft", "review", "submission",
    ):
        assert stage in STAGE_GATES


def test_gate_kinds_classify_each_known_gate() -> None:
    # Every gate in STAGE_GATES must have an entry in GATE_KINDS, otherwise
    # we can't decide blocking semantics.
    referenced = {gate for gates in STAGE_GATES.values() for gate in gates}
    for gate in referenced:
        assert gate in GATE_KINDS, f"missing kind for {gate!r}"


def test_evidence_chain_is_structural() -> None:
    assert GATE_KINDS["evidence_chain"] == "structural"


def test_mediocrity_finding_is_advisory() -> None:
    # The big architectural invariant: this must NEVER be structural.
    # Making it structural would re-introduce the c6b11d3 violation.
    assert GATE_KINDS["mediocrity_finding"] == "advisory"


def test_review_and_submission_run_both_gates() -> None:
    for stage in ("review", "submission"):
        gates = set(gates_for_stage(stage))
        assert "evidence_chain" in gates
        assert "mediocrity_finding" in gates


# ---------------------------------------------------------------------------
# GateResult.is_blocking — advisory NEVER blocks
# ---------------------------------------------------------------------------


def test_advisory_finding_never_blocks_even_if_passed_false() -> None:
    r = GateResult(
        name="mediocrity_finding", kind="advisory",
        passed=False, summary="x", detail="y",
    )
    # The dataclass allows passed=False for ergonomic reasons but the
    # runtime semantics are: advisory never blocks.
    assert r.is_blocking is False


def test_structural_failure_blocks() -> None:
    r = GateResult(
        name="evidence_chain", kind="structural",
        passed=False, summary="x", detail="y",
    )
    assert r.is_blocking is True


def test_structural_pass_does_not_block() -> None:
    r = GateResult(
        name="evidence_chain", kind="structural",
        passed=True, summary="ok", detail="",
    )
    assert r.is_blocking is False


def test_any_blocking_failure_ignores_advisory() -> None:
    advisory_fail = GateResult(
        name="mediocrity_finding", kind="advisory",
        passed=False, summary="x", detail="y",
    )
    structural_pass = GateResult(
        name="evidence_chain", kind="structural",
        passed=True, summary="ok", detail="",
    )
    assert any_blocking_failure([advisory_fail, structural_pass]) is False


# ---------------------------------------------------------------------------
# run_stage_gates — end-to-end via fake project
# ---------------------------------------------------------------------------


def test_run_stage_gates_review_clean_project_passes_structural(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72)
    _write_bundle(tmp_path, "bare-bundle", condition="bare", reward=0.60)
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "Argus beats bare on the benchmark",
                "evidence_1": "benchmarks/evidence/argus-bundle/summary.tsv",
                "evidence_2": "benchmarks/evidence/bare-bundle/summary.tsv",
            }
        ],
    )
    _seed_minimal_paper(tmp_path)

    results = run_stage_gates(
        tmp_path,
        stage="review",
        proposed_condition="argus",
        baseline_condition="bare",
    )

    names = [r.name for r in results]
    assert names == [
        "evidence_chain",
        "mediocrity_finding",
        "paper_structural_minimums",
        "reviewer_simulation",
        "experiment_audit",
        "exemplar_grounding",
        "run_evidence_health",
    ]
    # Structural passes, no block.
    assert any_blocking_failure(results) is False


def test_run_stage_gates_surfaces_structural_break(tmp_path: Path) -> None:
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
    _seed_minimal_paper(tmp_path)
    results = run_stage_gates(tmp_path, stage="draft")
    names = [r.name for r in results]
    assert names == [
        "evidence_chain",
        "paper_structural_minimums",
        "exemplar_grounding",
    ]
    chain_result = next(r for r in results if r.name == "evidence_chain")
    assert chain_result.is_blocking is True


def test_run_stage_gates_advisory_does_not_block_even_with_zero_baseline(tmp_path: Path) -> None:
    # No baseline aggregate at all → in the OLD F3 this would block as
    # "baseline_not_reproduced". In the new advisory model it does NOT.
    _write_bundle(tmp_path, "p", condition="argus", reward=0.72)
    results = run_stage_gates(
        tmp_path,
        stage="run",
        proposed_condition="argus",
        baseline_condition="bare",
    )
    assert {r.name for r in results} == {
        "mediocrity_finding", "run_evidence_health", "rl_training_plots",
        "rl_training_health", "method_differentiation",
    }
    advisory = next(r for r in results if r.name == "mediocrity_finding")
    assert advisory.kind == "advisory"
    assert any_blocking_failure(results) is False


# ---------------------------------------------------------------------------
# format_results — advisory tagged ADVISORY, never FAIL
# ---------------------------------------------------------------------------


def test_format_results_uses_advisory_label_for_advisory_kind() -> None:
    blocks = format_results(
        [
            GateResult(name="evidence_chain", kind="structural",
                       passed=True, summary="clean", detail=""),
            GateResult(name="mediocrity_finding", kind="advisory",
                       passed=False, summary="numbers", detail="d"),
        ]
    )
    assert "[PASS] gate:evidence_chain" in blocks
    assert "[ADVISORY] gate:mediocrity_finding" in blocks
    assert "[FAIL] gate:mediocrity_finding" not in blocks


# ---------------------------------------------------------------------------
# CLI — exit code reflects only structural failures
# ---------------------------------------------------------------------------


def test_automated_gates_cli_exits_zero_on_advisory_only_bad_numbers(tmp_path: Path, capsys) -> None:
    # No baseline, proposed numbers terrible — advisory finding will be
    # ugly, but exit code stays 0.
    _write_bundle(tmp_path, "p", condition="argus", reward=0.10)
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/p/summary.tsv",
            }
        ],
    )
    _seed_minimal_paper(tmp_path)
    rc = automated_gates_main(
        [
            "--project-root", str(tmp_path),
            "--stage", "review",
            "--proposed-condition", "argus",
            "--baseline-condition", "bare",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[PASS] gate:evidence_chain" in out
    assert "[ADVISORY] gate:mediocrity_finding" in out


def test_automated_gates_cli_exits_nonzero_on_structural(tmp_path: Path, capsys) -> None:
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
    rc = automated_gates_main(
        ["--project-root", str(tmp_path), "--stage", "draft"]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] gate:evidence_chain" in out


def test_automated_gates_cli_json_includes_kind(tmp_path: Path, capsys) -> None:
    _write_claims_tsv(tmp_path, [])
    automated_gates_main(
        ["--project-root", str(tmp_path), "--stage", "review", "--json"]
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    for r in payload["results"]:
        assert r["kind"] in ("structural", "advisory")
    assert "structural_block" in payload


# ---------------------------------------------------------------------------
# stage_check end-to-end — advisory must NOT affect exit code
# ---------------------------------------------------------------------------


def test_stage_check_advisory_finding_does_not_fail_exit_code(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "run"}), encoding="utf-8"
    )
    # Single bundle: advisory finding will note "1 family, no baseline"
    # but advisory NEVER blocks.
    _write_bundle(tmp_path, "p", condition="argus", reward=0.5)

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.stage_check",
         "--project-root", str(tmp_path), "--stage", "run"],
        text=True, capture_output=True,
    )

    # Shell checks at "run" stage will fail (no real venv etc.), so we
    # don't assert returncode == 0. We assert that the advisory finding
    # is rendered with the right tag and is reported as an advisory
    # finding (reviewer rules) in the trailing summary.
    assert "📋 mediocrity_finding (advisory)" in proc.stdout
    assert "advisory finding(s)" in proc.stdout


def test_stage_check_structural_break_does_fail_exit_code(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "draft"}), encoding="utf-8"
    )
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

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.stage_check",
         "--project-root", str(tmp_path), "--stage", "draft"],
        text=True, capture_output=True,
    )

    assert "❌ evidence_chain (structural)" in proc.stdout
    assert "evidence_path_missing" in proc.stdout
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# Top-level argus-skill CLI smoke
# ---------------------------------------------------------------------------


def test_cli_lifecycle_status_on_minimal_project(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-status", "--project-root", str(tmp_path)],
        text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "project lifecycle (F5)" in proc.stdout
    assert "observed_state    : incubating" in proc.stdout
    assert "token_allocatable : True" in proc.stdout


def test_cli_anti_mediocrity_check_exits_zero_even_with_no_baseline(tmp_path: Path) -> None:
    # The OLD CLI returned 1 on missing baseline. The new advisory CLI
    # exits 0 — the harness doesn't have an opinion on "missing baseline".
    _write_bundle(tmp_path, "p", condition="argus", reward=0.72)
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--anti-mediocrity-check",
         "--project-root", str(tmp_path),
         "--proposed-condition", "argus",
         "--baseline-condition", "bare"],
        text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    # The facts are surfaced so reviewer can rule:
    assert "Reviewer judgement points" in proc.stdout
