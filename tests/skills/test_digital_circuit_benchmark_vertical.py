from __future__ import annotations

import json

import pytest

from argus_skill.core.repair_freshness import load_freshness_expectation
from argus_skill.manager import Manager
from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.skills.vertical_select import VERTICAL_PURPOSES, VERTICALS
from argus_skill.verticals._base import load_vertical, vertical_role_banner
from argus_skill.verticals.digital_circuit.benchmark.stages import (
    prepare_repair_expectation,
    stage_completion_issues,
    validate_external_scoring_handoff,
)
from argus_skill.verticals.digital_circuit.evidence import validate_preflight


def test_benchmark_subvertical_is_registered_and_direct() -> None:
    assert "digital_circuit_benchmark" in VERTICALS
    assert "single-stage" in VERTICAL_PURPOSES["digital_circuit_benchmark"]
    mod = load_vertical("digital_circuit_benchmark")
    assert mod.STAGE_ORDER == ("execute",)
    assert mod.CHECKLIST_STAGE_ORDER == ("execute",)
    assert mod.WORKFLOW_MODE == "direct"
    assert mod.REQUIRE_INDEPENDENT_REVIEW is True
    assert Manager._kind_for("digital_circuit_benchmark") == "custom"
    assert mod.__name__ == "argus_skill.verticals.digital_circuit.benchmark.stages"


def test_benchmark_subvertical_inherits_digital_circuit_skills() -> None:
    skills = dict(iter_vertical_skill_texts("digital_circuit_benchmark"))
    assert "engineer/digital-circuit-first-pass-contract-closure.md" in skills
    assert "engineer/digital-circuit-error-guided-repair.md" in skills
    assert "reviewer/digital-circuit-benchmark-review.md" in skills
    assert "reviewer/digital-circuit-guidance-promotion-review.md" in skills


def test_benchmark_role_banner_forbids_staged_overhead() -> None:
    mod = load_vertical("digital_circuit_benchmark")
    for role in ("planner", "engineer", "reviewer"):
        banner = vertical_role_banner(mod, role)
        assert "BENCHMARK SUBVERTICAL" in banner
        assert "ONE bounded execute mission" in banner
        assert "Do not create or wait for separate specification" in banner
        assert "no_execution" in banner


def test_benchmark_checklist_preserves_public_contract_and_local_semantics() -> None:
    mod = load_vertical("digital_circuit_benchmark")
    rendered = " ".join(
        item.statement for item in mod.CHECKLIST_ITEMS["execute"]
    )
    assert "never silently corrected" in rendered
    assert "width/signing" in rendered
    assert "prior-state sequential behavior" in rendered
    assert "initialization uncertainty" in rendered
    assert "metamorphic" in rendered
    assert "No-execution infrastructure failures imply no RTL verdict" in rendered
    assert "changed public-only hypothesis and test" in rendered
    assert "do not consume a model attempt number" in rendered
    assert "Manager, Planner, Engineer, and independent Reviewer" in rendered


def test_second_attempt_requires_signed_freshness_expectation(tmp_path) -> None:
    preflight = tmp_path / "evidence" / "preflight.json"
    preflight.parent.mkdir(parents=True)
    preflight.write_text(
        json.dumps(
            {
                "status": "pass",
                "generation": 2,
                "iteration": 2,
                "repair_mission_id": "repair-2",
            }
        ),
        encoding="utf-8",
    )

    result = validate_external_scoring_handoff(tmp_path)

    assert result.passed is False
    assert result.issues == ("missing_freshness_expectation",)


def test_controller_can_prepare_signed_repair_expectation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    answer = tmp_path / "rtl" / "dut.sv"
    answer.parent.mkdir()
    answer.write_text("module dut; endmodule\n", encoding="utf-8")

    trusted = prepare_repair_expectation(
        tmp_path,
        generation=2,
        iteration=2,
        mission_id="repair-2",
        answer_paths=("rtl/dut.sv",),
    )
    expectation = load_freshness_expectation(tmp_path)

    assert trusted.is_file()
    assert expectation.generation == 2
    assert expectation.iteration == 2
    assert expectation.mission_id == "repair-2"
    assert expectation.answer_paths == ("rtl/dut.sv",)


def test_reference_model_artifact_can_pass_public_preflight(tmp_path) -> None:
    artifact = tmp_path / "reference" / "candidate.py"
    artifact.parent.mkdir()
    artifact.write_text("class TopModule: pass\n", encoding="utf-8")
    interface = tmp_path / "design" / "BENCHMARK_INTERFACE.json"
    interface.parent.mkdir()
    interface.write_text(
        json.dumps(
            {
                "status": "ready",
                "top_module": "TopModule",
                "output_path": "reference/candidate.py",
                "ports": [{"name": "value", "direction": "input"}],
            }
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "preflight.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "top_modules": ["TopModule"],
                "artifact_files": ["reference/candidate.py"],
                "output_paths": ["reference/candidate.py"],
                "compile_results": [{"returncode": 0}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "DELIVERY.md").write_text("benchmark result\n", encoding="utf-8")

    assert validate_preflight(tmp_path) == evidence / "preflight.json"
    assert stage_completion_issues("execute", tmp_path) == ()


def test_benchmark_completion_reports_missing_handoff(tmp_path) -> None:
    issues = stage_completion_issues("execute", tmp_path)

    assert any("BENCHMARK_INTERFACE.json" in issue for issue in issues)
    assert any("preflight.json" in issue for issue in issues)
    assert any("BENCHMARK_RESULT.md" in issue for issue in issues)
