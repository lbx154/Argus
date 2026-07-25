"""Fail-closed evidence checks for digital-circuit stage gates."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class EvidenceError(ValueError):
    """Raised when hardware evidence is missing or contradictory."""


_SUCCESS = {"pass", "passed", "proved", "unsat", "success"}
_FAILURE = {"fail", "failed", "failure", "error", "errors", "fatal"}
_FAILURE_KEYS = {"error", "errors", "failure", "failures", "failed"}
_STRUCTURAL_KEYS = {
    "inputs",
    "input_schema",
    "outputs",
    "output_mapping",
    "output_schema",
    "parameters",
    "ports",
}
_SUCCESS_LINE = re.compile(
    r"^\s*(?:(?:pass|passed|proved|unsat|success)(?:\s*[:\s]|$)"
    r"|status\s*:\s*(?:pass|passed|proved|unsat|success)(?:\s|$))",
    re.IGNORECASE | re.MULTILINE,
)
_FAILURE_WORD = re.compile(r"\b(?:fail|failed|failure|failures|error|errors|fatal)\b", re.IGNORECASE)


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
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceError(f"{path}: unreadable file: {exc}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise EvidenceError(f"{path}: file resolves outside the project")
    return resolved


def _has_failure(payload: object, *, ignore_structural_keys: bool = False) -> bool:
    if isinstance(payload, Mapping):
        for raw_key, value in payload.items():
            key = str(raw_key).strip().lower()
            if ignore_structural_keys and key in _STRUCTURAL_KEYS:
                continue
            if key in _FAILURE_KEYS and value not in (None, "", 0, False, [], {}):
                return True
            if key in {"status", "result", "verdict"}:
                if str(value or "").strip().lower() in _FAILURE:
                    return True
            if key in {"returncode", "exit_code"}:
                if isinstance(value, bool) or value != 0:
                    return True
            if _has_failure(value, ignore_structural_keys=ignore_structural_keys):
                return True
    elif isinstance(payload, list):
        return any(
            _has_failure(value, ignore_structural_keys=ignore_structural_keys)
            for value in payload
        )
    return False


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def compatible_string_values(
    payload: Mapping[str, Any],
    singular: str,
    plural: str,
) -> tuple[str, ...]:
    """Normalize a public schema's singular/plural spelling without ambiguity."""
    singular_values = _string_list(payload.get(singular))
    plural_values = _string_list(payload.get(plural))
    if singular_values and plural_values and singular_values != plural_values:
        raise EvidenceError(f"{singular} and {plural} disagree")
    return tuple(plural_values or singular_values)


