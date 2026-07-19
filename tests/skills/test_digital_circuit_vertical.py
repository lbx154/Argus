from __future__ import annotations

import json
import subprocess
import sys

from argus_skill.manager._core import _OPTIMIZE_VERTICALS, Manager
from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.skills.stage_checklists import (
    ChecklistLoadState,
    format_stage_checklist,
    resolve_stage_checklist_contract,
)
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
    require_vertical,
    resolve_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_completion_gate,
    vertical_role_banner,
)


def test_digital_circuit_is_registered_and_loadable() -> None:
    assert "digital_circuit" in VERTICALS
    assert "Verilog/SystemVerilog" in VERTICAL_PURPOSES["digital_circuit"]
    assert set(VERTICAL_PURPOSES) == set(VERTICALS)
    assert require_vertical("digital_circuit") == "digital_circuit"

    mod = load_vertical("digital_circuit")
    assert mod.STAGE_ORDER == (
        "specification",
        "rtl",
        "verification",
        "synthesis",
        "delivery",
    )
    assert tuple(mod.STAGE_CHECKS) == mod.STAGE_ORDER
    assert tuple(mod.REVIEWER_CHECKLISTS) == mod.STAGE_ORDER
    assert vertical_completion_gate(mod) == "none"


def test_digital_circuit_persists_and_renders_own_checklists(tmp_path) -> None:
    persist_vertical(tmp_path, "digital_circuit")

    assert resolve_vertical(tmp_path) == "digital_circuit"
    contract = resolve_stage_checklist_contract(
        "verification",
        project_root=tmp_path,
    )
    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert {item.id for item in contract.items} >= {
        "verify.independent-oracle",
        "verify.reset-boundary-random",
        "verify.no-xz-and-properties",
        "verify.reproducible-pass",
    }
    specification = resolve_stage_checklist_contract(
        "specification",
        project_root=tmp_path,
    )
    assert "spec.benchmark-interface-closure" in {
        item.id for item in specification.items
    }
    delivery = resolve_stage_checklist_contract(
        "delivery",
        project_root=tmp_path,
    )
    assert "delivery.benchmark-integrity" in {item.id for item in delivery.items}

    for stage in ("specification", "rtl", "verification", "synthesis", "delivery"):
        rendered = format_stage_checklist(
            stage,
            role="reviewer",
            project_root=tmp_path,
        )
        assert f"Stage checklist ({stage})" in rendered
        assert "submission" not in rendered


def test_digital_circuit_role_banners_pin_hardware_evidence() -> None:
    mod = load_vertical("digital_circuit")

    planner = vertical_role_banner(mod, "planner")
    engineer = vertical_role_banner(mod, "engineer")
    reviewer = vertical_role_banner(mod, "reviewer")
    for banner in (planner, engineer, reviewer):
        assert "MISSION TYPE: DIGITAL CIRCUIT / RTL ENGINEERING" in banner
        assert "NOT a paper pipeline" in banner
        assert "Never claim PASS from compile success alone" in banner
    assert "clock/reset/protocol" in planner
    assert "Verilator/iverilog" in engineer
    assert "hardware sign-off reviewer" in reviewer


