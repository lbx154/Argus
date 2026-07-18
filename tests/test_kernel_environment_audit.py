from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from argus_skill.verticals.kernel_engineering.environment_audit import (
    SCHEMA_VERSION,
    _normalize_requirements,
    collect_project_signals,
    derive_capabilities,
    render_markdown,
    validate_report,
)


def _records(*present: str) -> dict[str, dict[str, object]]:
    names = {
        "torch",
        "triton",
        "tilelang",
        "cutlass",
        "nvidia-smi",
        "nvcc",
        "ptxas",
        "ninja",
        "cmake",
        "ncu",
        "nsys",
        "compute-sanitizer",
    }
    return {name: {"present": name in present} for name in names}


def test_normalize_requirements_is_stable_and_accepts_commas() -> None:
    assert _normalize_requirements(["TileLang, profiling", "tilelang", "cuda-cpp"]) == [
        "tilelang",
        "profiling",
        "cuda_cpp",
    ]


def test_tilelang_requires_package_nvcc_torch_and_gpu() -> None:
    packages = _records("torch", "triton", "tilelang")
    tools = _records("nvidia-smi", "ncu", "ninja")
    caps = derive_capabilities(
        packages=packages,
        tools=tools,
        gpus=[{"name": "NVIDIA B200"}],
        torch_runtime={"cuda_available": True},
        project_signals={"framework_directories": []},
    )
    assert caps["triton"].ready is True
    assert caps["tilelang"].ready is False
    assert "nvcc" in caps["tilelang"].missing


def test_cuda_cpp_and_cutlass_are_separate_capabilities() -> None:
    packages = _records("torch")
    tools = _records("nvidia-smi", "nvcc", "ptxas", "ninja")
    caps = derive_capabilities(
        packages=packages,
        tools=tools,
        gpus=[{"name": "NVIDIA B200"}],
        torch_runtime={"cuda_available": True},
        project_signals={"framework_directories": []},
    )
    assert caps["cuda_cpp"].ready is True
    assert caps["cutlass_cute"].ready is False

    caps_with_cutlass = derive_capabilities(
        packages=packages,
        tools=tools,
        gpus=[{"name": "NVIDIA B200"}],
        torch_runtime={"cuda_available": True},
        project_signals={"framework_directories": ["third_party/cutlass"]},
    )
    assert caps_with_cutlass["cutlass_cute"].ready is True


def test_project_signals_capture_native_extras_and_benchmarks(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1'\n"
        "[project.optional-dependencies]\n"
        "tilelang=['tilelang>=0.1.9']\n"
        "test=['pytest']\n",
        encoding="utf-8",
    )

    signals = collect_project_signals(tmp_path)

    assert "AGENTS.md" in signals["instruction_and_lock_files"]
    assert signals["benchmark_directories"] == ["benchmarks"]
    assert signals["pyproject_extras"]["tilelang"] == ["tilelang>=0.1.9"]


def test_validate_report_fails_red_or_stale_audit(tmp_path: Path) -> None:
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (datetime.now(UTC) - timedelta(hours=30)).isoformat(),
        "project_root": str(tmp_path.resolve()),
        "requested_capabilities": ["tilelang"],
        "blocking_findings": ["Capability tilelang is not ready: nvcc"],
        "ready": False,
    }

    errors = validate_report(report, project_root=tmp_path, max_age_hours=24)

    assert any("stale" in item for item in errors)
    assert any("tilelang" in item for item in errors)
    assert "report is not ready" in errors


def test_render_markdown_surfaces_environment_failure() -> None:
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "project_root": "/repo",
        "host": {
            "target_python_version": "Python 3.12",
            "target_python": "/venv/bin/python",
        },
        "ready": False,
        "requested_capabilities": ["tilelang"],
        "gpus": [],
        "capabilities": {
            "tilelang": {"ready": False, "missing": ["tilelang", "nvcc"]},
        },
        "blocking_findings": ["Capability tilelang is not ready"],
        "warnings": [],
    }

    text = render_markdown(report)

    assert "Ready: **NO**" in text
    assert "tilelang, nvcc" in text
    assert "environment failure" in text.lower()
