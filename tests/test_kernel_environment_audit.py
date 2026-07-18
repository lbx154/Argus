from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from argus_skill.verticals.kernel_engineering.environment_audit import (
    SCHEMA_VERSION,
    _partition_dependency_issues,
    _normalize_requirements,
    collect_project_signals,
    derive_capabilities,
    main,
    render_markdown,
    validate_report,
)
from argus_skill.verticals.kernel_engineering.tool_registry import (
    filter_entries,
    load_registry,
    probe_entries,
    validate_registry,
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
    assert signals["project_name"] == "demo"


def test_dependency_health_blocks_project_and_selected_stack_only() -> None:
    critical, unrelated = _partition_dependency_issues(
        [
            "flash-linear-attention 0.5 requires transformers, which is not installed.",
            "apache-tvm-ffi 0.1.12 has requirement packaging<26, but you have 26.2.",
            "nvidia-dali-cuda120 1.50 has requirement packaging<=24.2, but you have 26.2.",
        ],
        {"flash-linear-attention", "tilelang", "apache-tvm-ffi"},
    )

    assert len(critical) == 2
    assert critical[0].startswith("flash-linear-attention")
    assert critical[1].startswith("apache-tvm-ffi")
    assert unrelated == [
        "nvidia-dali-cuda120 1.50 has requirement packaging<=24.2, but you have 26.2."
    ]


def test_validate_report_fails_red_audit_without_time_expiry(tmp_path: Path) -> None:
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (datetime.now(UTC) - timedelta(hours=30)).isoformat(),
        "project_root": str(tmp_path.resolve()),
        "requested_capabilities": ["tilelang"],
        "blocking_findings": ["Capability tilelang is not ready: nvcc"],
        "ready": False,
    }

    errors = validate_report(report, project_root=tmp_path)

    assert not any("stale" in item for item in errors)
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


def test_specialized_registry_is_broad_valid_and_tracks_legacy() -> None:
    registry = load_registry()
    assert validate_registry(registry) == []
    assert len(registry["entries"]) >= 85
    ids = {entry["id"] for entry in registry["entries"]}
    assert {
        "cutlass_cute",
        "tilelang",
        "helion",
        "thunderkittens",
        "flashinfer",
        "liger_kernel",
        "deepgemm",
        "nvshmem",
        "mscclpp",
        "deepep",
        "tritonbench",
        "kernel_tuner",
        "nvidia_mathdx",
        "cusparselt",
        "jax_pallas",
        "quack",
        "flashqla",
        "mirage",
        "verl",
    } <= ids
    assert any(entry["status"] in {"archived", "moved"} for entry in registry["entries"])


def test_catalog_filters_platform_category_and_legacy() -> None:
    registry = load_registry()
    entries = filter_entries(
        registry,
        categories=["attention"],
        platforms=["nvidia"],
    )
    ids = {entry["id"] for entry in entries}
    assert {"flash_attention", "flashinfer", "cutlass_cute"} <= ids
    assert "bitblas" not in ids

    with_legacy = filter_entries(
        registry,
        categories=["quantization"],
        platforms=["nvidia"],
        include_legacy=True,
    )
    assert "bitblas" in {entry["id"] for entry in with_legacy}


def test_registry_probe_detects_import_tool_and_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = {
        "entries": [
            {
                "id": "demo",
                "name": "Demo",
                "status": "active",
                "categories": ["attention"],
                "platforms": ["nvidia"],
                "official_url": "https://example.com",
                "use_when": "demo",
                "python_imports": ["demo"],
                "executables": ["demo-tool"],
                "source_markers": ["third_party/demo"],
            }
        ]
    }
    (tmp_path / "third_party" / "demo").mkdir(parents=True)
    monkeypatch.setattr(
        "argus_skill.verticals.kernel_engineering.tool_registry._probe_python_entries",
        lambda entries, target_python: {
            "demo": {
                "found_imports": ["demo"],
                "distribution": "demo-dist",
                "version": "1.2.3",
            }
        },
    )
    monkeypatch.setattr(
        "argus_skill.verticals.kernel_engineering.tool_registry.shutil.which",
        lambda name: "/usr/bin/demo-tool" if name == "demo-tool" else None,
    )

    records = probe_entries(
        registry,
        target_python="python",
        project_root=tmp_path,
    )

    assert records[0]["available"] is True
    assert records[0]["version"] == "1.2.3"
    assert records[0]["source_markers"] == ["third_party/demo"]


def test_catalog_cli_prints_specialized_attention_tools(capsys) -> None:
    assert main(["catalog", "--platform", "nvidia", "--category", "attention"]) == 0
    output = capsys.readouterr().out
    assert "flash_attention" in output
    assert "flashinfer" in output
    assert "bitblas" not in output

    assert main(["catalog", "--list-categories"]) == 0
    categories = capsys.readouterr().out
    assert "attention" in categories
    assert "communication" in categories
    assert "rl_stack" in categories

    assert main(["catalog", "--search", "expert-parallel"]) == 0
    search = capsys.readouterr().out
    assert "deepep" in search
