from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from argus_skill.manager._core import Manager
from argus_skill.manager._helpers import _OPTIMIZE_VERTICALS
from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.skills.stage_machine import (
    ChecklistLoadState,
    format_full_pipeline_checklist,
    resolve_stage_checklist_contract,
)
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
    require_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_completion_gate,
    vertical_requires_independent_review,
    vertical_role_banner,
    vertical_workflow_mode,
)
from argus_skill.verticals.chip_design import environment_audit
from argus_skill.verticals.chip_design.evidence import VALIDATORS, EvidenceError
from argus_skill.verticals.chip_design.tool_registry import (
    filter_entries,
    load_registry,
    validate_registry,
)

STAGES = (
    "definition",
    "architecture",
    "environment",
    "rtl",
    "verification",
    "ppa",
    "prototype",
    "benchmark",
    "signoff",
)


@pytest.fixture(autouse=True)
def _functional_tool_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(
        registry: dict,
        *,
        target_python: str,
        project_root: Path,
    ) -> list[dict]:
        del target_python, project_root
        records = []
        for entry in registry["entries"]:
            entry_id = str(entry["id"])
            executables = {
                command: f"/tools/{command}"
                for command in entry.get("executables", [])
            }
            records.append(
                {
                    "id": entry_id,
                    "available": True,
                    "executables": executables,
                    "source_markers": (
                        [f"/pdks/{entry_id}"]
                        if entry_id in {"sky130", "ihp_sg13g2", "gf180mcu"}
                        else []
                    ),
                }
            )
        return records

    monkeypatch.setattr(environment_audit, "probe_entries", fake_probe)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write(path: Path, text: str = "evidence\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _complete_project(root: Path, *, delivery_level: str = "rtl_ip") -> Path:
    _write_json(
        root / "research/PIPELINE_STATE.json",
        {"vertical": "chip_design", "current_stage": "signoff"},
    )
    _write_json(
        root / "design/CHIP_SCOPE.json",
        {
            "status": "ready",
            "delivery_level": delivery_level,
            "target": "edge_llm_accelerator",
            "workload": "batch1_int4_decode",
            "technology_or_board": "sky130-or-fpga",
            "interfaces": ["axi4", "axi4-lite"],
            "numerical_formats": ["int4", "int8", "int32"],
            "acceptance_metrics": {"correctness": "pass", "frequency_mhz": 100},
            "baselines": ["gemmini", "cpu"],
            "non_goals": ["production-node parity"],
        },
    )
    _write(root / "design/WORKLOAD.md")
    _write(root / "design/SPEC.md")
    _write(root / "design/ARCHITECTURE.md")
    _write(root / "design/BASELINE_PLAN.md")
    _write_json(
        root / "design/MEMORY_MODEL.json",
        {
            "status": "ready",
            "workload_shapes": [{"m": 2048, "k": 2048}],
            "memory_hierarchy": {"weight_sram_bytes": 524288},
            "traffic_bytes": {"per_token": 1000000},
            "arithmetic_intensity": {"flop_per_byte": 0.25},
            "bandwidth_model": {"required_gbps": 20},
            "capacity_model": {"weights_bytes": 750000000},
            "assumptions": ["batch=1"],
        },
    )
    _write_json(root / "design/TARGET.json", {"status": "ready", "target": "sky130"})
    _write_json(
        root / "research/ENVIRONMENT_AUDIT.json",
        environment_audit.collect(root, target_python=sys.executable, required=[]),
    )
    _write(root / "rtl/top.sv", "module top(input logic clk); endmodule\n")
    _write_json(
        root / "design/RTL_MANIFEST.json",
        {
            "status": "ready",
            "top_modules": ["top"],
            "source_files": ["rtl/top.sv"],
            "clock_domains": ["clk"],
            "interfaces": ["axi4"],
            "parameters": ["LANES=16"],
            "provenance": [{"source": "authored", "license": "Apache-2.0"}],
        },
    )
    _write(root / "verification/PLAN.md")
    _write(
        root / "reference/argus_npu_reference.py",
        "def reference_model(value: int) -> int:\n    return value\n",
    )
    _write(root / "verification/raw/sim.log", "PASS\n")
    _write_json(
        root / "verification/RESULTS.json",
        {
            "status": "pass",
            "commands": [{"argv": ["make", "test"], "exit_code": 0}],
            "coverage": {"functional_percent": 100},
            "scenarios": ["reset", "random", "backpressure"],
            "numerical": {"max_abs_error": 0},
            "raw_artifacts": ["verification/raw/sim.log"],
            "source_hashes": {
                "design/RTL_MANIFEST.json": _digest(
                    root / "design/RTL_MANIFEST.json"
                ),
                "rtl/top.sv": _digest(root / "rtl/top.sv"),
            },
        },
    )
    _write(root / "ppa/PROTOCOL.md")
    _write(root / "constraints/top.sdc", "create_clock -period 10 [get_ports clk]\n")
    _write(root / "ppa/raw/synth.rpt")
    ppa = {
        "status": "pass",
        "target": {"technology": "sky130"},
        "toolchain": {"yosys": "0.40"},
        "configuration": {"lanes": 16},
        "timing": {"target_mhz": 100, "worst_slack_ns": 0.1},
        "area_or_resources": {"cell_area_um2": 100000},
        "power": {"method": "vectorless", "mw": 50},
        "warnings_and_waivers": ["none"],
        "constraint_files": ["constraints/top.sdc"],
        "raw_artifacts": ["ppa/raw/synth.rpt"],
        "source_hashes": [
            {
                "path": "design/RTL_MANIFEST.json",
                "sha256": _digest(root / "design/RTL_MANIFEST.json"),
            },
            {"path": "rtl/top.sv", "sha256": _digest(root / "rtl/top.sv")},
            {
                "path": "design/TARGET.json",
                "sha256": _digest(root / "design/TARGET.json"),
            },
            {
                "path": "verification/RESULTS.json",
                "sha256": _digest(root / "verification/RESULTS.json"),
            },
            {
                "path": "ppa/PROTOCOL.md",
                "sha256": _digest(root / "ppa/PROTOCOL.md"),
            },
            {
                "path": "constraints/top.sdc",
                "sha256": _digest(root / "constraints/top.sdc"),
            },
        ],
    }
    if delivery_level in {"gds", "pre_tapeout", "tapeout"}:
        ppa["physical_closure"] = {"sta": "pass", "drc": "pass", "lvs": "pass"}
        for relative in (
            "physical/design.gds",
            "physical/design.extracted.v",
            "physical/sta.rpt",
            "physical/drc.rpt",
            "physical/lvs.rpt",
        ):
            _write(root / relative)
        ppa.update(
            {
                "layout_artifacts": ["physical/design.gds"],
                "extracted_netlists": ["physical/design.extracted.v"],
                "sta_reports": ["physical/sta.rpt"],
                "drc_reports": ["physical/drc.rpt"],
                "lvs_reports": ["physical/lvs.rpt"],
            }
        )
    _write_json(root / "ppa/RESULTS.json", ppa)
    _write(root / "prototype/raw/build.log")
    if delivery_level in {"rtl_ip", "gds", "pre_tapeout"}:
        prototype = {"status": "not_applicable", "reason": "scope does not require FPGA/silicon prototype"}
    else:
        prototype = {
            "status": "pass",
            "prototype_kind": "fpga",
            "target": {"board": "test-board"},
            "build": {"bitstream": "prototype/raw/build.log"},
            "correctness": {"status": "pass"},
            "limitations": ["not fabricated silicon"],
            "raw_artifacts": ["prototype/raw/build.log"],
            "source_hashes": {
                "design/RTL_MANIFEST.json": _digest(
                    root / "design/RTL_MANIFEST.json"
                ),
                "rtl/top.sv": _digest(root / "rtl/top.sv"),
            },
        }
    _write_json(root / "prototype/RESULTS.json", prototype)
    _write(root / "benchmark/PROTOCOL.md")
    _write(root / "benchmark/raw/results.csv")
    _write_json(
        root / "benchmark/RESULTS.json",
        {
            "status": "pass",
            "correctness": {"status": "pass"},
            "workloads": ["qwen-0.5b-gemv"],
            "baselines": ["gemmini-area-matched"],
            "metrics": [
                {
                    "name": "cycles",
                    "unit": "cycles",
                    "candidate": 100,
                    "baseline": 120,
                }
            ],
            "repetitions": 3,
            "regressions": ["none"],
            "limitations": ["RTL simulation only"],
            "comparison": {
                "kind": "matched_hardware",
                "same_workload": True,
                "same_quality": True,
                "same_target_flow": True,
                "same_host": True,
                "same_memory_budget": True,
            },
            "candidate_config": {
                "rtl": "candidate",
                "target": "same-simulator",
                "quantization": "int4",
            },
            "baseline_configs": [
                {
                    "name": "gemmini-area-matched",
                    "target": "same-simulator",
                    "quantization": "int4",
                }
            ],
            "measurement": {
                "warmup": 3,
                "repetitions": 3,
                "synchronization": "completion interrupt",
                "host_offload_partition": "GEMV only",
                "quantization": "identical int4 weights",
                "memory_budget": "same modeled DRAM bandwidth",
                "resource_budget": "same MAC and SRAM budget",
                "power_method": "not measured in RTL simulation",
                "uncertainty": "min/median/max retained",
            },
            "raw_artifacts": ["benchmark/raw/results.csv"],
            "source_hashes": [
                {
                    "path": "design/RTL_MANIFEST.json",
                    "sha256": _digest(root / "design/RTL_MANIFEST.json"),
                },
                {"path": "rtl/top.sv", "sha256": _digest(root / "rtl/top.sv")},
            ],
        },
    )
    _write(root / "signoff/raw/reproduce.log")
    _write(root / "RESULTS.md")
    _write(root / "Makefile", "test:\n\t@true\n")
    signoff = {
        "status": "pass",
        "stage_results": {
            "definition": "pass",
            "architecture": "pass",
            "environment": "pass",
            "rtl": "pass",
            "verification": "pass",
            "ppa": "pass",
            "prototype": prototype["status"],
            "benchmark": "pass",
        },
        "claims": [
            {"level": "rtl_ip", "statement": "synthesizable and verified RTL IP"}
        ],
        "known_limitations": ["not fabricated silicon"],
        "provenance": {"argus": "events.jsonl", "interventions": []},
        "reproduction_commands": ["make test"],
        "reproduction_files": ["Makefile"],
        "artifact_manifest": "signoff/ARTIFACT_MANIFEST.json",
        "environment_result": "research/ENVIRONMENT_AUDIT.json",
        "verification_result": "verification/RESULTS.json",
        "ppa_result": "ppa/RESULTS.json",
        "prototype_result": "prototype/RESULTS.json",
        "benchmark_result": "benchmark/RESULTS.json",
        "raw_artifacts": ["signoff/raw/reproduce.log"],
    }
    if delivery_level == "tapeout":
        _write(root / "signoff/raw/antenna.rpt")
        _write(root / "signoff/raw/io-package.txt")
        _write(root / "signoff/raw/foundry-checks.rpt")
        signoff["tapeout_readiness"] = {
            "sta": "pass",
            "drc": "pass",
            "lvs": "pass",
            "antenna": "pass",
            "io_package": "pass",
            "foundry_checks": "pass",
        }
        signoff["tapeout_artifacts"] = [
            "signoff/raw/antenna.rpt",
            "signoff/raw/io-package.txt",
            "signoff/raw/foundry-checks.rpt",
        ]
    if delivery_level in {"pre_tapeout", "tapeout"}:
        pre_tapeout_artifacts = [
            "signoff/raw/lint.rpt",
            "signoff/raw/cdc-rdc.rpt",
            "signoff/raw/formal-lec.rpt",
            "signoff/raw/dft-atpg.rpt",
            "signoff/raw/synthesis.rpt",
            "signoff/raw/floorplan-pdn.rpt",
            "signoff/raw/place-cts-route.rpt",
            "signoff/raw/extraction-sta-power.rpt",
            "signoff/raw/si-ir-em.rpt",
            "signoff/raw/drc-lvs-antenna.rpt",
        ]
        for relative in pre_tapeout_artifacts:
            _write(root / relative)
        signoff["pre_tapeout_readiness"] = {
            key: "pass"
            for key in (
                "lint",
                "cdc",
                "rdc",
                "formal",
                "equivalence",
                "dft",
                "scan_atpg",
                "synthesis",
                "floorplan",
                "pdn",
                "placement",
                "cts",
                "routing",
                "extraction",
                "sta",
                "power",
                "signal_integrity",
                "ir_drop",
                "em",
                "drc",
                "lvs",
                "antenna",
            )
        }
        signoff["pre_tapeout_artifacts"] = pre_tapeout_artifacts
    manifest_paths = [
        "design/CHIP_SCOPE.json",
        "design/WORKLOAD.md",
        "design/SPEC.md",
        "design/ARCHITECTURE.md",
        "design/MEMORY_MODEL.json",
        "design/BASELINE_PLAN.md",
        "design/TARGET.json",
        "design/RTL_MANIFEST.json",
        "rtl/top.sv",
        "verification/PLAN.md",
        "reference/argus_npu_reference.py",
        "ppa/PROTOCOL.md",
        "constraints/top.sdc",
        "benchmark/PROTOCOL.md",
        "RESULTS.md",
        "Makefile",
        "research/ENVIRONMENT_AUDIT.json",
        "verification/RESULTS.json",
        "verification/raw/sim.log",
        "ppa/RESULTS.json",
        "ppa/raw/synth.rpt",
        "prototype/RESULTS.json",
        "prototype/raw/build.log",
        "benchmark/RESULTS.json",
        "benchmark/raw/results.csv",
        "signoff/raw/reproduce.log",
    ]
    if delivery_level in {"gds", "pre_tapeout", "tapeout"}:
        manifest_paths.extend(
            [
                "physical/design.gds",
                "physical/design.extracted.v",
                "physical/sta.rpt",
                "physical/drc.rpt",
                "physical/lvs.rpt",
            ]
        )
    if delivery_level == "tapeout":
        manifest_paths.extend(signoff["tapeout_artifacts"])
    if delivery_level in {"pre_tapeout", "tapeout"}:
        manifest_paths.extend(signoff["pre_tapeout_artifacts"])
    _write_json(root / "signoff/SIGNOFF.json", signoff)
    manifest_paths.append("signoff/SIGNOFF.json")
    _write_json(
        root / "signoff/ARTIFACT_MANIFEST.json",
        {
            "status": "pass",
            "artifacts": [
                {"path": relative, "sha256": _digest(root / relative)}
                for relative in manifest_paths
            ],
        },
    )
    return root


def test_chip_design_is_registered_and_staged() -> None:
    assert "chip_design" in VERTICALS
    assert set(VERTICAL_PURPOSES) == set(VERTICALS)
    assert "RTL" in VERTICAL_PURPOSES["chip_design"]
    assert require_vertical("chip_design") == "chip_design"
    module = load_vertical("chip_design")
    assert tuple(module.STAGE_ORDER) == STAGES
    assert tuple(module.STAGE_CHECKS) == STAGES
    assert tuple(module.REVIEWER_CHECKLISTS) == STAGES
    assert vertical_completion_gate(module) == "metric"
    assert vertical_workflow_mode(module) == "proportional"
    assert vertical_requires_independent_review(module) is True
    assert Manager._kind_for("chip_design") == "custom"
    assert "chip_design" not in _OPTIMIZE_VERTICALS


def test_chip_design_checklists_load_without_paper_contract(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "chip_design")
    for stage in STAGES:
        contract = resolve_stage_checklist_contract(stage, project_root=tmp_path)
        assert contract.state is ChecklistLoadState.LOADED
        assert contract.checklist_optional is False
        assert contract.items
    text = format_full_pipeline_checklist(role="reviewer", project_root=tmp_path)
    assert "definition.delivery-scope" in text
    assert "architecture.compute-memory-model" in text
    assert "architecture.area-reuse-plan" in text
    assert "verification.independent-oracle" in text
    assert "ppa.physical-closure" in text
    assert "ppa.incremental-area-reserve" in text
    assert "signoff.provenance-autonomy" in text
    assert "### submission" not in text


def test_chip_design_banners_distinguish_delivery_levels() -> None:
    module = load_vertical("chip_design")
    manager = vertical_role_banner(module, "manager")
    planner = vertical_role_banner(module, "planner")
    engineer = vertical_role_banner(module, "engineer")
    reviewer = vertical_role_banner(module, "reviewer")
    for banner in (manager, planner, engineer, reviewer):
        assert "MISSION TYPE: CHIP / ACCELERATOR DESIGN" in banner
        assert "NOT ordinary software work" in banner
        assert "FPGA" in banner
        assert "tapeout" in banner
    assert "roofline/Amdahl" in planner
    assert "roll back directly to rtl" in manager
    assert "Do not rewrite, rehash, or recertify stable" in manager
    assert "fast capability loop" in manager
    assert "current design/RTL_MANIFEST.json source revision" in manager
    assert "RTL_MANIFEST.json hash" not in manager
    assert "Intermediate operator groups" in manager
    assert "complete model/system demonstration" in manager
    assert "freeze that family once" in manager
    assert "do not reopen earlier stages" in manager
    assert "never schedule per-operator environment refreshes" in planner
    assert "exactly one bounded RTL task" in planner
    assert "Do not emit separate verification-stage or PPA-stage closeout tasks" in planner
    assert "stage_closing=false" in manager
    assert "operator-owned contracts" in manager
    assert "do not route implementation through a proposed cap" in planner
    assert "independent executable model" in engineer
    assert "different-node PPA" in reviewer
    assert "Reviewer acceptance alone cannot change" in reviewer
    assert "mission completion, not permission to advance" in reviewer
    assert "do not launch a second full Yosys/ABC PPA" in reviewer


def test_chip_design_skills_include_chip_and_digital_circuit_layers() -> None:
    skills = dict(iter_vertical_skill_texts("chip_design"))
    assert "engineer/chip-design-environment-first.md" in skills
    assert "reviewer/chip-design-signoff-review.md" in skills
    assert "engineer/digital-circuit-rtl-verification.md" in skills
    assert "reviewer/digital-circuit-signoff-review.md" in skills
    assert "bytes per token" in skills["engineer/chip-design-environment-first.md"]
    assert "Delivery-level gate" in skills["reviewer/chip-design-signoff-review.md"]


def test_reviewer_skill_paths_exist() -> None:
    module = load_vertical("chip_design")
    root = (
        Path(__file__).resolve().parents[2]
        / "argus_skill"
        / "verticals"
        / "chip_design"
        / "skills"
    )
    missing = [
        f"{stage}: {path}"
        for stage, (path, _instructions, _artifacts) in module.REVIEWER_CHECKLISTS.items()
        if not (root / path).is_file()
    ]
    assert missing == []


def test_all_structured_evidence_validators_accept_complete_rtl_ip(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    for name, validator in VALIDATORS.items():
        assert validator(root).is_file(), name


def test_prototype_na_is_rejected_for_fpga_delivery(tmp_path: Path) -> None:
    root = _complete_project(tmp_path, delivery_level="fpga")
    _write_json(
        root / "prototype/RESULTS.json",
        {"status": "not_applicable", "reason": "board unavailable"},
    )
    with pytest.raises(EvidenceError, match="cannot be N/A"):
        VALIDATORS["prototype"](root)


def test_verification_rejects_failed_command_even_with_pass_status(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    payload = json.loads((root / "verification/RESULTS.json").read_text(encoding="utf-8"))
    payload["commands"][0]["exit_code"] = 1
    _write_json(root / "verification/RESULTS.json", payload)
    with pytest.raises(EvidenceError, match="contradictory failure evidence"):
        VALIDATORS["verification"](root)


def test_architecture_rejects_nested_failure_evidence(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    path = root / "design/MEMORY_MODEL.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bandwidth_model"]["status"] = "failed"
    _write_json(path, payload)
    with pytest.raises(EvidenceError, match="architecture contains failure evidence"):
        VALIDATORS["architecture"](root)


def test_ppa_requires_nonempty_power_evidence_or_reason(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    path = root / "ppa/RESULTS.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["power"] = {}
    _write_json(path, payload)
    with pytest.raises(EvidenceError, match="power object"):
        VALIDATORS["ppa"](root)


def test_tapeout_signoff_requires_full_closure(tmp_path: Path) -> None:
    root = _complete_project(tmp_path, delivery_level="tapeout")
    payload = json.loads((root / "signoff/SIGNOFF.json").read_text(encoding="utf-8"))
    payload["tapeout_readiness"]["lvs"] = "hold"
    _write_json(root / "signoff/SIGNOFF.json", payload)
    with pytest.raises(EvidenceError, match=r"tapeout_readiness\.lvs"):
        VALIDATORS["signoff"](root)


def test_tapeout_also_requires_pre_tapeout_closure(tmp_path: Path) -> None:
    root = _complete_project(tmp_path, delivery_level="tapeout")
    payload = json.loads((root / "signoff/SIGNOFF.json").read_text(encoding="utf-8"))
    payload["pre_tapeout_readiness"]["ir_drop"] = "hold"
    _write_json(root / "signoff/SIGNOFF.json", payload)
    with pytest.raises(EvidenceError, match=r"pre_tapeout_readiness\.ir_drop"):
        VALIDATORS["signoff"](root)


def test_pre_tapeout_signoff_requires_all_computer_executable_checks(
    tmp_path: Path,
) -> None:
    root = _complete_project(tmp_path, delivery_level="pre_tapeout")
    payload = json.loads((root / "signoff/SIGNOFF.json").read_text(encoding="utf-8"))
    payload["pre_tapeout_readiness"]["scan_atpg"] = "hold"
    _write_json(root / "signoff/SIGNOFF.json", payload)
    with pytest.raises(EvidenceError, match=r"pre_tapeout_readiness\.scan_atpg"):
        VALIDATORS["signoff"](root)


def test_chip_tool_registry_is_valid_and_queryable() -> None:
    registry = load_registry()
    assert validate_registry(registry) == []
    accelerators = filter_entries(registry, categories=["accelerator_ip"])
    assert {entry["id"] for entry in accelerators} >= {"gemmini", "nvdla", "tvm_vta"}
    physical = filter_entries(registry, categories=["physical_design"])
    assert {entry["id"] for entry in physical} >= {"openroad", "librelane"}
    pdks = filter_entries(registry, categories=["pdk"])
    assert {entry["id"] for entry in pdks} >= {"sky130", "ihp_sg13g2", "gf180mcu"}
    ppa_platforms = filter_entries(registry, categories=["ppa"])
    assert {entry["id"] for entry in ppa_platforms} >= {
        "openroad_flow_scripts",
        "nangate45",
        "asap7",
    }


def test_environment_audit_derives_delivery_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _complete_project(tmp_path)
    available_ids = {
        "verilator",
        "iverilog",
        "verible",
        "yosys",
    }

    def fake_probe(
        registry: dict,
        *,
        target_python: str,
        project_root: Path,
    ) -> list[dict]:
        del target_python, project_root
        return [
            {
                "id": entry["id"],
                "available": entry["id"] in available_ids,
                "executables": (
                    {
                        command: f"/tools/{command}"
                        for command in entry.get("executables", [])
                    }
                    if entry["id"] in available_ids
                    else {}
                ),
                "source_markers": [],
            }
            for entry in registry["entries"]
        ]

    monkeypatch.setattr(environment_audit, "probe_entries", fake_probe)
    payload = environment_audit.collect(root, target_python=sys.executable, required=[])
    assert payload["required_capabilities"] == ["simulation", "lint", "synthesis"]
    assert payload["ready"] is True
    _write_json(root / environment_audit.DEFAULT_REPORT, payload)
    ok, errors = environment_audit.check(root)
    assert ok is True
    assert errors == []


def test_environment_audit_report_is_sanitized(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    payload = json.loads(
        (root / environment_audit.DEFAULT_REPORT).read_text(encoding="utf-8")
    )

    assert payload["project_root"] == "."
    assert payload["runtime"]["python"] == Path(sys.executable).name
    assert "cwd" not in payload["runtime"]
    serialized = json.dumps(payload)
    assert str(root.resolve()) not in serialized
    assert "/tools/" not in serialized
    assert "/pdks/" not in serialized


def test_environment_audit_never_executes_recorded_target_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _complete_project(tmp_path)
    path = root / environment_audit.DEFAULT_REPORT
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["runtime"]["python"] = "/project/malicious-python"
    _write_json(path, payload)
    targets: list[str] = []

    def fake_probe(
        registry: dict,
        *,
        target_python: str,
        project_root: Path,
    ) -> list[dict]:
        del project_root
        targets.append(target_python)
        return [
            {
                "id": entry["id"],
                "available": True,
                "executables": {
                    command: f"/tools/{command}"
                    for command in entry.get("executables", [])
                },
                "source_markers": (
                    [f"/pdks/{entry['id']}"]
                    if entry["id"] in {"sky130", "ihp_sg13g2", "gf180mcu"}
                    else []
                ),
            }
            for entry in registry["entries"]
        ]

    monkeypatch.setattr(environment_audit, "probe_entries", fake_probe)
    trusted_python = str(root / ".venv" / "bin" / "python")
    ok, errors = environment_audit.check(
        root,
        target_python=trusted_python,
    )

    assert ok, errors
    assert targets == [trusted_python]


def test_environment_audit_unknown_capability_fails_without_crashing(
    tmp_path: Path,
) -> None:
    root = _complete_project(tmp_path)
    path = root / environment_audit.DEFAULT_REPORT
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["required_capabilities"].append("malicious_unknown")
    _write_json(path, payload)

    ok, errors = environment_audit.check(root)

    assert ok is False
    assert any("unknown required capabilities" in error for error in errors)


def test_environment_audit_fails_closed_when_required_tool_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _complete_project(tmp_path, delivery_level="gds")

    def fake_probe(
        registry: dict,
        *,
        target_python: str,
        project_root: Path,
    ) -> list[dict]:
        del target_python, project_root
        return [
            {
                "id": entry["id"],
                "available": False,
                "executables": {},
                "source_markers": [],
            }
            for entry in registry["entries"]
        ]

    monkeypatch.setattr(environment_audit, "probe_entries", fake_probe)
    payload = environment_audit.collect(root, target_python=sys.executable, required=[])
    assert payload["ready"] is False
    assert payload["capabilities"]["physical_design"]["ready"] is False
    _write_json(root / environment_audit.DEFAULT_REPORT, payload)
    ok, errors = environment_audit.check(root)
    assert ok is False
    assert any("physical_design" in error for error in errors)


def test_environment_audit_rejects_report_that_omits_scope_requirements(
    tmp_path: Path,
) -> None:
    root = _complete_project(tmp_path)
    _write_json(
        root / environment_audit.DEFAULT_REPORT,
        {
            "schema_version": environment_audit.SCHEMA_VERSION,
            "project_root": str(root.resolve()),
            "delivery_level": "rtl_ip",
            "selected_pdk": "sky130",
            "required_capabilities": [],
            "ready": True,
            "capabilities": {},
            "detected_tools": [],
        },
    )
    ok, errors = environment_audit.check(root)
    assert ok is False
    assert any("required_capabilities" in error for error in errors)


def test_environment_audit_rejects_symlink_output(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("unchanged\n", encoding="utf-8")
    output = tmp_path / "research/ENVIRONMENT_AUDIT.json"
    output.parent.mkdir(parents=True)
    output.symlink_to(outside)
    with pytest.raises(ValueError, match="must not be a symlink"):
        environment_audit._safe_output_path(  # noqa: SLF001 - security regression
            tmp_path,
            Path("research/ENVIRONMENT_AUDIT.json"),
        )
    assert outside.read_text(encoding="utf-8") == "unchanged\n"


def test_gds_ppa_requires_physical_artifacts(tmp_path: Path) -> None:
    root = _complete_project(tmp_path, delivery_level="gds")
    payload = json.loads((root / "ppa/RESULTS.json").read_text(encoding="utf-8"))
    payload.pop("layout_artifacts")
    _write_json(root / "ppa/RESULTS.json", payload)
    with pytest.raises(EvidenceError, match="layout_artifacts"):
        VALIDATORS["ppa"](root)


def test_ppa_rejects_stale_rtl_manifest_binding(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    manifest = root / "design/RTL_MANIFEST.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["parameters"].append("ROWS=128")
    _write_json(manifest, payload)
    with pytest.raises(EvidenceError, match="source_hashes is stale"):
        VALIDATORS["ppa"](root)


def test_ppa_rejects_stale_rtl_source_binding(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    _write(root / "rtl/top.sv", "module top(input logic clk); logic changed; endmodule\n")
    with pytest.raises(EvidenceError, match="source_hashes is stale for rtl/top.sv"):
        VALIDATORS["ppa"](root)


def test_ppa_rejects_stale_target_binding(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    _write_json(
        root / "design/TARGET.json",
        {"status": "ready", "target": "nangate45"},
    )
    with pytest.raises(EvidenceError, match="source_hashes is stale for design/TARGET.json"):
        VALIDATORS["ppa"](root)


def test_ppa_rejects_stale_constraint_binding(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    _write(root / "constraints/top.sdc", "create_clock -period 8 [get_ports clk]\n")
    with pytest.raises(EvidenceError, match="source_hashes is stale for constraints/top.sdc"):
        VALIDATORS["ppa"](root)


def test_prototype_rejects_stale_rtl_source_binding(tmp_path: Path) -> None:
    root = _complete_project(tmp_path, delivery_level="fpga")
    _write(root / "rtl/top.sv", "module top(input logic clk); logic changed; endmodule\n")
    with pytest.raises(EvidenceError, match="source_hashes is stale for rtl/top.sv"):
        VALIDATORS["prototype"](root)


def test_signoff_revalidates_referenced_results(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    payload = json.loads((root / "verification/RESULTS.json").read_text(encoding="utf-8"))
    payload["status"] = "fail"
    _write_json(root / "verification/RESULTS.json", payload)
    with pytest.raises(EvidenceError, match="verification must pass"):
        VALIDATORS["signoff"](root)


def test_signoff_prototype_status_must_match_result(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    payload = json.loads((root / "signoff/SIGNOFF.json").read_text(encoding="utf-8"))
    payload["stage_results"]["prototype"] = "pass"
    _write_json(root / "signoff/SIGNOFF.json", payload)
    with pytest.raises(EvidenceError, match="stage_results.prototype does not match"):
        VALIDATORS["signoff"](root)


def test_signoff_rejects_claim_above_delivery_level(tmp_path: Path) -> None:
    root = _complete_project(tmp_path)
    payload = json.loads((root / "signoff/SIGNOFF.json").read_text(encoding="utf-8"))
    payload["claims"] = [{"level": "tapeout", "statement": "tapeout ready"}]
    _write_json(root / "signoff/SIGNOFF.json", payload)
    with pytest.raises(EvidenceError, match="exceeds delivery_level"):
        VALIDATORS["signoff"](root)
