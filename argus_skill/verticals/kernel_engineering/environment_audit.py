"""Collect and validate environment provenance for GPU-kernel work.

The audit is intentionally diagnostic rather than an installer.  It answers a
question agents routinely skip: *is the selected professional toolchain really
available in the same environment that will run correctness and benchmarks?*

Usage::

    python -m argus_skill.verticals.kernel_engineering.environment_audit collect \
      --project-root . --require tilelang --require profiling
    python -m argus_skill.verticals.kernel_engineering.environment_audit check \
      --project-root .
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

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

PACKAGE_IMPORTS: dict[str, tuple[str, ...]] = {
    "torch": ("torch",),
    "triton": ("triton",),
    "tilelang": ("tilelang",),
    "apache_tvm_ffi": ("tvm_ffi", "apache-tvm-ffi"),
    "flash_attn": ("flash_attn", "flash-attn"),
    "flashinfer": ("flashinfer", "flashinfer-python"),
    "transformer_engine": ("transformer_engine", "transformer-engine"),
    "xformers": ("xformers",),
    "cutlass": ("cutlass", "nvidia-cutlass-dsl", "nvidia-cutlass"),
    "kernels": ("kernels",),
    "einops": ("einops",),
    "cuda_python": ("cuda", "cuda-python"),
    "cupy": ("cupy", "cupy-cuda12x", "cupy-cuda13x"),
    "numpy": ("numpy",),
    "pytest": ("pytest",),
    "hypothesis": ("hypothesis",),
}

TOOL_COMMANDS: dict[str, tuple[str, ...]] = {
    "nvidia-smi": ("nvidia-smi", "--version"),
    "nvcc": ("nvcc", "--version"),
    "ptxas": ("ptxas", "--version"),
    "cuobjdump": ("cuobjdump", "--version"),
    "nvdisasm": ("nvdisasm", "--version"),
    "ncu": ("ncu", "--version"),
    "nsys": ("nsys", "--version"),
    "compute-sanitizer": ("compute-sanitizer", "--version"),
    "cmake": ("cmake", "--version"),
    "ninja": ("ninja", "--version"),
    "ccache": ("ccache", "--version"),
    "gcc": ("gcc", "--version"),
    "g++": ("g++", "--version"),
    "clang": ("clang", "--version"),
    "git": ("git", "--version"),
    "rg": ("rg", "--version"),
    "jq": ("jq", "--version"),
    "uv": ("uv", "--version"),
}

CAPABILITY_NAMES = (
    "torch",
    "triton",
    "tilelang",
    "cuda_cpp",
    "cutlass_cute",
    "profiling",
    "sanitizer",
)

IMPLEMENTATION_CAPABILITIES = frozenset({"torch", "triton", "tilelang", "cuda_cpp", "cutlass_cute"})

PROJECT_SIGNAL_NAMES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "INSTALL.md",
    "ENVs.md",
    "README.md",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "environment.yml",
    "Dockerfile",
)

BENCHMARK_DIR_NAMES = ("benchmark", "benchmarks", "perf", "performance")


@dataclass(frozen=True)
class Capability:
    ready: bool
    evidence: tuple[str, ...]
    missing: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "evidence": list(self.evidence),
            "missing": list(self.missing),
        }


def _run(argv: Iterable[str], timeout: float = 10.0) -> dict[str, Any]:
    args = [str(x) for x in argv]
    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "argv": args,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    return {
        "argv": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:500]
    return ""


def collect_packages(python_executable: str) -> dict[str, dict[str, Any]]:
    """Probe packages in the benchmark/test Python, not the Argus helper venv."""
    probe = r"""
import importlib.metadata, importlib.util, json, sys
mapping = json.loads(sys.argv[1])
out = {}
for key, names in mapping.items():
    try:
        present = importlib.util.find_spec(names[0]) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        present = False
    version = ""
    for dist in names:
        try:
            version = importlib.metadata.version(dist)
            break
        except importlib.metadata.PackageNotFoundError:
            pass
    out[key] = {"present": present, "version": version}
