"""Collect and validate EDA, PDK, FPGA, and compiler/runtime readiness."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .tool_registry import (
    filter_entries,
    load_registry,
    probe_entries,
    render_catalog,
    validate_registry,
)

SCHEMA_VERSION = 1
DEFAULT_REPORT = Path("research/ENVIRONMENT_AUDIT.json")
DEFAULT_MARKDOWN = Path("research/ENVIRONMENT_AUDIT.md")

CAPABILITY_TOOL_BUNDLES: dict[str, tuple[tuple[str, ...], ...]] = {
    "simulation": (("verilator",), ("iverilog",)),
    "formal": (("symbiyosys", "yosys"),),
    "lint": (("verilator",), ("verible",), ("slang",)),
    "synthesis": (("yosys",), ("vivado",), ("quartus",)),
    "fpga": (("nextpnr", "yosys"), ("vivado",), ("quartus",)),
    "physical_design": (("openroad",), ("librelane",)),
    "signoff": (
        ("opensta", "klayout", "netgen"),
        ("openroad", "klayout", "netgen"),
        ("librelane",),
    ),
    "pdk": (("sky130",), ("ihp_sg13g2",), ("gf180mcu",)),
    "compiler_runtime": (
        ("gemmini",),
        ("tvm_vta",),
        ("buddy_mlir",),
        ("iree",),
        ("circt",),
        ("chisel",),
    ),
}
CAPABILITY_NAMES = tuple(CAPABILITY_TOOL_BUNDLES)
TOOL_EXECUTABLE_BUNDLES: dict[str, tuple[tuple[str, ...], ...]] = {
    "verilator": (("verilator",),),
    "iverilog": (("iverilog", "vvp"),),
    "verible": (("verible-verilog-lint",),),
    "slang": (("slang",),),
    "yosys": (("yosys",),),
    "symbiyosys": (("sby",),),
    "nextpnr": (
        ("nextpnr-ice40",),
        ("nextpnr-ecp5",),
        ("nextpnr-nexus",),
        ("nextpnr-himbaechel",),
    ),
    "vivado": (("vivado",),),
    "quartus": (("quartus_sh",),),
    "openroad": (("openroad",),),
    "librelane": (("librelane",),),
    "opensta": (("sta",), ("opensta",)),
    "klayout": (("klayout",),),
    "netgen": (("netgen",),),
}
DELIVERY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "rtl_ip": ("simulation", "lint", "synthesis"),
    "fpga": ("simulation", "lint", "synthesis", "fpga"),
    "gds": ("simulation", "lint", "synthesis", "physical_design", "signoff", "pdk"),
    "pre_tapeout": (
        "simulation",
        "formal",
        "lint",
        "synthesis",
        "physical_design",
        "signoff",
        "pdk",
    ),
    "tapeout": ("simulation", "formal", "lint", "synthesis", "physical_design", "signoff", "pdk"),
}


def _run(argv: list[str], *, timeout: float = 20.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "argv": argv,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    return {
        "argv": argv,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _first_line(text: str) -> str:
    return next((line.strip()[:500] for line in text.splitlines() if line.strip()), "")


def _scope(project_root: Path) -> dict[str, Any]:
    path = project_root / "design" / "CHIP_SCOPE.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid chip scope: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _selected_pdk(scope: dict[str, Any], project_root: Path) -> str:
    target_path = project_root / "design" / "TARGET.json"
    try:
        target = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        target = {}
    if not isinstance(target, dict):
        target = {}
    text = " ".join(
        str(value)
        for value in (
            scope.get("technology_or_board"),
            scope.get("target"),
            target.get("target"),
            target.get("technology"),
            target.get("pdk"),
        )
        if value is not None
    ).casefold()
    aliases = {
        "sky130": ("sky130", "skywater"),
        "ihp_sg13g2": ("ihp", "sg13g2"),
        "gf180mcu": ("gf180", "globalfoundries 180"),
    }
    matches = [
        entry_id
        for entry_id, needles in aliases.items()
        if any(needle in text for needle in needles)
    ]
    return matches[0] if len(matches) == 1 else ""


def _normalize_pdk_detection(
    entries: list[dict[str, Any]],
    registry: dict[str, Any],
) -> None:
    """Do not treat a generic PDK_ROOT variable as proving every open PDK."""
    specs = {
        str(entry.get("id") or ""): entry
        for entry in registry.get("entries", [])
        if isinstance(entry, dict)
    }
    for record in entries:
        entry_id = str(record.get("id") or "")
        if entry_id not in {"sky130", "ihp_sg13g2", "gf180mcu"}:
            continue
        spec = specs.get(entry_id, {})
        roots = [
            Path(value).expanduser()
            for name in spec.get("env_vars", [])
            if (value := os.environ.get(str(name)))
        ]
        markers = [Path(str(value)) for value in spec.get("source_markers", [])]
        env_matches = [
            str(root / marker)
            for root in roots
            for marker in markers
            if (root / marker).exists()
        ]
        project_matches = list(record.get("source_markers") or [])
        record["pdk_paths"] = sorted({*env_matches, *project_matches})
        record["available"] = bool(record["pdk_paths"])


def _normalize_command_detection(entries: list[dict[str, Any]]) -> None:
    """Require the exact command set needed by each EDA tool capability."""
    for record in entries:
        entry_id = str(record.get("id") or "")
        bundles = TOOL_EXECUTABLE_BUNDLES.get(entry_id)
        if not bundles:
            continue
        found = set((record.get("executables") or {}).keys())
        satisfied = [
            list(bundle) for bundle in bundles if all(command in found for command in bundle)
        ]
        record["command_bundles"] = satisfied
        record["available"] = bool(satisfied)


def _capability(
    by_id: dict[str, dict[str, Any]],
    bundles: tuple[tuple[str, ...], ...],
) -> dict[str, Any]:
    satisfied = [
        list(bundle)
        for bundle in bundles
        if all(by_id.get(tool_id, {}).get("available") for tool_id in bundle)
    ]
    available = sorted(
        {
            tool_id
            for bundle in bundles
            for tool_id in bundle
            if by_id.get(tool_id, {}).get("available")
        }
    )
    return {
        "ready": bool(satisfied),
        "available_tools": available,
        "satisfied_bundles": satisfied,
        "acceptable_bundles": [list(bundle) for bundle in bundles],
    }


def _safe_output_path(project_root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe project-relative output path: {relative}")
    root = project_root.resolve()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"output path must not be a symlink: {relative}")
    if not path.parent.resolve().is_relative_to(root):
        raise ValueError(f"output path resolves outside project: {relative}")
    return path


def _project_signals(project_root: Path) -> dict[str, list[str]]:
    groups = {
        "instructions": ("AGENTS.md", "CONTRIBUTING.md", "README.md", "Makefile", "justfile"),
        "containers": ("Dockerfile", "docker-compose.yml", "compose.yml", "flake.nix"),
        "flows": ("OpenLane", "librelane", "openroad", "flow", "scripts"),
        "hardware": ("rtl", "src", "tb", "formal", "constraints", "fpga", "physical"),
        "compiler": ("chipyard", "gemmini", "tvm", "mlir", "runtime", "software"),
    }
    return {
        group: [name for name in names if (project_root / name).exists()]
        for group, names in groups.items()
    }


def _safe_project_marker(value: object) -> str | None:
    """Return a portable relative marker, never a host path.

    ``Path('/pdks/...').is_absolute()`` is false for ``WindowsPath`` because it
    has no drive, even though it is an absolute POSIX path and must not be
    persisted in a shareable audit. Check both path grammars so collecting on
    one OS cannot leak a path written for the other.
    """
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(raw)
    if posix.is_absolute() or windows.is_absolute() or windows.root:
        return None
    if ".." in posix.parts or ".." in windows.parts:
        return None
    return raw


def collect(
    project_root: Path,
    *,
    target_python: str,
    required: list[str],
) -> dict[str, Any]:
    root = project_root.resolve()
    registry = load_registry()
    errors = validate_registry(registry)
    if errors:
        raise ValueError("invalid chip tool registry: " + "; ".join(errors))
    entries = probe_entries(registry, target_python=target_python, project_root=root)
    _normalize_command_detection(entries)
    _normalize_pdk_detection(entries, registry)
    by_id = {str(entry.get("id") or ""): entry for entry in entries}
    scope = _scope(root)
    level = str(scope.get("delivery_level") or "").strip()
    if level not in DELIVERY_REQUIREMENTS:
        raise ValueError(f"unsupported or missing delivery_level: {level!r}")
    inferred = list(DELIVERY_REQUIREMENTS.get(level, ()))
    selected = list(dict.fromkeys([*inferred, *required]))
    invalid = sorted(set(selected) - set(CAPABILITY_NAMES))
    if invalid:
        raise ValueError(f"unknown required capabilities: {', '.join(invalid)}")

    capabilities: dict[str, dict[str, Any]] = {}
    for name, bundles in CAPABILITY_TOOL_BUNDLES.items():
        capabilities[name] = _capability(by_id, bundles)
    selected_pdk = _selected_pdk(scope, root)
    if level in {"gds", "pre_tapeout", "tapeout"} and not selected_pdk:
        raise ValueError(
            "GDS/pre-tapeout/tapeout scope must name exactly one supported target PDK "
            "(sky130, ihp_sg13g2, or gf180mcu)"
        )
    if selected_pdk:
        capabilities["pdk"] = _capability(by_id, ((selected_pdk,),))

    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(UTC).isoformat(),
        "project_root": ".",
        "delivery_level": level,
        "selected_pdk": selected_pdk,
        "required_capabilities": selected,
        "ready": all(capabilities[name]["ready"] for name in selected),
        "capabilities": capabilities,
        "detected_tools": [
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "available": bool(record.get("available")),
                "version": record.get("version", ""),
                "commands": sorted((record.get("executables") or {}).keys()),
                "environment_variables": sorted(record.get("env_vars") or []),
                "project_markers": sorted(
                    marker
                    for value in record.get("source_markers") or []
                    if (marker := _safe_project_marker(value)) is not None
                ),
            }
            for record in entries
        ],
        "project_signals": _project_signals(root),
        "runtime": {
            "platform": platform.platform(),
            "python": Path(target_python).name,
            "python_version": _first_line(
                _run([target_python, "--version"]).get("stdout", "")
                or _run([target_python, "--version"]).get("stderr", "")
            ),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Chip Design Environment Audit",
        "",
        f"- Collected: `{payload.get('collected_at', '')}`",
        f"- Delivery level: `{payload.get('delivery_level') or 'unset'}`",
        f"- Target Python: `{payload.get('runtime', {}).get('python', '')}`",
        f"- Ready: `{str(bool(payload.get('ready'))).lower()}`",
        "",
        "| Capability | Required | Ready | Available tools |",
        "| --- | --- | --- | --- |",
    ]
    required = set(payload.get("required_capabilities") or [])
    for name, result in payload.get("capabilities", {}).items():
        lines.append(
            f"| `{name}` | {'yes' if name in required else 'no'} | "
            f"{'yes' if result.get('ready') else 'no'} | "
            f"{', '.join(str(value) for value in result.get('available_tools', [])) or '—'} |"
        )
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def check(
    project_root: Path,
    report: Path = DEFAULT_REPORT,
    *,
    target_python: str | None = None,
) -> tuple[bool, list[str]]:
    path = project_root / report
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"{path}: invalid or missing audit: {exc}"]
    if not isinstance(payload, dict):
        return False, [f"{path}: expected a JSON object"]
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("project_root") != ".":
        errors.append("project_root must use the sanitized project-relative marker '.'")
    try:
        scope = _scope(project_root.resolve())
    except ValueError as exc:
        errors.append(str(exc))
        scope = {}
    level = str(payload.get("delivery_level") or "")
    current_level = str(scope.get("delivery_level") or "")
    if level != current_level or level not in DELIVERY_REQUIREMENTS:
        errors.append(f"delivery_level does not match current scope: {level!r}")
    expected_required = list(DELIVERY_REQUIREMENTS.get(current_level, ()))
    reported_required = payload.get("required_capabilities")
    if not isinstance(reported_required, list):
        reported_required = []
        errors.append("required_capabilities must be a list")
    invalid_required = sorted(set(reported_required) - set(CAPABILITY_NAMES))
    if invalid_required:
        errors.append(f"unknown required capabilities: {', '.join(invalid_required)}")
    if not set(expected_required).issubset(reported_required):
        errors.append(
            "required_capabilities omit current scope requirements: "
            f"required {expected_required!r}"
        )
    expected_pdk = _selected_pdk(scope, project_root.resolve())
    if current_level in {"gds", "pre_tapeout", "tapeout"} and not expected_pdk:
        errors.append(
            "current GDS/pre-tapeout/tapeout scope does not name one supported target PDK"
        )
    if payload.get("selected_pdk", "") != expected_pdk:
        errors.append("selected_pdk does not match current target")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        errors.append("capabilities must be an object")
        capabilities = {}
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict) or not str(runtime.get("python") or ""):
        errors.append("runtime.python metadata is required")
    trusted_target_python = (
        target_python
        or os.environ.get("ARGUS_SKILL_PROJECT_PYTHON")
        or sys.executable
    )
    try:
        registry = load_registry()
        registry_errors = validate_registry(registry)
        if registry_errors:
            raise ValueError("; ".join(registry_errors))
        fresh_entries = probe_entries(
            registry,
            target_python=trusted_target_python,
            project_root=project_root.resolve(),
        )
        _normalize_command_detection(fresh_entries)
        _normalize_pdk_detection(fresh_entries, registry)
        fresh_by_id = {
            str(record.get("id") or ""): record for record in fresh_entries
        }
    except (OSError, ValueError) as exc:
        errors.append(f"fresh tool probe failed: {exc}")
        fresh_by_id = {}
    for name in reported_required:
        if name not in CAPABILITY_TOOL_BUNDLES:
            continue
        bundles = CAPABILITY_TOOL_BUNDLES[name]
        if name == "pdk" and expected_pdk:
            bundles = ((expected_pdk,),)
        fresh = _capability(fresh_by_id, bundles)
        if not fresh["ready"]:
            errors.append(f"fresh probe found required capability unavailable: {name}")
        result = capabilities.get(name)
        if not isinstance(result, dict) or not result.get("ready"):
            errors.append(f"required capability is not ready: {name}")
        elif result.get("satisfied_bundles") != fresh["satisfied_bundles"]:
            errors.append(f"required capability does not match fresh probe: {name}")
    if bool(payload.get("ready")) != (not errors):
        errors.append("ready does not match required capability results")
    return not errors, errors


def _catalog(args: argparse.Namespace) -> int:
    registry = load_registry()
    errors = validate_registry(registry)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    if args.list_categories:
        categories = sorted(
            {str(category) for entry in registry["entries"] for category in entry.get("categories", [])}
        )
        print("\n".join(categories))
        return 0
    entries = filter_entries(
        registry,
        categories=args.category,
        platforms=args.platform,
        query=args.query,
        include_legacy=args.include_legacy,
    )
    print(render_catalog(entries))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chip-design-environment-audit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--project-root", default=".")
    collect_parser.add_argument("--target-python", default=sys.executable)
    collect_parser.add_argument("--require", action="append", default=[])
    collect_parser.add_argument("--output", default=str(DEFAULT_REPORT))
    collect_parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--project-root", default=".")
    check_parser.add_argument("--report", default=str(DEFAULT_REPORT))
    check_parser.add_argument("--target-python", default=None)

    catalog_parser = subparsers.add_parser("catalog")
    catalog_parser.add_argument("--category", action="append", default=[])
    catalog_parser.add_argument("--platform", action="append", default=[])
    catalog_parser.add_argument("--query", default="")
    catalog_parser.add_argument("--include-legacy", action="store_true")
    catalog_parser.add_argument("--list-categories", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "catalog":
        return _catalog(args)
    root = Path(args.project_root).resolve()
    if args.command == "collect":
        try:
            payload = collect(
                root,
                target_python=str(Path(args.target_python).expanduser()),
                required=list(args.require),
            )
        except ValueError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 2
        output = Path(args.output)
        markdown = Path(args.markdown)
        _write(
            _safe_output_path(root, output),
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
        _write(_safe_output_path(root, markdown), render_markdown(payload))
        print(f"wrote {output} and {markdown}; ready={str(payload['ready']).lower()}")
        return 0 if payload["ready"] else 1
    ok, errors = check(
        root,
        Path(args.report),
        target_python=args.target_python,
    )
    if not ok:
        print("\n".join(f"FAIL: {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"OK: validated {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
