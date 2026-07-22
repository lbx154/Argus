from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from argus_skill.tools.new_auto_research_project import (
    STARTER_CODE_TEMPLATE_FILES,
    iter_starter_code_templates,
    seed_starter_code,
)

# The starter scaffolds are standalone modules under project_templates/code/.
SCAFFOLD_DIR = (
    Path(__file__).resolve().parents[2]
    / "argus_skill"
    / "tools"
    / "project_templates"
    / "code"
)


@pytest.fixture()
def scaffolds(monkeypatch: pytest.MonkeyPatch):
    """Import the standalone scaffold modules with their dir on sys.path."""
    monkeypatch.syspath_prepend(str(SCAFFOLD_DIR))
    for name in ("gpu_env", "experiment_io", "run_experiments"):
        sys.modules.pop(name, None)
    gpu_env = importlib.import_module("gpu_env")
    experiment_io = importlib.import_module("experiment_io")
    run_experiments = importlib.import_module("run_experiments")
    yield gpu_env, experiment_io, run_experiments
    for name in ("gpu_env", "experiment_io", "run_experiments"):
        sys.modules.pop(name, None)


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def test_starter_code_seeds_gpu_and_experiment_scaffolds(tmp_path: Path) -> None:
    for required in ("gpu_env.py", "experiment_io.py", "run_experiments.py"):
        assert required in STARTER_CODE_TEMPLATE_FILES

    seed_starter_code(tmp_path, overwrite=True)
    code_dir = tmp_path / "code"
    for required in ("gpu_env.py", "experiment_io.py", "run_experiments.py"):
        assert (code_dir / required).exists(), f"{required} was not seeded"


def test_starter_scaffolds_are_standalone() -> None:
    """Seeded code runs in the project venv, so it must not import argus_skill.

    A literal ``import argus_skill`` *statement* is forbidden; string references
    (e.g. the module name passed to a subprocess importability probe) are fine.
    """
    import re

    stmt = re.compile(r"^\s*(?:import|from)\s+argus_skill\b", re.MULTILINE)
    for name, text in iter_starter_code_templates():
        if name in {"gpu_env.py", "experiment_io.py", "run_experiments.py"}:
            assert not stmt.search(text), f"{name} imports argus_skill"


# --------------------------------------------------------------------------
# gpu_env
# --------------------------------------------------------------------------
def test_gpu_env_cache_paths_under_root(scaffolds, tmp_path: Path) -> None:
    gpu_env, _, _ = scaffolds
    env = gpu_env.cache_env(tmp_path / "models")
    assert env["HF_HOME"].endswith("models/huggingface")
    assert env["HUGGINGFACE_HUB_CACHE"].endswith("models/huggingface/hub")
    assert env["TORCH_HOME"].endswith("models/torch")


def test_gpu_env_visible_devices_from_env(scaffolds, monkeypatch: pytest.MonkeyPatch) -> None:
    gpu_env, _, _ = scaffolds
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
    assert gpu_env.visible_devices() == ["2", "3"]
    assert gpu_env.suggest_nproc() == 2
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert gpu_env.visible_devices() == []
    assert gpu_env.suggest_nproc() == 1


def test_gpu_env_configure_caches_sets_env(scaffolds, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gpu_env, _, _ = scaffolds
    monkeypatch.chdir(tmp_path)
    env = gpu_env.configure_caches()
    import os

    assert os.environ["HF_HOME"] == env["HF_HOME"]
    assert Path(env["HF_HOME"]).is_dir()


# --------------------------------------------------------------------------
# experiment_io
# --------------------------------------------------------------------------
def test_experiment_io_writes_full_run_contract(scaffolds, tmp_path: Path) -> None:
    _, experiment_io, _ = scaffolds
    run_dir = tmp_path / "runs" / "main"
    with experiment_io.RunWriter(run_dir, method="proposed", manifest={"benchmark": "demo"}, echo=False) as run:
        for i in range(3):
            run.start_task(f"t{i}")
            run.record(task_id=f"t{i}", prediction="yes", score=1.0)

    audit = experiment_io.validate_run(run_dir)
    assert audit["complete_contract"] is True
    assert audit["state"] == "completed"
    assert audit["rows_by_method"] == {"proposed": 3}
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "progress.jsonl").exists()
    assert (run_dir / "results.jsonl").exists()