print(json.dumps(out))
"""
    result = _run(
        [python_executable, "-c", probe, json.dumps(PACKAGE_IMPORTS)],
        timeout=30.0,
    )
    if result["returncode"] == 0:
        try:
            payload = json.loads(result["stdout"])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {
        key: {
            "present": False,
            "version": "",
            "probe_error": _first_line(result["stderr"] or result["stdout"]),
        }
        for key in PACKAGE_IMPORTS
    }


def collect_dependency_health(python_executable: str) -> dict[str, Any]:
    """Run the target environment's dependency-consistency check."""
    result = _run([python_executable, "-m", "pip", "check"], timeout=30.0)
    output = "\n".join(
        part for part in (result.get("stdout", ""), result.get("stderr", "")) if part
    )
    issues = [line.strip() for line in output.splitlines() if line.strip()]
    probe_ok = result.get("returncode") is not None and not any(
        marker in output.casefold()
        for marker in ("no module named pip", "cannot find pip")
    )
    return {
        "probe_ok": probe_ok,
        "ok": probe_ok and result.get("returncode") == 0,
        "returncode": result.get("returncode"),
        "issues": issues,
    }


def collect_tools() -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name, command in TOOL_COMMANDS.items():
        path = shutil.which(command[0])
        if not path:
            records[name] = {"present": False, "path": "", "version": ""}
            continue
        result = _run(command)
        version_text = result["stdout"] or result["stderr"]
        records[name] = {
            "present": True,
            "path": path,
            "version": _first_line(version_text),
            "version_returncode": result["returncode"],
        }
    return records


def collect_gpus() -> list[dict[str, str]]:
    query = "index,name,uuid,memory.total,driver_version,compute_cap"
    result = _run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"])
    if result["returncode"] != 0:
        return []
    rows: list[dict[str, str]] = []
    keys = query.split(",")
    for line in result["stdout"].splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(keys):
            continue
        rows.append(dict(zip(keys, parts, strict=True)))
    return rows


def collect_torch_runtime(python_executable: str) -> dict[str, Any]:
    probe = (
        "import json, torch; "
        "print(json.dumps({"
        "'version': torch.__version__, 'cuda_version': torch.version.cuda, "
        "'cuda_available': torch.cuda.is_available(), "
        "'device_count': torch.cuda.device_count(), "
        "'devices': ["
        "{'name': torch.cuda.get_device_name(i), "
        "'capability': list(torch.cuda.get_device_capability(i))} "
        "for i in range(torch.cuda.device_count())]}))"
    )
    result = _run([python_executable, "-c", probe], timeout=30.0)
    if result["returncode"] != 0:
        return {"probe_ok": False, "error": _first_line(result["stderr"])}
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return {"probe_ok": False, "error": "torch probe returned invalid JSON"}
    payload["probe_ok"] = True
    return payload


def _read_pyproject_extras(path: Path) -> dict[str, list[str]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return {}
    raw = data.get("project", {}).get("optional-dependencies", {})
    if not isinstance(raw, dict):
        return {}
    extras: dict[str, list[str]] = {}
    for key, values in raw.items():
        if isinstance(values, list):
            extras[str(key)] = [str(value) for value in values]
    return extras


def _read_pyproject_name(path: Path) -> str:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return ""
    return str(data.get("project", {}).get("name", "")).strip()


def _count_kernel_sources(root: Path) -> dict[str, int]:
    counts = {"cuda": 0, "triton_or_python": 0, "cpp": 0}
    ignored = {".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__"}
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in ignored and not name.startswith(".tox")]
        for name in files:
            suffix = Path(name).suffix.lower()
            if suffix in {".cu", ".cuh"}:
                counts["cuda"] += 1
            elif suffix == ".py":
                counts["triton_or_python"] += 1
            elif suffix in {".cpp", ".cc", ".cxx", ".h", ".hpp"}:
                counts["cpp"] += 1
    return counts


