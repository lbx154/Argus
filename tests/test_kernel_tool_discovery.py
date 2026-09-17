"""Tool discovery must look beyond PATH.

A CUDA toolkit that is installed but not exported is the common case in
containers and on shared boxes. Reporting it as missing makes the agent
conclude the machine cannot do CUDA C++ work and quietly narrow its plan, so
the audit has to tell "not installed" apart from "installed, not on PATH".
"""
from __future__ import annotations

import stat

import pytest

from argus.verticals.kernel_engineering import environment_audit as audit


def _make_tool(directory, name: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\necho 'fake 1.0'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture(autouse=True)
def isolate_system_cuda(monkeypatch):
    """Ignore the host's real /usr/local/cuda* during these tests.

    The developer box has several toolkits installed, which would otherwise
    satisfy every lookup and make the assertions meaningless.
    """
    monkeypatch.setattr(audit, "_CUDA_ROOT_GLOBS", ())
    for var in ("CUDA_HOME", "CUDA_PATH", "CUDA_ROOT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def hidden_cuda(tmp_path, monkeypatch):
    """A CUDA toolkit on disk that PATH does not expose."""
    root = tmp_path / "usr" / "local" / "cuda-13.1"
    nvcc = _make_tool(root / "bin", "nvcc")
    monkeypatch.setenv("CUDA_HOME", str(root))
    # PATH deliberately does not contain the toolkit.
    monkeypatch.setattr(audit.shutil, "which", lambda _name: None)
    return root, nvcc


def test_finds_a_tool_that_is_installed_but_not_on_path(hidden_cuda) -> None:
    _root, nvcc = hidden_cuda

    assert audit.find_off_path_tool("nvcc") == nvcc


def test_reports_installed_not_on_path_rather_than_missing(hidden_cuda) -> None:
    _root, nvcc = hidden_cuda

    records = audit.collect_tools()

    assert records["nvcc"]["present"] is True
    assert records["nvcc"]["on_path"] is False
    assert records["nvcc"]["discovery"] == "installed_not_on_path"
    assert records["nvcc"]["path"] == nvcc


def test_genuinely_absent_tool_is_still_reported_missing(hidden_cuda) -> None:
    records = audit.collect_tools()

    # Only nvcc exists in the fixture toolkit.
    assert records["ptxas"]["present"] is False
    assert records["ptxas"]["discovery"] == "not_found"


def test_capability_derivation_sees_the_hidden_tool(hidden_cuda) -> None:
    # _present() drives derive_capabilities(); an installed-but-hidden tool
    # must count as present or cuda_cpp stays red for a fixable reason.
    records = audit.collect_tools()

    assert audit._present(records, "nvcc") is True


def test_path_repair_hint_points_at_the_directory(hidden_cuda) -> None:
    root, _nvcc = hidden_cuda

    hint = audit.path_repair_hint(audit.collect_tools())

    assert hint["needed"] is True
    assert "nvcc" in hint["tools"]
    assert str(root / "bin") in hint["directories"]
    assert hint["export"].startswith("export PATH=")
    assert hint["export"].endswith("$PATH")


def test_no_repair_hint_when_everything_is_on_path(monkeypatch, tmp_path) -> None:
    tool = _make_tool(tmp_path / "bin", "nvcc")
    monkeypatch.setattr(audit.shutil, "which", lambda name: tool if name == "nvcc" else None)

    hint = audit.path_repair_hint(audit.collect_tools())

    assert hint["needed"] is False
    assert hint["directories"] == []


def test_non_cuda_tools_are_not_hunted_off_path(tmp_path, monkeypatch) -> None:
    # gcc/git/jq belong on PATH; searching CUDA roots for them would only
    # produce confusing results.
    root = tmp_path / "cuda"
    _make_tool(root / "bin", "gcc")
    monkeypatch.setenv("CUDA_HOME", str(root))

    assert audit.find_off_path_tool("gcc") == ""


def test_explicit_cuda_home_wins_over_glob(tmp_path, monkeypatch) -> None:
    preferred = tmp_path / "opt" / "chosen-cuda"
    chosen = _make_tool(preferred / "bin", "ptxas")
    monkeypatch.setenv("CUDA_HOME", str(preferred))

    assert audit.find_off_path_tool("ptxas") == chosen


def test_nsight_nested_layout_is_discovered(tmp_path, monkeypatch) -> None:
    # ncu lives at <root>/nsight-compute/<version>/ncu, one level deeper.
    root = tmp_path / "cuda"
    ncu = _make_tool(root / "nsight-compute" / "2024.3.1", "ncu")
    monkeypatch.setenv("CUDA_HOME", str(root))

    assert audit.find_off_path_tool("ncu") == ncu


def test_extra_roots_are_searched_first(tmp_path, monkeypatch) -> None:
    # torch knows the toolkit it was built against even when the shell doesn't.
    torch_root = tmp_path / "torch-cuda"
    nvcc = _make_tool(torch_root / "bin", "nvcc")

    assert audit.find_off_path_tool("nvcc", [str(torch_root)]) == nvcc


def test_missing_root_is_tolerated(monkeypatch) -> None:
    monkeypatch.setenv("CUDA_HOME", "/definitely/not/here")

    assert audit.find_off_path_tool("nvcc", ["/also/not/here"]) == ""
