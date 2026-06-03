"""Tests for exemplar_grounding gate (Step 6 — force top-conference
style study + format observation + figure-inventory analysis before
drafting)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus_skill.skills.exemplar_grounding import (
    MIN_BLUEPRINT_CHARS,
    MIN_EXEMPLARS,
    MIN_STYLE_PROFILE_CHARS,
    validate_exemplar_grounding,
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _seed_exemplar(root: Path, slug: str, *, with_figs: bool = True) -> dict:
    """Create exemplars/<slug>/paper.pdf + return the EXEMPLAR.json entry."""
    d = root / "paper" / "style_ref" / "exemplars" / slug
    d.mkdir(parents=True, exist_ok=True)
    pdf = d / "paper.pdf"
    body = f"%PDF-1.4 fake {slug}\n".encode()
    pdf.write_bytes(body)
    profile = {"section_count": 6, "page_count": 8}
    if with_figs:
        profile["figure_inventory"] = [
            {"id": "fig1", "type": "teaser"},
            {"id": "fig2", "type": "pipeline"},
            {"id": "tab1", "type": "results_table"},
        ]
    return {
        "slug": slug,
        "title": f"Toy paper {slug}",
        "url": f"https://arxiv.org/abs/0000.{slug}",
        "venue": "EMNLP",
        "year": 2024,
        "source_type": "arxiv",
        "open_access": True,
        "license": "arxiv-nonexclusive",
        "pdf_storage_policy": "local",
        "usage": "structural_style_only",
        "no_prose_copy": True,
        "local_pdf": f"paper/style_ref/exemplars/{slug}/paper.pdf",
        "pdf_sha256": _sha(body),
        "text_extract": "",
        "structural_profile": profile,
    }


def _seed_passing(root: Path, *, with_conformance: bool = False) -> None:
    style_ref = root / "paper" / "style_ref"
    style_ref.mkdir(parents=True, exist_ok=True)
    e1 = _seed_exemplar(root, "best2024-awesome")
    e2 = _seed_exemplar(root, "samedir2024-method")
    (style_ref / "EXEMPLAR.json").write_text(
        json.dumps({
            "exemplar_schema_version": 2,
            "exemplars": [e1, e2],
        }),
        encoding="utf-8",
    )
    (style_ref / "STYLE_PROFILE.md").write_text(
        "# Style Profile\n\n" + ("Top-venue structural lesson. " * 200),
        encoding="utf-8",
    )
    (style_ref / "EXEMPLAR_SUITABILITY.json").write_text(
        json.dumps({
            "verdict": "PASS",
            "primary_exemplar": "best2024-awesome",
            "no_prose_copy_attestation": True,
            "scores": {
                "task_type": 4, "method_family": 5,
                "experiment_shape": 4, "figure_density": 4,
                "related_work_shape": 5, "page_rhythm": 4,
            },
        }),
        encoding="utf-8",
    )
    (style_ref / "PAPER_STRUCTURE_BLUEPRINT.md").write_text(
        "# Blueprint\n\n" + ("Section role and page budget. " * 80),
        encoding="utf-8",
    )
    if with_conformance:
        (style_ref / "STRUCTURE_CONFORMANCE.json").write_text(
            json.dumps({
                "conformance_schema_version": 1,
                "verdict": "PASS",
                "no_prose_copy_attestation": True,
                "exemplar_lessons": ["L1", "L2"],
                "section_mappings": [
                    {"section": "Introduction",
                     "maps_to_exemplar_phase": "intro",
                     "evidence_sources": ["research/BRIEF.md"],
                     "exemplar_lesson": "open with gap"},
                    {"section": "Method",
                     "maps_to_exemplar_phase": "method",
                     "evidence_sources": ["code/method.py"],
                     "exemplar_lesson": "two paragraphs"},
                ],
            }),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Pre-draft contract
# ---------------------------------------------------------------------------


def test_missing_style_ref_dir_fails(tmp_path: Path) -> None:
    report = validate_exemplar_grounding(tmp_path)
    codes = {i.code for i in report.issues}
    assert "missing_style_ref_dir" in codes


def test_full_passing_grounding_ok(tmp_path: Path) -> None:
    _seed_passing(tmp_path)
    report = validate_exemplar_grounding(tmp_path)
    assert report.ok, report.to_text()
    assert report.exemplar_count == 2
    assert report.primary_exemplar == "best2024-awesome"
    assert report.style_profile_chars >= MIN_STYLE_PROFILE_CHARS
    assert report.blueprint_chars >= MIN_BLUEPRINT_CHARS


def test_one_exemplar_only_fails(tmp_path: Path) -> None:
    style_ref = tmp_path / "paper" / "style_ref"
    style_ref.mkdir(parents=True)
    e = _seed_exemplar(tmp_path, "only")
    (style_ref / "EXEMPLAR.json").write_text(
        json.dumps({"exemplar_schema_version": 2, "exemplars": [e]}),
        encoding="utf-8",
    )
    (style_ref / "STYLE_PROFILE.md").write_text("x" * (MIN_STYLE_PROFILE_CHARS + 1), encoding="utf-8")
    (style_ref / "PAPER_STRUCTURE_BLUEPRINT.md").write_text("x" * (MIN_BLUEPRINT_CHARS + 1), encoding="utf-8")
    (style_ref / "EXEMPLAR_SUITABILITY.json").write_text(
        json.dumps({"verdict": "PASS", "primary_exemplar": "only",
                    "no_prose_copy_attestation": True}),
        encoding="utf-8",
    )
    report = validate_exemplar_grounding(tmp_path)
    codes = {i.code for i in report.issues}
    assert "too_few_exemplars" in codes


def test_exemplar_pdf_must_exist_on_disk(tmp_path: Path) -> None:
    """Anti-fab: an EXEMPLAR.json entry pointing at a fake path must fail."""
    _seed_passing(tmp_path)
    data = json.loads((tmp_path / "paper/style_ref/EXEMPLAR.json").read_text())
    data["exemplars"][0]["local_pdf"] = "paper/style_ref/exemplars/ghost/paper.pdf"
    (tmp_path / "paper/style_ref/EXEMPLAR.json").write_text(
        json.dumps(data), encoding="utf-8",
    )
    report = validate_exemplar_grounding(tmp_path)
    codes = {i.code for i in report.issues}
    assert "exemplar_local_pdf_missing_on_disk" in codes


def test_missing_pdf_sha256_fails(tmp_path: Path) -> None:
    """Anti-fab: every exemplar must record the hash so a hand-typed
    entry with no real download can be traced."""
    _seed_passing(tmp_path)
    data = json.loads((tmp_path / "paper/style_ref/EXEMPLAR.json").read_text())
    data["exemplars"][0]["pdf_sha256"] = ""
    (tmp_path / "paper/style_ref/EXEMPLAR.json").write_text(
        json.dumps(data), encoding="utf-8",
    )
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "exemplar_missing_pdf_sha256" in codes


def test_exemplar_missing_figure_inventory_fails(tmp_path: Path) -> None:
    """User requirement #3: every exemplar's structural_profile must
    record what figures/tables it has, so this paper can mirror the plan."""
    _seed_passing(tmp_path)
    data = json.loads((tmp_path / "paper/style_ref/EXEMPLAR.json").read_text())
    # Remove figure inventory from the primary exemplar.
    data["exemplars"][0]["structural_profile"] = {"section_count": 6}
    (tmp_path / "paper/style_ref/EXEMPLAR.json").write_text(
        json.dumps(data), encoding="utf-8",
    )
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "exemplar_missing_figure_inventory" in codes


def test_alternate_figure_inventory_keys_accepted(tmp_path: Path) -> None:
    """Either `figure_inventory`, `figures`, or `figure_table_inventory`
    counts — the contract is liberal in what fulfils it."""
    _seed_passing(tmp_path)
    data = json.loads((tmp_path / "paper/style_ref/EXEMPLAR.json").read_text())
    prof = data["exemplars"][0]["structural_profile"]
    del prof["figure_inventory"]
    prof["figures"] = ["fig1", "fig2"]
    (tmp_path / "paper/style_ref/EXEMPLAR.json").write_text(
        json.dumps(data), encoding="utf-8",
    )
    report = validate_exemplar_grounding(tmp_path)
    assert report.ok, report.to_text()


def test_schema_version_mismatch_fails(tmp_path: Path) -> None:
    _seed_passing(tmp_path)
    data = json.loads((tmp_path / "paper/style_ref/EXEMPLAR.json").read_text())
    data["exemplar_schema_version"] = 1
    (tmp_path / "paper/style_ref/EXEMPLAR.json").write_text(
        json.dumps(data), encoding="utf-8",
    )
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "exemplar_schema_version_mismatch" in codes


def test_style_profile_too_short_fails(tmp_path: Path) -> None:
    _seed_passing(tmp_path)
    (tmp_path / "paper/style_ref/STYLE_PROFILE.md").write_text("# tiny\n", encoding="utf-8")
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "style_profile_too_short" in codes


def test_blueprint_too_short_fails(tmp_path: Path) -> None:
    _seed_passing(tmp_path)
    (tmp_path / "paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md").write_text(
        "# tiny\n", encoding="utf-8"
    )
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "paper_structure_blueprint_too_short" in codes


def test_suitability_not_pass_fails(tmp_path: Path) -> None:
    _seed_passing(tmp_path)
    s = json.loads((tmp_path / "paper/style_ref/EXEMPLAR_SUITABILITY.json").read_text())
    s["verdict"] = "WARN"
    (tmp_path / "paper/style_ref/EXEMPLAR_SUITABILITY.json").write_text(
        json.dumps(s), encoding="utf-8",
    )
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "exemplar_suitability_not_pass" in codes


def test_primary_exemplar_unknown_slug_fails(tmp_path: Path) -> None:
    _seed_passing(tmp_path)
    s = json.loads((tmp_path / "paper/style_ref/EXEMPLAR_SUITABILITY.json").read_text())
    s["primary_exemplar"] = "this-slug-does-not-exist"
    (tmp_path / "paper/style_ref/EXEMPLAR_SUITABILITY.json").write_text(
        json.dumps(s), encoding="utf-8",
    )
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "primary_exemplar_unknown_slug" in codes


def test_suitability_missing_no_prose_attestation_fails(tmp_path: Path) -> None:
    _seed_passing(tmp_path)
    s = json.loads((tmp_path / "paper/style_ref/EXEMPLAR_SUITABILITY.json").read_text())
    s["no_prose_copy_attestation"] = False
    (tmp_path / "paper/style_ref/EXEMPLAR_SUITABILITY.json").write_text(
        json.dumps(s), encoding="utf-8",
    )
    codes = {i.code for i in validate_exemplar_grounding(tmp_path).issues}
    assert "exemplar_suitability_no_prose_copy_attestation_missing" in codes


# ---------------------------------------------------------------------------
# Submission stage — STRUCTURE_CONFORMANCE enforcement
# ---------------------------------------------------------------------------


def test_conformance_not_required_at_draft(tmp_path: Path) -> None:
    """At draft stage, missing STRUCTURE_CONFORMANCE.json is OK — it's a
    post-draft artifact."""
    _seed_passing(tmp_path, with_conformance=False)
    report = validate_exemplar_grounding(tmp_path, require_conformance=False)
    assert report.ok


def test_conformance_required_at_submission(tmp_path: Path) -> None:
    _seed_passing(tmp_path, with_conformance=False)
    report = validate_exemplar_grounding(tmp_path, require_conformance=True)
    codes = {i.code for i in report.issues}
    assert "missing_structure_conformance_json" in codes


def test_conformance_pass_at_submission_ok(tmp_path: Path) -> None:
    _seed_passing(tmp_path, with_conformance=True)
    report = validate_exemplar_grounding(tmp_path, require_conformance=True)
    assert report.ok, report.to_text()
    assert report.has_conformance_json
    assert report.conformance_section_mappings == 2


def test_conformance_empty_section_mappings_fails(tmp_path: Path) -> None:
    _seed_passing(tmp_path, with_conformance=True)
    p = tmp_path / "paper/style_ref/STRUCTURE_CONFORMANCE.json"
    data = json.loads(p.read_text())
    data["section_mappings"] = []
    p.write_text(json.dumps(data), encoding="utf-8")
    report = validate_exemplar_grounding(tmp_path, require_conformance=True)
    codes = {i.code for i in report.issues}
    assert "structure_conformance_empty_section_mappings" in codes


# ---------------------------------------------------------------------------
# automated_gates wiring
# ---------------------------------------------------------------------------


def test_automated_gates_wires_exemplar_grounding(tmp_path: Path) -> None:
    from argus_skill.skills.automated_gates import (
        GATE_KINDS,
        STAGE_GATES,
        gates_for_stage,
        run_stage_gates,
    )

    for stage in ("draft", "review", "submission"):
        assert "exemplar_grounding" in STAGE_GATES[stage]
        assert "exemplar_grounding" in gates_for_stage(stage)
    assert "exemplar_grounding" not in STAGE_GATES["analysis"]
    assert GATE_KINDS["exemplar_grounding"] == "structural"

    # Empty workdir at draft → structural failure.
    results = run_stage_gates(tmp_path, stage="draft")
    eg = next(r for r in results if r.name == "exemplar_grounding")
    assert eg.passed is False
    assert eg.is_blocking is True


def test_run_stage_gates_submission_requires_conformance(tmp_path: Path) -> None:
    """At submission stage, even a passing pre-draft grounding fails
    until STRUCTURE_CONFORMANCE.json is added."""
    _seed_passing(tmp_path, with_conformance=False)
    from argus_skill.skills.automated_gates import run_stage_gates
    results = run_stage_gates(tmp_path, stage="submission")
    eg = next(r for r in results if r.name == "exemplar_grounding")
    assert eg.passed is False
    assert "STRUCTURE_CONFORMANCE" in eg.detail or "structure_conformance" in eg.detail


def test_run_stage_gates_submission_passes_with_conformance(tmp_path: Path) -> None:
    _seed_passing(tmp_path, with_conformance=True)
    from argus_skill.skills.automated_gates import run_stage_gates
    results = run_stage_gates(tmp_path, stage="submission")
    eg = next(r for r in results if r.name == "exemplar_grounding")
    assert eg.passed is True, eg.detail