def test_experiment_io_stop_cancels_with_exit_130(scaffolds, tmp_path: Path) -> None:
    _, experiment_io, _ = scaffolds
    run_dir = tmp_path / "runs" / "cancel"
    run_dir.mkdir(parents=True)
    (run_dir / "STOP").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        with experiment_io.RunWriter(run_dir, method="m", echo=False) as run:
            run.raise_if_stopped(force=True)
            run.record(task_id="x", score=1)
    assert excinfo.value.code == 130
    assert (run_dir / "run_cancelled").exists()
    audit = experiment_io.validate_run(run_dir)
    assert audit["cancelled"] is True
    assert audit["state"] == "cancelled"


def test_experiment_io_count_rows_dedupes_task_ids(scaffolds, tmp_path: Path) -> None:
    _, experiment_io, _ = scaffolds
    results = tmp_path / "results.jsonl"
    results.write_text(
        '{"method": "a", "task_id": "1", "score": 1}\n'
        '{"method": "a", "task_id": "1", "score": 0}\n'
        '{"method": "a", "task_id": "2", "score": 1}\n'
        '{"method": "b", "task_id": "1", "score": 1}\n',
        encoding="utf-8",
    )
    assert experiment_io.count_rows_by_method(results) == {"a": 2, "b": 1}


# --------------------------------------------------------------------------
# run_experiments
# --------------------------------------------------------------------------
def test_run_experiments_explicit_policy_requires_gpus(scaffolds) -> None:
    _, _, run_experiments = scaffolds
    matrix = {"gpu_policy": "explicit", "conditions": [{"id": "a", "command": "echo hi"}]}
    with pytest.raises(ValueError, match="requires"):
        run_experiments.assign_gpus(matrix)


def test_run_experiments_fanout_round_robins_visible_gpus(
    scaffolds, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, run_experiments = scaffolds
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    matrix = {
        "gpu_policy": "fanout_one_gpu",
        "conditions": [{"id": f"c{i}", "command": "x"} for i in range(4)],
    }
    plans = run_experiments.assign_gpus(matrix)
    assert [p["gpus"] for p in plans] == ["0", "1", "0", "1"]


def test_run_experiments_build_argv_wraps_cuda_visible_devices(scaffolds) -> None:
    _, _, run_experiments = scaffolds
    plan = {
        "id": "proposed",
        "command": ".venv/bin/python code/run.py",
        "gpus": "1,2",
        "cpu_count": 4,
        "cpu_ids": None,
        "timeout": 100,
    }
    argv = run_experiments.build_submit_argv("pyx", plan, "desc")
    assert argv[:4] == ["pyx", "-m", "argus_skill.tools.subagent", "submit"]
    command_index = argv.index("--command") + 1
    assert argv[command_index] == "env CUDA_VISIBLE_DEVICES=1,2 .venv/bin/python code/run.py"
    assert argv[argv.index("--cpu-count") + 1] == "4"


def test_run_experiments_forwards_explicit_cpu_ids(scaffolds) -> None:
    _, _, run_experiments = scaffolds
    plan = {
        "id": "cpu-bound",
        "command": ".venv/bin/python code/run.py",
        "gpus": "",
        "cpu_count": 0,
        "cpu_ids": "4,5",
        "timeout": 100,
    }
    argv = run_experiments.build_submit_argv("pyx", plan, "desc")
    assert argv[argv.index("--cpu-ids") + 1] == "4,5"


def test_run_experiments_rejects_empty_matrix(scaffolds, tmp_path: Path) -> None:
    _, _, run_experiments = scaffolds
    path = tmp_path / "MATRIX.json"
    path.write_text('{"conditions": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        run_experiments.load_matrix(path)