def collect_project_signals(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    files = [name for name in PROJECT_SIGNAL_NAMES if (root / name).is_file()]
    benchmark_dirs = [name for name in BENCHMARK_DIR_NAMES if (root / name).is_dir()]
    ci_files: list[str] = []
    ci_root = root / ".github" / "workflows"
    if ci_root.is_dir():
        ci_files = [str(path.relative_to(root)) for path in sorted(ci_root.glob("*.y*ml"))]
    extras = _read_pyproject_extras(root / "pyproject.toml") if "pyproject.toml" in files else {}
    project_name = _read_pyproject_name(root / "pyproject.toml") if "pyproject.toml" in files else ""
    framework_dirs = [
        name
        for name in ("cutlass", "cute", "cute_dsl", "third_party/cutlass", "vendor/cutlass")
        if (root / name).exists()
    ]
    kernel_sources = _count_kernel_sources(root)
    return {
        "instruction_and_lock_files": files,
        "benchmark_directories": benchmark_dirs,
        "ci_workflows": ci_files,
        "pyproject_extras": extras,
        "project_name": project_name,
        "framework_directories": framework_dirs,
        "kernel_source_counts": kernel_sources,
    }


def _normalize_distribution_name(value: str) -> str:
    return str(value or "").strip().casefold().replace("_", "-").replace(".", "-")


def _critical_dependency_distributions(
    project_signals: dict[str, Any], requirements: Iterable[str]
) -> set[str]:
    names = {_normalize_distribution_name(project_signals.get("project_name", ""))}
    capability_packages = {
        "torch": {"torch"},
        "triton": {"torch", "triton"},
        "tilelang": {"torch", "tilelang", "apache-tvm-ffi", "tvm-ffi"},
        "cutlass_cute": {"nvidia-cutlass-dsl", "nvidia-cutlass", "cutlass"},
    }
    for requirement in requirements:
        names.update(capability_packages.get(str(requirement), set()))
    return {_normalize_distribution_name(name) for name in names if name}


def _partition_dependency_issues(
    issues: Iterable[str], critical_distributions: set[str]
) -> tuple[list[str], list[str]]:
    critical: list[str] = []
    unrelated: list[str] = []
    for raw in issues:
        issue = str(raw).strip()
        owner = _normalize_distribution_name(issue.split(maxsplit=1)[0] if issue else "")
        (critical if owner in critical_distributions else unrelated).append(issue)
    return critical, unrelated


def _present(records: dict[str, dict[str, Any]], key: str) -> bool:
    return bool(records.get(key, {}).get("present"))


def derive_capabilities(
    *,
    packages: dict[str, dict[str, Any]],
    tools: dict[str, dict[str, Any]],
    gpus: list[dict[str, str]],
    torch_runtime: dict[str, Any],
    project_signals: dict[str, Any],
) -> dict[str, Capability]:
    gpu_ready = bool(gpus) and bool(torch_runtime.get("cuda_available"))
    torch_ready = gpu_ready and _present(packages, "torch")

    def cap(evidence: list[str], missing: list[str]) -> Capability:
        return Capability(not missing, tuple(evidence), tuple(missing))

    capabilities: dict[str, Capability] = {}
    capabilities["torch"] = cap(
        ["CUDA-visible GPU", "torch with CUDA"] if torch_ready else [],
        [] if torch_ready else ["CUDA-visible GPU and CUDA-enabled torch"],
    )
    capabilities["triton"] = cap(
        ["torch", "triton"] if torch_ready and _present(packages, "triton") else [],
        []
        if torch_ready and _present(packages, "triton")
        else [
            name
            for name, ok in (("torch/CUDA", torch_ready), ("triton", _present(packages, "triton")))
            if not ok
        ],
    )
    tilelang_ok = torch_ready and _present(packages, "tilelang") and _present(tools, "nvcc")
    capabilities["tilelang"] = cap(
        ["torch", "tilelang", "nvcc"] if tilelang_ok else [],
        [
            name
            for name, ok in (
                ("torch/CUDA", torch_ready),
                ("tilelang", _present(packages, "tilelang")),
                ("nvcc", _present(tools, "nvcc")),
            )
            if not ok
        ],
    )
    cuda_cpp_ok = (
        gpu_ready
        and _present(tools, "nvcc")
        and _present(tools, "ptxas")
        and (_present(tools, "ninja") or _present(tools, "cmake"))
    )
    capabilities["cuda_cpp"] = cap(
        ["GPU", "nvcc", "ptxas", "ninja/cmake"] if cuda_cpp_ok else [],
        [
            name
            for name, ok in (
                ("CUDA-visible GPU", gpu_ready),
                ("nvcc", _present(tools, "nvcc")),
                ("ptxas", _present(tools, "ptxas")),
                ("ninja or cmake", _present(tools, "ninja") or _present(tools, "cmake")),
            )
            if not ok
        ],
    )
    cutlass_hint = (
        _present(packages, "cutlass")
        or bool(os.environ.get("CUTLASS_PATH"))
        or bool(project_signals.get("framework_directories"))
    )
    cutlass_ok = cuda_cpp_ok and cutlass_hint
    capabilities["cutlass_cute"] = cap(
        ["cuda_cpp", "CUTLASS/CuTe source or package"] if cutlass_ok else [],
        [
            name
            for name, ok in (
                ("cuda_cpp", cuda_cpp_ok),
                ("CUTLASS/CuTe source, package, or CUTLASS_PATH", cutlass_hint),
            )
            if not ok
        ],
    )
    profiling_ok = gpu_ready and (_present(tools, "ncu") or _present(tools, "nsys"))
    capabilities["profiling"] = cap(
        ["GPU", "ncu or nsys"] if profiling_ok else [],
        [
            name
            for name, ok in (
                ("CUDA-visible GPU", gpu_ready),
                ("ncu or nsys", _present(tools, "ncu") or _present(tools, "nsys")),
            )
            if not ok
        ],
    )
    sanitizer_ok = gpu_ready and _present(tools, "compute-sanitizer")
    capabilities["sanitizer"] = cap(
        ["GPU", "compute-sanitizer"] if sanitizer_ok else [],
        [
            name
            for name, ok in (
                ("CUDA-visible GPU", gpu_ready),
                ("compute-sanitizer", _present(tools, "compute-sanitizer")),
            )
            if not ok
        ],
    )
    return capabilities


def _normalize_requirements(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        for part in str(raw).split(","):
            name = part.strip().lower().replace("-", "_")
            if name and name not in result:
                result.append(name)
    return result


def build_report(
    project_root: Path,
    requirements: list[str],
    *,
    target_python: str | None = None,
) -> dict[str, Any]:
    python_executable = str(Path(target_python or sys.executable).expanduser())
    python_version_probe = _run([python_executable, "--version"])
    packages = collect_packages(python_executable)
    dependency_health = collect_dependency_health(python_executable)
    tools = collect_tools()
    gpus = collect_gpus()
    torch_runtime = collect_torch_runtime(python_executable)
    signals = collect_project_signals(project_root)
    registry = load_registry()
    registry_errors = validate_registry(registry)
    registry_probe = probe_entries(
        registry,
        target_python=python_executable,
        project_root=project_root,
    )
    available_specialized = [item for item in registry_probe if item["available"]]
    capabilities = derive_capabilities(
        packages=packages,
        tools=tools,
        gpus=gpus,
        torch_runtime=torch_runtime,
        project_signals=signals,
    )
    unknown = [name for name in requirements if name not in capabilities]
    missing = [
        name for name in requirements if name in capabilities and not capabilities[name].ready
    ]
    implementation_selected = bool(IMPLEMENTATION_CAPABILITIES.intersection(requirements))
    blockers: list[str] = []
    if not requirements:
        blockers.append(
            "No required capabilities were selected for the kernel implementation path."
        )
    elif not implementation_selected:
        blockers.append(
            "Select the implementation capability: torch, triton, tilelang, "
            "cuda_cpp, or cutlass_cute."
        )
    for name in unknown:
        blockers.append(f"Unknown requested capability: {name}")
    for name in missing:
        detail = ", ".join(capabilities[name].missing) or "missing prerequisites"
        blockers.append(f"Capability {name} is not ready: {detail}")

    warnings: list[str] = []
    if not signals["instruction_and_lock_files"]:
        warnings.append(
            "No project-native setup/instruction/lock files were detected at repository root."
        )
    if not signals["benchmark_directories"]:
        warnings.append(
            "No conventional benchmark directory was detected; identify the canonical runner manually."
        )
    if not capabilities["profiling"].ready:
        warnings.append(
            "No Nsight profiler is available; timing-only diagnosis may miss the actual bottleneck."
        )
    if not capabilities["sanitizer"].ready:
        warnings.append(
            "Compute Sanitizer is unavailable; race/memory diagnostics need another justified path."
        )
    if registry_errors:
        blockers.extend(f"Specialized tool registry error: {error}" for error in registry_errors)
    critical_dependency_issues, unrelated_dependency_issues = _partition_dependency_issues(
        dependency_health.get("issues", []),
        _critical_dependency_distributions(signals, requirements),
    )
    if not dependency_health.get("probe_ok"):
        warnings.append(
            "Dependency consistency could not be checked in the target Python; "
            "use the repository's package manager/lockfile to prove closure."
        )
    blockers.extend(
        f"Target dependency closure is inconsistent: {issue}"
        for issue in critical_dependency_issues
    )
    warnings.extend(
        f"Unrelated installed distribution is inconsistent: {issue}"
        for issue in unrelated_dependency_issues
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "project_root": str(project_root.resolve()),
        "host": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "argus_python": sys.executable,
            "target_python": python_executable,
            "target_python_version": _first_line(
                python_version_probe["stdout"] or python_version_probe["stderr"]
            ),
        },
        "gpus": gpus,
        "torch_runtime": torch_runtime,
        "packages": packages,
        "dependency_health": dependency_health,
        "tools": tools,
        "project_signals": signals,
        "specialized_tool_registry": {
            "refreshed_at": registry.get("refreshed_at"),
            "entry_count": len(registry.get("entries", [])),
            "available_count": len(available_specialized),
            "available": available_specialized,
            "legacy_statuses_excluded_from_default_catalog": [
                "archived",
                "deprecated",
                "moved",
            ],
        },
        "requested_capabilities": requirements,
        "capabilities": {name: value.as_dict() for name, value in capabilities.items()},
        "blocking_findings": blockers,
        "warnings": warnings,
        "ready": not blockers,
        "policy": {
            "auto_install": False,
            "project_native_first": True,
            "benchmark_environment_must_match_validation_environment": True,
            "missing_tooling_is_environment_failure_not_algorithm_failure": True,
            "declared_dependency_closure_must_be_healthy": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Kernel Environment Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Project: `{report['project_root']}`",
        f"- Target Python: `{report['host']['target_python_version']}` "
        f"(`{report['host']['target_python']}`)",
        f"- Ready: **{'YES' if report['ready'] else 'NO'}**",
        f"- Requested capabilities: `{', '.join(report['requested_capabilities']) or 'none'}`",
        "",
        "## GPU",
        "",
    ]
    if report["gpus"]:
        lines.append("| index | name | compute capability | memory MiB | driver |")
        lines.append("|---:|---|---|---:|---|")
        for gpu in report["gpus"]:
            lines.append(
                f"| {gpu.get('index', '')} | {gpu.get('name', '')} | "
                f"{gpu.get('compute_cap', '')} | {gpu.get('memory.total', '')} | "
                f"{gpu.get('driver_version', '')} |"
            )
    else:
        lines.append("No CUDA GPU was discovered by `nvidia-smi`.")
    lines.extend(
        ["", "## Capability gate", "", "| capability | ready | missing |", "|---|---|---|"]
    )
    for name, item in report["capabilities"].items():
        missing = ", ".join(item["missing"])
        lines.append(f"| `{name}` | {'yes' if item['ready'] else 'no'} | {missing} |")
    lines.extend(["", "## Blocking findings", ""])
    if report["blocking_findings"]:
        lines.extend(f"- {item}" for item in report["blocking_findings"])
    else:
        lines.append("- None.")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {item}" for item in report["warnings"])
    else:
        lines.append("- None.")
    dependency = report.get("dependency_health") or {}
    lines.extend(["", "## Dependency closure", ""])
    if dependency.get("probe_ok"):
        lines.append(f"- `pip check`: **{'clean' if dependency.get('ok') else 'red'}**")
        for issue in dependency.get("issues") or []:
            lines.append(f"- {issue}")
    else:
        lines.append("- Dependency consistency probe unavailable in target Python.")
    registry = report.get("specialized_tool_registry") or {}
    lines.extend(
        [
            "",
            "## Specialized ecosystem detected",
            "",
            f"Catalog entries: `{registry.get('entry_count', 0)}`; "
            f"detected in this environment/project: `{registry.get('available_count', 0)}`.",
            "",
        ]
    )
    available = registry.get("available") or []
    if available:
        lines.append("| id | version | detected by |")
        lines.append("|---|---|---|")
        for item in available:
            detected_by = []
            if item.get("found_imports"):
                detected_by.append("imports=" + ",".join(item["found_imports"]))
            if item.get("executables"):
                detected_by.append("executables=" + ",".join(item["executables"]))
            if item.get("source_markers"):
                detected_by.append("source=" + ",".join(item["source_markers"]))
            if item.get("env_vars"):
                detected_by.append("env=" + ",".join(item["env_vars"]))
            lines.append(
                f"| `{item.get('id', '')}` | {item.get('version', '')} | {'; '.join(detected_by)} |"
            )
    else:
        lines.append("No catalogued specialist package/tool was detected.")
    lines.extend(
        [
            "",
            "## Environment-first rule",
            "",
            "Do not start kernel implementation until the selected capability is green. "
            "Use repository extras, lockfiles, containers, benchmark runners, and mature "
            "vendor/framework primitives before writing replacement infrastructure. A "
            "missing compiler, package, profiler, or architecture flag is an environment "
            "failure—not evidence that the kernel mechanism is wrong.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    report: dict[str, Any], *, project_root: Path, json_path: Path, markdown_path: Path
) -> None:
    resolved_json = json_path if json_path.is_absolute() else project_root / json_path
    resolved_markdown = (
        markdown_path if markdown_path.is_absolute() else project_root / markdown_path
    )
    resolved_json.parent.mkdir(parents=True, exist_ok=True)
    resolved_markdown.parent.mkdir(parents=True, exist_ok=True)
    resolved_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    resolved_markdown.write_text(render_markdown(report), encoding="utf-8")


def _parse_timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def validate_report(report: dict[str, Any], *, project_root: Path) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {report.get('schema_version')!r}")
    if Path(str(report.get("project_root", ""))).resolve() != project_root.resolve():
        errors.append("report project_root does not match current project")
    generated = _parse_timestamp(report.get("generated_at"))
    if generated is None:
        errors.append("generated_at is missing or invalid")
    requirements = report.get("requested_capabilities")
    if not isinstance(requirements, list) or not requirements:
        errors.append("requested_capabilities is empty")
    elif not IMPLEMENTATION_CAPABILITIES.intersection(str(x) for x in requirements):
        errors.append("no implementation capability was selected")
    blockers = report.get("blocking_findings")
    if not isinstance(blockers, list):
        errors.append("blocking_findings is not a list")
    elif blockers:
        errors.extend(str(item) for item in blockers)
    if report.get("ready") is not True:
        errors.append("report is not ready")
    return list(dict.fromkeys(errors))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="collect JSON + Markdown environment evidence")
    collect.add_argument("--project-root", type=Path, default=Path.cwd())
    collect.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    collect.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    collect.add_argument(
        "--target-python",
        default=sys.executable,
        help="Python used by the repository's tests/benchmarks; defaults to this interpreter",
    )
    collect.add_argument(
        "--require",
        action="append",
        default=[],
        help=f"required capability, repeatable/comma-separated: {', '.join(CAPABILITY_NAMES)}",
    )
    check = sub.add_parser("check", help="validate a previously collected report")
    check.add_argument("--project-root", type=Path, default=Path.cwd())
    check.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    catalog = sub.add_parser("catalog", help="query the curated professional kernel-tool registry")
    catalog.add_argument("--category", action="append", default=[])
    catalog.add_argument("--platform", action="append", default=[])
    catalog.add_argument("--search", default="")
    catalog.add_argument("--list-categories", action="store_true")
    catalog.add_argument("--list-platforms", action="store_true")
    catalog.add_argument("--include-legacy", action="store_true")
    catalog.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "catalog":
        registry = load_registry()
        errors = validate_registry(registry)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 2
        if args.list_categories:
            categories = sorted(
                {
                    str(category)
                    for entry in registry["entries"]
                    for category in entry.get("categories", [])
                }
            )
            print("\n".join(categories))
            return 0
        if args.list_platforms:
            platforms = sorted(
                {
                    str(platform)
                    for entry in registry["entries"]
                    for platform in entry.get("platforms", [])
                }
            )
            print("\n".join(platforms))
            return 0
        entries = filter_entries(
            registry,
            categories=args.category,
            platforms=args.platform,
            query=args.search,
            include_legacy=args.include_legacy,
        )
        if args.json:
            print(json.dumps(entries, indent=2, sort_keys=True))
        else:
            print(render_catalog(entries))
        return 0
    project_root = args.project_root.resolve()
    if args.command == "collect":
        requirements = _normalize_requirements(args.require)
        report = build_report(
            project_root,
            requirements,
            target_python=args.target_python,
        )
        write_report(
            report,
            project_root=project_root,
            json_path=args.report,
            markdown_path=args.markdown,
        )
        print(
            json.dumps({"ready": report["ready"], "blocking_findings": report["blocking_findings"]})
        )
        return 0 if report["ready"] else 2

    report_path = args.report if args.report.is_absolute() else project_root / args.report
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"environment audit unreadable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("environment audit root must be a JSON object", file=sys.stderr)
        return 2
    errors = validate_report(report, project_root=project_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print("kernel environment audit: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
