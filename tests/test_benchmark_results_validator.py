from __future__ import annotations

from pathlib import Path

from benchmarks.validate_results import (
    validate_experiment_dir,
    validate_results_root,
)


def _write_file(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_complete_experiment(root: Path, *, with_exempt: bool = False) -> Path:
    exp = root / "tb2-ablation-2026-05-10-new"
    _write_file(exp / "PLAN.md")
    _write_file(exp / "BUILD_INFO.md")
    _write_file(exp / "RESULTS.md")
    _write_file(exp / "aggregate.py", "print('ok')\n")
    _write_file(exp / "run-ablation.sh", "#!/usr/bin/env bash\n")
    _write_file(exp / "driver.stdout.log")
    _write_file(
        exp / "summary.tsv",
        "\t".join([
            "cond",
            "task",
            "reward",
            "wall_s",
            "eng_in_tok",
            "eng_cached_in_tok",
            "eng_out_tok",
            "rev_in_tok",
            "rev_cached_in_tok",
            "rev_out_tok",
            "sci_tokens",
            "sci_cached_in_tok",
            "model_eng",
            "model_rev",
            "model_sci",
            "cost_usd",
        ]) + "\n" + "\t".join(["c", "t", "1.0", "2.0", "3", "4", "5", "6", "7", "8", "9", "10", "m1", "m2", "m3", "12.3"]) + "\n",
    )
    _write_file(exp / "C0" / "task" / "jobs" / "trial.log")
    if with_exempt:
        _write_file(exp / "EXEMPT.md", "legacy\n")
    return exp


def test_validate_complete_experiment_dir(tmp_path: Path) -> None:
    exp = _make_complete_experiment(tmp_path)
    assert validate_experiment_dir(exp) == []


def test_validate_experiment_dir_requires_summary_columns(tmp_path: Path) -> None:
    exp = tmp_path / "tb2-ablation-2026-05-10-bad"
    _write_file(exp / "PLAN.md")
    _write_file(exp / "BUILD_INFO.md")
    _write_file(exp / "RESULTS.md")
    _write_file(exp / "aggregate.py", "print('ok')\n")
    _write_file(exp / "run-ablation.sh", "#!/usr/bin/env bash\n")
    _write_file(exp / "driver.stdout.log")
    _write_file(exp / "summary.tsv", "cond\ttask\treward\n")
    _write_file(exp / "C0" / "task" / "jobs" / "trial.log")

    issues = validate_experiment_dir(exp)
    assert any("missing required summary columns" in issue.message for issue in issues)


def test_validate_experiment_dir_allows_explicit_exemption(tmp_path: Path) -> None:
    exp = _make_complete_experiment(tmp_path, with_exempt=True)
    _write_file(exp / "summary.tsv", "cond\ttask\treward\n")
    assert validate_experiment_dir(exp) == []


def test_validate_current_results_tree(tmp_path: Path) -> None:
    results_root = Path("benchmarks/results")
    if not results_root.exists():
        results_root = tmp_path / "results"
        _make_complete_experiment(results_root)
    issues = validate_results_root(results_root)
    assert issues == []
