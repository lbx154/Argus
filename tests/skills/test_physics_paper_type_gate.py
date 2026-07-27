"""Tests for the Paper-Type classifier gate (consumes upstream gate results)."""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.physics import stages
from argus_skill.verticals.physics.gates import paper_type as pt


def _gate_result(root: Path, prefix: str, passed: bool) -> None:
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / f"{prefix}_RESULT.json").write_text(
        json.dumps({"passed": passed}), encoding="utf-8"
    )


def _classifier(root: Path, **over: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    data = {f: "x" for f in pt.REQUIRED_FIELDS}
    data["paper_type"] = "original research article candidate"
    data["confidence"] = "0.7"
    data.update(over)
    (root / pt.ARTIFACT).write_text(json.dumps(data), encoding="utf-8")


def _codes(root: Path) -> list[str]:
    return [f["failure_id"] for f in pt.verify_paper_type(root)]


def test_original_passes_when_gates_pass(tmp_path: Path) -> None:
    _gate_result(tmp_path, "LITERATURE_GATE", True)
    _gate_result(tmp_path, "NOVELTY_GATE", True)
    _gate_result(tmp_path, "NUMERICAL_GATE", True)
    _classifier(tmp_path)
    assert pt.verify_paper_type(tmp_path) == []


def test_missing_classifier_fails_pt000(tmp_path: Path) -> None:
    assert pt.verify_paper_type(tmp_path)[0]["failure_id"] == "PT-000"


def test_original_but_literature_unpassed_fails_pt001(tmp_path: Path) -> None:
    _gate_result(tmp_path, "NOVELTY_GATE", True)
    _gate_result(tmp_path, "NUMERICAL_GATE", True)  # literature absent -> not passed
    _classifier(tmp_path)
    assert "PT-001" in _codes(tmp_path)


def test_original_but_novelty_unpassed_fails_pt002(tmp_path: Path) -> None:
    _gate_result(tmp_path, "LITERATURE_GATE", True)
    _gate_result(tmp_path, "NUMERICAL_GATE", True)  # novelty absent
    _classifier(tmp_path)
    assert "PT-002" in _codes(tmp_path)


def test_publishable_without_basis_fails_pt003(tmp_path: Path) -> None:
    for g in ("LITERATURE_GATE", "NOVELTY_GATE", "NUMERICAL_GATE"):
        _gate_result(tmp_path, g, True)
    _classifier(tmp_path, basis_from_numerical_gate="")
    assert "PT-003" in _codes(tmp_path)


def test_invalid_paper_type_fails_pt004(tmp_path: Path) -> None:
    for g in ("LITERATURE_GATE", "NOVELTY_GATE", "NUMERICAL_GATE"):
        _gate_result(tmp_path, g, True)
    _classifier(tmp_path, paper_type="nobel prize paper")
    assert "PT-004" in _codes(tmp_path)


def test_publishable_without_honest_boundary_fails_pt005(tmp_path: Path) -> None:
    for g in ("LITERATURE_GATE", "NOVELTY_GATE", "NUMERICAL_GATE"):
        _gate_result(tmp_path, g, True)
    _classifier(tmp_path, why_not_higher="")
    assert "PT-005" in _codes(tmp_path)


def test_lower_type_passes_without_gates(tmp_path: Path) -> None:
    # a diagnostic benchmark needs no literature/novelty pass and no basis fields
    _classifier(tmp_path, paper_type="diagnostic benchmark")
    assert pt.verify_paper_type(tmp_path) == []


def test_advisory_cli_and_banner(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir(
        parents=True, exist_ok=True
    )  # missing classifier -> PT-000 blocker
    assert pt.main(["check", "--project-root", str(tmp_path), "--advisory"]) == 0
    assert pt.main(["check", "--project-root", str(tmp_path)]) == 1
    banner = stages.role_banner("reviewer", project_root=tmp_path)
    assert "PAPER_TYPE_GATE REPAIR REQUIRED" in banner and "PT-000" in banner


def test_review_stage_check_includes_paper_type() -> None:
    assert not hasattr(stages, "STAGE_CHECKS")
    banner = stages.role_banner("reviewer")
    assert "gates.paper_type" in banner
    assert "ADVISORY" in banner
