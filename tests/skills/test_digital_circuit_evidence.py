from __future__ import annotations

import json

import pytest

from argus_skill.verticals.digital_circuit.evidence import (
    EvidenceError,
    validate_benchmark_interface,
    validate_preflight,
    validate_verification_results,
)


def _json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_verification_rejects_success_text_with_failure_evidence(tmp_path):
    log = tmp_path / "verification" / "simulation.log"
    log.parent.mkdir()
    log.write_text("SUCCESS but actually failed on reset\n", encoding="utf-8")
    with pytest.raises(EvidenceError):
        validate_verification_results(tmp_path)

    _json(
        tmp_path / "verification" / "result.json",
        {"status": "pass", "failed": 1},
    )
    with pytest.raises(EvidenceError):
        validate_verification_results(tmp_path)

    log.unlink()
    _json(
        tmp_path / "verification" / "result.json",
        {"status": "pass", "failed": 0},
    )
    assert validate_verification_results(tmp_path).name == "result.json"


def test_verification_accepts_status_log_and_rejects_nested_failure(tmp_path):
    log = tmp_path / "verification" / "simulation.log"
    log.parent.mkdir()
    log.write_text("status: pass\n0 failures\n", encoding="utf-8")
    assert validate_verification_results(tmp_path) == log

    log.unlink()
    _json(
        tmp_path / "verification" / "result.json",
        {"status": "pass", "tests": [{"status": "failed"}]},
    )
    with pytest.raises(EvidenceError):
        validate_verification_results(tmp_path)


def test_verification_rejects_failure_nested_under_outputs(tmp_path):
    _json(
        tmp_path / "verification" / "result.json",
        {
            "status": "pass",
            "outputs": [{"status": "failed", "returncode": 1}],
        },
    )
    with pytest.raises(EvidenceError):
        validate_verification_results(tmp_path)


def test_verification_rejects_result_symlink_outside_project(tmp_path):
    outside = tmp_path.parent / "outside-pass.log"
    outside.write_text("status: pass\n", encoding="utf-8")
    link = tmp_path / "verification" / "result.log"
    link.parent.mkdir()
    link.symlink_to(outside)
    with pytest.raises(EvidenceError):
        validate_verification_results(tmp_path)


def test_benchmark_interface_requires_contract_fields(tmp_path):
    path = tmp_path / "design" / "BENCHMARK_INTERFACE.json"
    _json(path, {"status": "ready"})
    with pytest.raises(EvidenceError):
        validate_benchmark_interface(tmp_path)

    _json(
        path,
        {
            "status": "ready",
            "top_module": "dut",
            "output_path": "rtl/dut.sv",
            "ports": [{"name": "clk", "direction": "input"}],
        },
    )
    assert validate_benchmark_interface(tmp_path) == path


def test_benchmark_interface_allows_port_named_error(tmp_path):
    path = tmp_path / "design" / "BENCHMARK_INTERFACE.json"
    _json(
        path,
        {
            "status": "ready",
            "top_module": "dut",
            "output_path": "rtl/dut.sv",
            "ports": {"error": {"direction": "output", "width": 1}},
        },
    )
    assert validate_benchmark_interface(tmp_path) == path


def test_fixed_evidence_files_cannot_resolve_outside_project(tmp_path):
    outside = tmp_path.parent / "outside-interface.json"
    _json(
        outside,
        {
            "status": "ready",
            "top_module": "dut",
            "output_path": "rtl/dut.sv",
            "ports": [{"name": "clk", "direction": "input"}],
        },
    )
    link = tmp_path / "design" / "BENCHMARK_INTERFACE.json"
    link.parent.mkdir()
    link.symlink_to(outside)
    with pytest.raises(EvidenceError, match="outside"):
        validate_benchmark_interface(tmp_path)


def test_preflight_rejects_contradictions_and_validates_references(tmp_path):
    path = tmp_path / "evidence" / "preflight.json"
    _json(
        path,
        {
            "status": "pass",
            "error": "elaboration failed",
            "top_modules": ["dut"],
            "rtl_files": ["rtl/dut.sv"],
            "output_paths": ["rtl/answer.sv"],
            "compile_results": [{"returncode": 0}],
        },
    )
    with pytest.raises(EvidenceError):
        validate_preflight(tmp_path)

    for rel in ("rtl/dut.sv", "rtl/answer.sv"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("module dut; endmodule\n", encoding="utf-8")
    _json(
        path,
        {
            "status": "pass",
            "top_modules": ["dut"],
            "rtl_files": ["rtl/dut.sv"],
            "output_paths": ["rtl/answer.sv"],
            "compile_results": [{"returncode": 0}],
        },
    )
    assert validate_preflight(tmp_path) == path


def test_preflight_rejects_reference_resolving_outside_project(tmp_path):
    outside = tmp_path.parent / "outside-rtl.sv"
    outside.write_text("module hidden; endmodule\n", encoding="utf-8")
    link = tmp_path / "rtl" / "dut.sv"
    link.parent.mkdir()
    link.symlink_to(outside)
    answer = tmp_path / "rtl" / "answer.sv"
    answer.write_text("module answer; endmodule\n", encoding="utf-8")
    _json(
        tmp_path / "evidence" / "preflight.json",
        {
            "status": "pass",
            "top_modules": ["dut"],
            "rtl_files": ["rtl/dut.sv"],
            "output_paths": ["rtl/answer.sv"],
            "compile_results": [{"returncode": 0}],
        },
    )
    with pytest.raises(EvidenceError, match="outside"):
        validate_preflight(tmp_path)
