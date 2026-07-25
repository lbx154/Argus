"""Fail-closed structured evidence checks for chip-design stage gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DELIVERY_LEVELS = {"rtl_ip", "fpga", "gds", "pre_tapeout", "tapeout"}
PASS_STATUSES = {"pass", "passed", "ready", "success", "proved"}
NA_STATUSES = {"not_applicable", "n/a", "na"}


class EvidenceError(ValueError):
    """Raised when chip-design evidence is absent, unsafe, or contradictory."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceError(f"{path}: expected a JSON object")
    return payload


def _project_file(project_root: Path, relative: str) -> Path:
    root = project_root.resolve()
    rel = Path(str(relative))
    if rel.is_absolute() or ".." in rel.parts:
        raise EvidenceError(f"unsafe project-relative path: {relative!r}")
    try:
        resolved = (root / rel).resolve(strict=True)
    except OSError as exc:
        raise EvidenceError(f"{relative}: missing or unreadable: {exc}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise EvidenceError(f"{relative}: resolves outside the project or is not a file")
    if resolved.stat().st_size == 0:
        raise EvidenceError(f"{relative}: file is empty")
    return resolved


def _payload(project_root: Path, relative: str) -> tuple[Path, dict[str, Any]]:
    path = _project_file(project_root, relative)
    return path, _load_object(path)


def _status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status") or payload.get("result") or "").strip().lower()


def _require_text(payload: Mapping[str, Any], key: str, path: Path) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise EvidenceError(f"{path}: non-empty {key!r} is required")
    return value


def _require_mapping(payload: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping) or not value:
        raise EvidenceError(f"{path}: non-empty object {key!r} is required")
    return value


def _require_list(payload: Mapping[str, Any], key: str, path: Path) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise EvidenceError(f"{path}: non-empty list {key!r} is required")
    return value


def _paths(payload: Mapping[str, Any], *keys: str) -> list[str]:
    result: list[str] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
        elif isinstance(value, list):
            result.extend(str(item).strip() for item in value if str(item).strip())
    return result


def _require_project_files(project_root: Path, payload: Mapping[str, Any], *keys: str) -> list[Path]:
    values = _paths(payload, *keys)
    if not values:
        raise EvidenceError(f"one of {keys!r} must reference at least one project file")
    return [_project_file(project_root, value) for value in values]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_current_source_hashes(
    project_root: Path,
    payload: Mapping[str, Any],
    result_path: Path,
    *,
    required_paths: tuple[str, ...] = ("design/RTL_MANIFEST.json",),
) -> None:
    raw = payload.get("source_hashes")
    bindings: dict[str, str] = {}
    if isinstance(raw, Mapping):
        bindings = {
            str(relative): str(digest).strip().lower()
            for relative, digest in raw.items()
            if str(relative).strip()
        }
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            relative = str(entry.get("path") or "").strip()
            digest = str(entry.get("sha256") or "").strip().lower()
            if relative:
                bindings[relative] = digest
    else:
        raise EvidenceError(f"{result_path}: source_hashes must be an object or list")

    required = list(required_paths)
    manifest = _load_object(_project_file(project_root, "design/RTL_MANIFEST.json"))
    required.extend(_paths(manifest, "source_files", "generated_sources"))
    for relative in dict.fromkeys(required):
        expected = bindings.get(relative, "")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise EvidenceError(
                f"{result_path}: source_hashes must bind {relative} with SHA-256"
            )
        source = _project_file(project_root, relative)
        if _sha256(source) != expected:
            raise EvidenceError(
                f"{result_path}: source_hashes is stale for {relative}"
            )


def _has_failure(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for raw_key, value in payload.items():
            key = str(raw_key).strip().lower()
            if key in {"error", "errors", "failure", "failures", "failed"} and value not in (
                None,
                "",
                0,
                False,
                [],
                {},
            ):
                return True
            if key in {"status", "result", "verdict"} and str(value or "").strip().lower() in {
                "fail",
                "failed",
                "failure",
                "error",
                "fatal",
            }:
                return True
            if key in {"returncode", "exit_code"} and (isinstance(value, bool) or value != 0):
                return True
            if _has_failure(value):
                return True
    elif isinstance(payload, list):
        return any(_has_failure(value) for value in payload)
    return False


def _scope(project_root: Path) -> Path:
    path, payload = _payload(project_root, "design/CHIP_SCOPE.json")
    if _status(payload) not in {"ready", "pass"}:
        raise EvidenceError(f"{path}: status must be ready/pass")
    level = _require_text(payload, "delivery_level", path)
    if level not in DELIVERY_LEVELS:
        raise EvidenceError(f"{path}: delivery_level must be one of {sorted(DELIVERY_LEVELS)}")
    for key in ("target", "workload", "technology_or_board"):
        _require_text(payload, key, path)
    _require_list(payload, "interfaces", path)
    _require_list(payload, "numerical_formats", path)
    _require_mapping(payload, "acceptance_metrics", path)
    _require_list(payload, "baselines", path)
    _require_list(payload, "non_goals", path)
    if _has_failure(payload):
        raise EvidenceError(f"{path}: ready scope contains failure evidence")
    return path


def _architecture(project_root: Path) -> Path:
    path, payload = _payload(project_root, "design/MEMORY_MODEL.json")
    if _status(payload) not in {"ready", "pass"}:
        raise EvidenceError(f"{path}: status must be ready/pass")
    for key in ("workload_shapes", "memory_hierarchy", "traffic_bytes", "arithmetic_intensity"):
        value = payload.get(key)
        if value in (None, "", [], {}):
            raise EvidenceError(f"{path}: {key!r} is required")
    _require_mapping(payload, "bandwidth_model", path)
    _require_mapping(payload, "capacity_model", path)
    _require_list(payload, "assumptions", path)
    if _has_failure(payload):
        raise EvidenceError(f"{path}: ready architecture contains failure evidence")
    _project_file(project_root, "design/ARCHITECTURE.md")
    _project_file(project_root, "design/BASELINE_PLAN.md")
    return path


def _rtl(project_root: Path) -> Path:
    path, payload = _payload(project_root, "design/RTL_MANIFEST.json")
    if _status(payload) not in {"ready", "pass"}:
        raise EvidenceError(f"{path}: status must be ready/pass")
    _require_list(payload, "top_modules", path)
    _require_list(payload, "clock_domains", path)
    _require_list(payload, "interfaces", path)
    _require_list(payload, "parameters", path)
    _require_list(payload, "provenance", path)
    _require_project_files(project_root, payload, "source_files", "generated_sources")
    if _has_failure(payload):
        raise EvidenceError(f"{path}: ready manifest contains failure evidence")
    return path


def _verification(project_root: Path) -> Path:
    from ..digital_circuit.evidence import validate_verification_sources

    validate_verification_sources(project_root)
    path, payload = _payload(project_root, "verification/RESULTS.json")
    if _status(payload) not in PASS_STATUSES or _has_failure(payload):
        raise EvidenceError(f"{path}: verification must pass without contradictory failure evidence")
    commands = _require_list(payload, "commands", path)
    for index, command in enumerate(commands):
        if not isinstance(command, Mapping) or isinstance(command.get("exit_code"), bool):
            raise EvidenceError(f"{path}: commands[{index}] must record an integer exit_code")
        if command.get("exit_code") != 0:
            raise EvidenceError(f"{path}: commands[{index}] did not exit successfully")
    _require_mapping(payload, "coverage", path)
    _require_list(payload, "scenarios", path)
    _require_mapping(payload, "numerical", path)
    _require_project_files(project_root, payload, "raw_artifacts")
    _require_current_source_hashes(project_root, payload, path)
    return path


def _ppa(project_root: Path) -> Path:
    path, payload = _payload(project_root, "ppa/RESULTS.json")
    if _status(payload) not in PASS_STATUSES or _has_failure(payload):
        raise EvidenceError(f"{path}: PPA evidence must pass without contradictory failures")
    for key in ("target", "toolchain", "configuration"):
        _require_mapping(payload, key, path)
    _require_mapping(payload, "timing", path)
    _require_mapping(payload, "area_or_resources", path)
    power = payload.get("power")
    if (
        not isinstance(power, Mapping)
        or not power
    ) and not str(payload.get("power_not_measured_reason") or "").strip():
        raise EvidenceError(f"{path}: power object or power_not_measured_reason is required")
    _require_list(payload, "warnings_and_waivers", path)
    _require_project_files(project_root, payload, "raw_artifacts")
    constraint_files = _paths(payload, "constraint_files")
    if not constraint_files:
        raise EvidenceError(f"{path}: constraint_files must reference the applied constraints")
    for relative in constraint_files:
        _project_file(project_root, relative)
    _require_current_source_hashes(
        project_root,
        payload,
        path,
        required_paths=(
            "design/RTL_MANIFEST.json",
            "design/TARGET.json",
            "verification/RESULTS.json",
            "ppa/PROTOCOL.md",
            *constraint_files,
        ),
    )
    level = _load_object(_project_file(project_root, "design/CHIP_SCOPE.json")).get("delivery_level")
    if level in {"gds", "pre_tapeout", "tapeout"}:
        closure = _require_mapping(payload, "physical_closure", path)
        for check in ("sta", "drc", "lvs"):
            if str(closure.get(check) or "").strip().lower() not in PASS_STATUSES:
                raise EvidenceError(f"{path}: physical_closure.{check} must pass for {level}")
        for key in (
            "layout_artifacts",
            "extracted_netlists",
            "sta_reports",
            "drc_reports",
            "lvs_reports",
        ):
            _require_project_files(project_root, payload, key)
    return path


def _prototype(project_root: Path) -> Path:
    path, payload = _payload(project_root, "prototype/RESULTS.json")
    status = _status(payload)
    scope = _load_object(_project_file(project_root, "design/CHIP_SCOPE.json"))
    level = str(scope.get("delivery_level") or "")
    if status in NA_STATUSES:
        _require_text(payload, "reason", path)
        if level == "fpga":
            raise EvidenceError(f"{path}: prototype cannot be N/A for delivery_level={level}")
        return path
    if status not in PASS_STATUSES or _has_failure(payload):
        raise EvidenceError(f"{path}: prototype must pass or be validly not_applicable")
    _require_text(payload, "prototype_kind", path)
    _require_mapping(payload, "target", path)
    _require_mapping(payload, "build", path)
    _require_mapping(payload, "correctness", path)
    _require_list(payload, "limitations", path)
    _require_project_files(project_root, payload, "raw_artifacts", "build_artifacts")
    _require_current_source_hashes(project_root, payload, path)
    return path


def _benchmark(project_root: Path) -> Path:
    path, payload = _payload(project_root, "benchmark/RESULTS.json")
    if _status(payload) not in PASS_STATUSES or _has_failure(payload):
        raise EvidenceError(f"{path}: benchmark result must pass without contradictory failures")
    correctness = _require_mapping(payload, "correctness", path)
    if str(correctness.get("status") or "").strip().lower() not in PASS_STATUSES:
        raise EvidenceError(f"{path}: benchmark correctness status must pass")
    _require_list(payload, "workloads", path)
    _require_list(payload, "baselines", path)
    metrics = _require_list(payload, "metrics", path)
    for index, metric in enumerate(metrics):
        if not isinstance(metric, Mapping):
            raise EvidenceError(f"{path}: metrics[{index}] must be an object")
        for key in ("name", "unit", "candidate", "baseline"):
            if metric.get(key) in (None, ""):
                raise EvidenceError(f"{path}: metrics[{index}].{key} is required")
    repetitions = payload.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise EvidenceError(f"{path}: repetitions must be a positive integer")
    _require_list(payload, "regressions", path)
    _require_list(payload, "limitations", path)
    comparison = _require_mapping(payload, "comparison", path)
    kind = str(comparison.get("kind") or "").strip()
    if kind not in {"matched_hardware", "system_measurement", "market_context"}:
        raise EvidenceError(f"{path}: comparison.kind is invalid")
    for key in ("same_workload", "same_quality"):
        if not isinstance(comparison.get(key), bool):
            raise EvidenceError(f"{path}: comparison.{key} must be boolean")
    if not comparison["same_workload"] or not comparison["same_quality"]:
        raise EvidenceError(f"{path}: workload and quality must match")
    if kind == "matched_hardware":
        for key in ("same_target_flow", "same_host", "same_memory_budget"):
            if comparison.get(key) is not True:
                raise EvidenceError(f"{path}: comparison.{key} must be true")
    if kind == "market_context" and comparison.get("claim") != "context_only":
        raise EvidenceError(f"{path}: market comparisons must be context_only")
    _require_mapping(payload, "candidate_config", path)
    _require_list(payload, "baseline_configs", path)
    measurement = _require_mapping(payload, "measurement", path)
    for key in (
        "warmup",
        "repetitions",
        "synchronization",
        "host_offload_partition",
        "quantization",
        "memory_budget",
        "resource_budget",
        "power_method",
        "uncertainty",
    ):
        if measurement.get(key) in (None, "", [], {}):
            raise EvidenceError(f"{path}: measurement.{key} is required")
    if measurement.get("repetitions") != repetitions:
        raise EvidenceError(f"{path}: measurement.repetitions must match repetitions")
    _require_project_files(project_root, payload, "raw_artifacts")
    _require_current_source_hashes(project_root, payload, path)
    return path


def _validate_artifact_manifest(
    project_root: Path,
    relative: str,
    required_paths: list[str],
) -> Path:
    path, payload = _payload(project_root, relative)
    if _status(payload) not in PASS_STATUSES:
        raise EvidenceError(f"{path}: artifact manifest status must pass")
    entries = _require_list(payload, "artifacts", path)
    indexed: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise EvidenceError(f"{path}: artifacts[{index}] must be an object")
        rel = str(entry.get("path") or "").strip()
        digest = str(entry.get("sha256") or "").strip().lower()
        if not rel or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise EvidenceError(f"{path}: artifacts[{index}] needs path and SHA-256")
        artifact = _project_file(project_root, rel)
        if _sha256(artifact) != digest:
            raise EvidenceError(f"{path}: SHA-256 mismatch for {rel}")
        indexed[rel] = digest
    missing = sorted(set(required_paths) - set(indexed))
    if missing:
        raise EvidenceError(f"{path}: manifest does not bind required artifacts: {missing}")
    return path


def _signoff(project_root: Path) -> Path:
    path, payload = _payload(project_root, "signoff/SIGNOFF.json")
    if _status(payload) not in PASS_STATUSES or _has_failure(payload):
        raise EvidenceError(f"{path}: sign-off must pass without contradictory failures")
    stages = _require_mapping(payload, "stage_results", path)
    mandatory = ("definition", "architecture", "environment", "rtl", "verification", "ppa", "benchmark")
    for stage in mandatory:
        if str(stages.get(stage) or "").strip().lower() not in PASS_STATUSES:
            raise EvidenceError(f"{path}: stage_results.{stage} must pass")
    prototype_status = str(stages.get("prototype") or "").strip().lower()
    if prototype_status not in PASS_STATUSES | NA_STATUSES:
        raise EvidenceError(f"{path}: stage_results.prototype must pass or be N/A")
    claims = _require_list(payload, "claims", path)
    _require_list(payload, "known_limitations", path)
    _require_mapping(payload, "provenance", path)
    _require_list(payload, "reproduction_commands", path)
    reproduction_files = _paths(payload, "reproduction_files")
    if not reproduction_files:
        raise EvidenceError(f"{path}: reproduction_files is required")
    for relative in reproduction_files:
        _project_file(project_root, relative)
    referenced = {
        key: _require_text(payload, key, path)
        for key in (
            "artifact_manifest",
            "environment_result",
            "verification_result",
            "ppa_result",
            "prototype_result",
            "benchmark_result",
        )
    }
    _require_project_files(project_root, payload, "raw_artifacts")
    _project_file(project_root, "RESULTS.md")
    _scope(project_root)
    _architecture(project_root)
    _rtl(project_root)
    verification_path = _verification(project_root)
    ppa_path = _ppa(project_root)
    prototype_path = _prototype(project_root)
    benchmark_path = _benchmark(project_root)
    actual_prototype_status = _status(_load_object(prototype_path))
    declared_prototype_na = prototype_status in NA_STATUSES
    actual_prototype_na = actual_prototype_status in NA_STATUSES
    if declared_prototype_na != actual_prototype_na:
        raise EvidenceError(
            f"{path}: stage_results.prototype does not match "
            f"{prototype_path.relative_to(project_root.resolve())} status"
        )
    expected = {
        "verification_result": verification_path,
        "ppa_result": ppa_path,
        "prototype_result": prototype_path,
        "benchmark_result": benchmark_path,
    }
    root = project_root.resolve()
    for key, expected_path in expected.items():
        actual_path = _project_file(project_root, referenced[key])
        if actual_path != expected_path:
            raise EvidenceError(f"{path}: {key} must reference {expected_path.relative_to(root)}")
    from .environment_audit import check as check_environment

    environment_path = _project_file(project_root, referenced["environment_result"])
    environment_ok, environment_errors = check_environment(
        project_root,
        environment_path.relative_to(root),
    )
    if not environment_ok:
        raise EvidenceError(f"{path}: environment audit is invalid: {'; '.join(environment_errors)}")
    level = str(
        _load_object(_project_file(project_root, "design/CHIP_SCOPE.json")).get(
            "delivery_level"
        )
        or ""
    )
    level_rank = {
        "rtl_ip": 0,
        "fpga": 1,
        "gds": 2,
        "pre_tapeout": 3,
        "tapeout": 4,
    }
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            raise EvidenceError(f"{path}: claims[{index}] must be an object")
        claim_level = str(claim.get("level") or "").strip()
        _require_text(claim, "statement", path)
        if claim_level not in level_rank:
            raise EvidenceError(f"{path}: claims[{index}].level is invalid")
        if level_rank[claim_level] > level_rank[level]:
            raise EvidenceError(
                f"{path}: claims[{index}] exceeds delivery_level={level}"
            )
    if level in {"pre_tapeout", "tapeout"}:
        readiness = _require_mapping(payload, "pre_tapeout_readiness", path)
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
        ):
            if str(readiness.get(key) or "").strip().lower() not in PASS_STATUSES:
                raise EvidenceError(f"{path}: pre_tapeout_readiness.{key} must pass")
        _require_project_files(project_root, payload, "pre_tapeout_artifacts")
    if level == "tapeout":
        tapeout = _require_mapping(payload, "tapeout_readiness", path)
        for key in ("sta", "drc", "lvs", "antenna", "io_package", "foundry_checks"):
            if str(tapeout.get(key) or "").strip().lower() not in PASS_STATUSES:
                raise EvidenceError(f"{path}: tapeout_readiness.{key} must pass")
        _require_project_files(project_root, payload, "tapeout_artifacts")
    required_manifest_paths = [
        "design/CHIP_SCOPE.json",
        "design/WORKLOAD.md",
        "design/SPEC.md",
        "design/ARCHITECTURE.md",
        "design/MEMORY_MODEL.json",
        "design/BASELINE_PLAN.md",
        "design/TARGET.json",
        "design/RTL_MANIFEST.json",
        "verification/PLAN.md",
        "ppa/PROTOCOL.md",
        "benchmark/PROTOCOL.md",
        "signoff/SIGNOFF.json",
        "RESULTS.md",
        referenced["environment_result"],
        referenced["verification_result"],
        referenced["ppa_result"],
        referenced["prototype_result"],
        referenced["benchmark_result"],
        *reproduction_files,
        *_paths(payload, "raw_artifacts"),
    ]
    rtl_payload = _load_object(_project_file(project_root, "design/RTL_MANIFEST.json"))
    required_manifest_paths.extend(_paths(rtl_payload, "source_files", "generated_sources"))
    for result_path, keys in (
        (verification_path, ("raw_artifacts",)),
        (ppa_path, ("raw_artifacts",)),
        (prototype_path, ("raw_artifacts", "build_artifacts")),
        (benchmark_path, ("raw_artifacts",)),
    ):
        required_manifest_paths.extend(_paths(_load_object(result_path), *keys))
    if level in {"gds", "pre_tapeout", "tapeout"}:
        ppa_payload = _load_object(ppa_path)
        required_manifest_paths.extend(
            _paths(
                ppa_payload,
                "layout_artifacts",
                "extracted_netlists",
                "sta_reports",
                "drc_reports",
                "lvs_reports",
            )
        )
    if level in {"pre_tapeout", "tapeout"}:
        required_manifest_paths.extend(_paths(payload, "pre_tapeout_artifacts"))
    if level == "tapeout":
        required_manifest_paths.extend(_paths(payload, "tapeout_artifacts"))
    _validate_artifact_manifest(
        project_root,
        referenced["artifact_manifest"],
        required_manifest_paths,
    )
    return path


VALIDATORS = {
    "scope": _scope,
    "architecture": _architecture,
    "rtl": _rtl,
    "verification": _verification,
    "ppa": _ppa,
    "prototype": _prototype,
    "benchmark": _benchmark,
    "signoff": _signoff,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-design-evidence")
    parser.add_argument("check", choices=tuple(VALIDATORS))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    try:
        path = VALIDATORS[args.check](root)
    except EvidenceError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: validated {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