def benchmark_output_paths(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return canonical benchmark output paths from either public spelling."""
    return compatible_string_values(payload, "output_path", "output_paths")


def _validate_interface_ports(
    path: Path,
    ports: object,
    *,
    require_typed_shape: bool,
) -> None:
    if isinstance(ports, list):
        rows = ports
    elif isinstance(ports, dict):
        rows = [
            {"name": name, **details}
            for name, details in ports.items()
            if isinstance(details, dict)
        ]
        if len(rows) != len(ports):
            raise EvidenceError(f"{path}: every port definition must be an object")
    else:
        raise EvidenceError(f"{path}: non-empty ports are required")
    if not rows:
        raise EvidenceError(f"{path}: non-empty ports are required")
    if not require_typed_shape:
        return
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise EvidenceError(f"{path}: every port definition must be an object")
        name = str(row.get("name") or "").strip()
        direction = str(row.get("direction") or "").strip().lower()
        width = row.get("width")
        valid_width = (
            isinstance(width, int)
            and not isinstance(width, bool)
            and width > 0
        ) or (isinstance(width, str) and bool(width.strip()))
        if (
            not name
            or name in names
            or direction not in {"input", "output", "inout"}
            or not valid_width
            or not isinstance(row.get("signed"), bool)
        ):
            raise EvidenceError(
                f"{path}: each unique port requires name, input/output/inout "
                "direction, exact width, and boolean signedness"
            )
        names.add(name)


def _require_project_files(project_root: Path, paths: list[str], field: str) -> None:
    root = project_root.resolve()
    for value in paths:
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts:
            raise EvidenceError(f"{field} contains an unsafe path: {value!r}")
        target = root / rel
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise EvidenceError(f"{field} references an unreadable file: {value!r}") from exc
        if not resolved.is_relative_to(root):
            raise EvidenceError(f"{field} resolves outside the project: {value!r}")
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise EvidenceError(f"{field} references a missing or empty file: {value!r}")


def validate_verification_sources(project_root: Path) -> Path:
    """Require executable verification source, not only a success-shaped report."""
    root = project_root.resolve()
    suffixes = {".v", ".sv", ".py", ".sby"}
    for relative in ("tb", "testbench", "verification", "formal", "reference"):
        base = root / relative
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if (
                resolved.is_relative_to(root)
                and resolved.is_file()
                and resolved.stat().st_size > 0
                and resolved.suffix.lower() in suffixes
            ):
                return path
    raise EvidenceError(
        "no executable verification source found under tb/, testbench/, "
        "verification/, formal/, or reference/"
    )


def validate_verification_results(project_root: Path) -> Path:
    """Return one non-contradictory passing verification result."""
    validate_verification_sources(project_root)
    root = project_root.resolve()
    candidates: list[Path] = []
    for rel in ("reports", "verification"):
        base = root / rel
        if base.is_dir():
            candidates.extend(path for path in base.rglob("*") if path.suffix.lower() in {".json", ".log"})

    for path in sorted(set(candidates)):
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = _load_object(resolved)
            except EvidenceError:
                continue
            status = str(payload.get("status") or "").strip().lower()
            if status in _SUCCESS and not _has_failure(payload):
                return path
            continue

        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError:
            continue
        normalized = re.sub(r"\b(?:0+|no)\s+(?:failed|failures|errors)\b", "", text, flags=re.I)
        if _SUCCESS_LINE.search(normalized) and not _FAILURE_WORD.search(normalized):
            return path

    raise EvidenceError(
        "no verification .json/.log has an explicit passing status without "
        "contradictory failure evidence"
    )


def validate_benchmark_interface(project_root: Path) -> Path:
    """Validate the public benchmark interface manifest structurally."""
    path = _project_file(project_root, "design/BENCHMARK_INTERFACE.json")
    payload = _load_object(path)
    if payload.get("status") != "ready":
        raise EvidenceError(f"{path}: status must be 'ready'")
    if not compatible_string_values(payload, "top_module", "top_modules"):
        raise EvidenceError(f"{path}: top_module/top_modules is required")
    if not benchmark_output_paths(payload):
        raise EvidenceError(f"{path}: output_path/output_paths is required")
    schema_version = payload.get("schema_version", 1)
    if isinstance(schema_version, bool) or schema_version not in {1, 2}:
        raise EvidenceError(f"{path}: schema_version must be 1 or 2")
    _validate_interface_ports(
        path,
        payload.get("ports"),
        require_typed_shape=schema_version == 2,
    )
    if schema_version == 2:
        ambiguities = payload.get("ambiguities")
        if not isinstance(ambiguities, list) or any(
            not isinstance(item, str) or not item.strip() for item in ambiguities
        ):
            raise EvidenceError(
                f"{path}: ambiguities must be a list of non-empty strings"
            )
        interface_change = payload.get("interface_change")
        if not isinstance(interface_change, dict) or not isinstance(
            interface_change.get("requested"), bool
        ):
            raise EvidenceError(
                f"{path}: interface_change.requested must record whether the public "
                "prompt explicitly requested an interface bug fix"
            )
        if interface_change["requested"] and not str(
            interface_change.get("public_request") or ""
        ).strip():
            raise EvidenceError(
                f"{path}: requested interface changes require public_request provenance"
            )
    if _has_failure(payload, ignore_structural_keys=True):
        raise EvidenceError(f"{path}: ready manifest contains failure evidence")
    return path


def validate_preflight(project_root: Path) -> Path:
    """Validate the documented pre-score handoff schema and referenced files."""
    path = _project_file(project_root, "evidence/preflight.json")
    payload = _load_object(path)
    if payload.get("status") != "pass":
        raise EvidenceError(f"{path}: status must be 'pass'")
    if _has_failure(payload):
        raise EvidenceError(f"{path}: passing preflight contains failure evidence")

    top_modules = compatible_string_values(payload, "top_module", "top_modules")
    rtl_files = compatible_string_values(payload, "rtl_file", "rtl_files")
    output_paths = benchmark_output_paths(payload)
    if not top_modules or not rtl_files or not output_paths:
        raise EvidenceError(
            f"{path}: top_modules, rtl_files, and output_paths must all be non-empty"
        )
    compile_results = payload.get("compile_results")
    if not isinstance(compile_results, list) or not compile_results:
        raise EvidenceError(f"{path}: compile_results must be a non-empty list")
    if any(
        not isinstance(result, dict)
        or isinstance(result.get("returncode"), bool)
        or result.get("returncode") != 0
        for result in compile_results
    ):
        raise EvidenceError(f"{path}: every compile result must have integer returncode 0")

    _require_project_files(project_root, list(rtl_files), "rtl_files")
    _require_project_files(project_root, list(output_paths), "output_paths")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="digital-circuit-evidence")
    parser.add_argument(
        "check",
        choices=("verification", "benchmark-interface", "preflight"),
    )
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    validators = {
        "verification": validate_verification_results,
        "benchmark-interface": validate_benchmark_interface,
        "preflight": validate_preflight,
    }
    try:
        path = validators[args.check](root)
    except EvidenceError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: validated {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