def test_digital_circuit_skills_are_packaged() -> None:
    skills = dict(iter_vertical_skill_texts("digital_circuit"))

    assert "engineer/digital-circuit-rtl-verification.md" in skills
    assert "engineer/digital-circuit-benchmark-execution.md" in skills
    assert "engineer/digital-circuit-error-guided-repair.md" in skills
    assert "engineer/digital-circuit-first-pass-contract-closure.md" in skills
    assert "engineer/digital-circuit-spec-guidance-registry.md" in skills
    assert "reviewer/digital-circuit-signoff-review.md" in skills
    assert "reviewer/digital-circuit-benchmark-review.md" in skills
    assert "reviewer/digital-circuit-guidance-promotion-review.md" in skills
    assert "## Operating method" in skills["engineer/digital-circuit-rtl-verification.md"]
    assert "immutable first-attempt evidence" in skills[
        "engineer/digital-circuit-benchmark-execution.md"
    ]
    assert "## Review protocol" in skills["reviewer/digital-circuit-signoff-review.md"]
    assert "Do not divide attempt successes" in skills[
        "reviewer/digital-circuit-benchmark-review.md"
    ]
    assert "benchmark-packaging defect" in skills[
        "engineer/digital-circuit-benchmark-execution.md"
    ]
    assert "prompt-referenced pre-existing public file" in skills[
        "reviewer/digital-circuit-benchmark-review.md"
    ]
    assert "`cdc-transfer`" in skills[
        "engineer/digital-circuit-error-guided-repair.md"
    ]
    assert "design/BENCHMARK_INTERFACE.json" in skills[
        "engineer/digital-circuit-first-pass-contract-closure.md"
    ]
    assert '"status": "ready"' in skills[
        "engineer/digital-circuit-first-pass-contract-closure.md"
    ]
    assert "evidence/preflight.json" in skills[
        "engineer/digital-circuit-benchmark-execution.md"
    ]
    assert "Pure combinational truth table" in skills[
        "engineer/digital-circuit-spec-guidance-registry.md"
    ]
    assert "at least two independent tasks" in skills[
        "reviewer/digital-circuit-guidance-promotion-review.md"
    ]
    assert all(
        "category: anti-cheat" in skills[path]
        for path in (
            "engineer/digital-circuit-benchmark-execution.md",
            "reviewer/digital-circuit-benchmark-review.md",
        )
    )


def test_digital_circuit_banners_cover_benchmark_integrity_and_local_tools() -> None:
    mod = load_vertical("digital_circuit")

    planner = vertical_role_banner(mod, "planner")
    engineer = vertical_role_banner(mod, "engineer")
    reviewer = vertical_role_banner(mod, "reviewer")
    for banner in (planner, engineer, reviewer):
        assert "Keep the first official attempt immutable" in banner
        assert "golden outputs or hidden harness sources" in banner
    assert "shortest auditable path" in planner
    assert "route repair work" in planner
    assert "exact benchmark interface closure" in planner
    assert "declared local containers" in engineer
    assert "failure-taxonomy class" in engineer
    assert "instead of guessing compatibility aliases" in engineer
    assert "post-repair records" in reviewer
    assert "cross-task evidence" in reviewer
    assert "interface manifest is absent" in reviewer


def test_digital_circuit_uses_custom_staged_kind() -> None:
    assert Manager._kind_for("digital_circuit") == "custom"
    assert "digital_circuit" not in _OPTIMIZE_VERTICALS


def test_verification_stage_rejects_failed_log_and_accepts_explicit_pass(tmp_path) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "digital_circuit", "current_stage": "verification"}),
        encoding="utf-8",
    )
    (tmp_path / "tb").mkdir()
    (tmp_path / "tb" / "dut_tb.sv").write_text("module dut_tb; endmodule\n", encoding="utf-8")
    (tmp_path / "verification").mkdir()
    log = tmp_path / "verification" / "simulation.log"
    log.write_text("0 passed, 1 failed\nFAIL: expected pass after reset\n", encoding="utf-8")

    command = [
        sys.executable,
        "-m",
        "argus_skill.tools.stage_check",
        "--project-root",
        str(tmp_path),
        "--vertical",
        "digital_circuit",
        "--stage",
        "verification",
    ]
    failed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "Verification results present" in failed.stdout

    log.write_text("PASS: reset and boundary scenarios\n", encoding="utf-8")
    passed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_verification_result_does_not_count_as_verification_source(tmp_path) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "digital_circuit", "current_stage": "verification"}),
        encoding="utf-8",
    )
    (tmp_path / "verification").mkdir()
    (tmp_path / "verification" / "result.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "argus_skill.tools.stage_check",
        "--project-root",
        str(tmp_path),
        "--vertical",
        "digital_circuit",
        "--stage",
        "verification",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Verification source present" in result.stdout
