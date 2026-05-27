from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import benchmarks.tb2_fullbench_launcher as tb2_fullbench_launcher
import benchmarks.tb2_fullbench_matrix_launcher as tb2_fullbench_matrix_launcher
from benchmarks.tb2_fullbench_launcher import _build_spec


def test_argus_and_bare_conditions_build_distinct_commands(tmp_path: Path) -> None:
    argus = _build_spec("argus-v12-redux", tmp_path, None)
    bare = _build_spec("bare-gpt54", tmp_path, None)
    mini = _build_spec("bare-gpt54-mini", tmp_path, None)

    assert argus.run_root == tmp_path.resolve()
    assert argus.command[0:3] == ["sg", "docker", "-c"]
    assert "benchmarks.harbor_adapter:ArgusSkillCodex" in argus.command[3]
    assert "--model openai/gpt-5.4-mini" in argus.command[3]
    assert argus.metadata["dataset_id"] == "terminal-bench@2.0"
    assert argus.metadata["pricing_source"] == "argus_skill.core.pricing.usd_for_tokens"
    assert argus.env["ARGUS_SKILL_HARBOR_REVIEWER_GATE"] == "0"
    assert argus.env["ARGUS_SKILL_HARBOR_REVIEWER_EFFORT"] == "high"
    assert argus.env["ARGUS_SKILL_HARBOR_VERIFIER_PASS_SHORT_CIRCUIT"] == "1"
    assert 'OPENAI_BASE_URL="$OPENAI_BASE_URL"' in argus.command[3]

    assert "benchmarks.harbor_adapter:ArgusSkillCodex" not in bare.command[3]
    assert "--model openai/gpt-5.4" in bare.command[3]
    assert bare.metadata["pricing_source"] == "harbor default pricing"
    assert 'OPENAI_BASE_URL="$OPENAI_BASE_URL"' in bare.command[3]

    assert "--model openai/gpt-5.4-mini" in mini.command[3]
    assert mini.metadata["model_ids"]["engineer"] == "openai/gpt-5.4-mini"


def test_shared_env_normalizes_openai_base_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/openai/v1")

    spec = _build_spec("bare-gpt54", tmp_path, None)

    assert spec.env["OPENAI_BASE_URL"] == "https://example.invalid/openai/v1/"


def test_shared_env_forces_default_codex_auth_json_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"secret"}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))

    spec = _build_spec("bare-gpt54", tmp_path, None)

    assert spec.env["CODEX_FORCE_AUTH_JSON"] == "1"
    assert spec.env["OPENAI_BASE_URL"] == "https://ai4m6.openai.azure.com/openai/v1/"


def test_discover_task_images_and_fail_fast_on_rate_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_root = tmp_path / "tasks"
    task_dir = cache_root / "abc123" / "cancel-async-tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        "\n".join(
            [
                "version = \"1.0\"",
                "",
                "[environment]",
                'docker_image = "alexgshaw/cancel-async-tasks:20251031"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_MODE", "pull")

    calls: list[list[str]] = []

    def _fake_run(
        command: list[str],
        *,
        check: bool = False,  # noqa: ARG001
        capture_output: bool = False,  # noqa: ARG001
        text: bool = False,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="error from registry: You have reached your unauthenticated pull rate limit.",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(tb2_fullbench_launcher.subprocess, "run", _fake_run)

    images = tb2_fullbench_launcher._discover_task_images(task_cache_root=cache_root)
    assert images == ["alexgshaw/cancel-async-tasks:20251031"]

    preflight = tb2_fullbench_launcher._preflight_tb2_images(task_cache_root=cache_root)

    assert preflight is not None
    assert preflight["state"] == "launch_failed"
    assert preflight["missing_image"] == "alexgshaw/cancel-async-tasks:20251031"
    assert preflight["rate_limit"] is True
    assert len(calls) == 2


def test_preflight_auto_continues_when_task_cache_is_empty_and_no_artifact_roots_are_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_root = tmp_path / "empty-cache"
    cache_root.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_MODE", "auto")

    def _unexpected_run(*args, **kwargs):  # noqa: ANN001, ANN002, D401
        raise AssertionError("docker commands should not run when no task images are discovered")

    monkeypatch.setattr(tb2_fullbench_launcher.subprocess, "run", _unexpected_run)

    preflight = tb2_fullbench_launcher._preflight_tb2_images(
        task_cache_root=cache_root,
        artifact_roots=(),
    )

    assert preflight is not None
    assert preflight["state"] == "preflight_complete"
    assert preflight["missing_task_metadata"] is True
    assert str(cache_root) in preflight["message"]
    assert preflight["checked_images"] == []
    assert preflight["deferred_images"] == []
    assert preflight["staged_images"] == []


def test_preflight_uses_artifact_roots_when_task_cache_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_root = tmp_path / "empty-cache"
    cache_root.mkdir()
    artifact_root = tmp_path / "repo-artifacts"
    source = artifact_root / "benchmarks" / "evidence" / "bundle-a" / "jobs" / "raw" / "trial-a"
    source.mkdir(parents=True)
    (source / "result.json").write_text(
        '{"docker_image": "alexgshaw/cancel-async-tasks:20251031"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_MODE", "pull")

    calls: list[list[str]] = []

    def _fake_run(
        command: list[str],
        *,
        check: bool = False,  # noqa: ARG001
        capture_output: bool = False,  # noqa: ARG001
        text: bool = False,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(tb2_fullbench_launcher.subprocess, "run", _fake_run)

    preflight = tb2_fullbench_launcher._preflight_tb2_images(
        task_cache_root=cache_root,
        artifact_roots=(artifact_root,),
    )

    assert preflight is not None
    assert preflight["state"] == "preflight_complete"
    assert preflight["checked_images"] == [
        {"image": "alexgshaw/cancel-async-tasks:20251031", "present": True}
    ]
    assert preflight["staged_images"] == ["alexgshaw/cancel-async-tasks:20251031"]
    assert calls == [["docker", "image", "inspect", "alexgshaw/cancel-async-tasks:20251031"]]


def test_preflight_auto_records_missing_images_without_pulling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_root = tmp_path / "tasks"
    task_dir = cache_root / "abc123" / "cancel-async-tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        "\n".join(
            [
                "version = \"1.0\"",
                "",
                "[environment]",
                'docker_image = "alexgshaw/cancel-async-tasks:20251031"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("ARGUS_SKILL_TB2_PREFLIGHT_MODE", "auto")

    calls: list[list[str]] = []

    def _fake_run(
        command: list[str],
        *,
        check: bool = False,  # noqa: ARG001
        capture_output: bool = False,  # noqa: ARG001
        text: bool = False,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(tb2_fullbench_launcher.subprocess, "run", _fake_run)

    preflight = tb2_fullbench_launcher._preflight_tb2_images(task_cache_root=cache_root)

    assert preflight is not None
    assert preflight["state"] == "preflight_complete"
    assert "missing_task_metadata" not in preflight
    assert preflight["checked_images"] == [
        {
            "image": "alexgshaw/cancel-async-tasks:20251031",
            "present": False,
            "deferred": True,
            "staged": False,
        }
    ]
    assert preflight["staged_images"] == []
    assert preflight["deferred_images"] == ["alexgshaw/cancel-async-tasks:20251031"]
    assert calls == [["docker", "image", "inspect", "alexgshaw/cancel-async-tasks:20251031"]]


def test_matrix_launcher_launches_replicates_with_deterministic_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launched: list[dict[str, Any]] = []

    def _fake_launch_detached(spec):
        launched.append(
            {
                "run_id": spec.run_id,
                "run_root": str(spec.run_root),
                "metadata": dict(spec.metadata),
                "command": list(spec.command),
            }
        )
        run_dir = spec.run_root / spec.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": spec.run_id,
                    "metadata": dict(spec.metadata),
                    "command": list(spec.command),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (run_dir / "status.json").write_text(
            json.dumps({"run_id": spec.run_id, "state": "running"}, sort_keys=True),
            encoding="utf-8",
        )
        (run_dir / "pid").write_text("1234\n", encoding="utf-8")
        (run_dir / "stdout.log").write_text("", encoding="utf-8")
        (run_dir / "stderr.log").write_text("", encoding="utf-8")
        return run_dir

    monkeypatch.setattr(tb2_fullbench_matrix_launcher, "launch_detached", _fake_launch_detached)

    summary = tb2_fullbench_matrix_launcher.launch_sweep(
        conditions=["argus-v12-true", "bare-gpt54"],
        run_root=tmp_path / "experiments",
        replicates=3,
        sweep_id="tb2-sweep-smoke",
    )

    assert summary["launcher"] == "tb2_fullbench_matrix_launcher"
    assert summary["sweep_id"] == "tb2-sweep-smoke"
    assert summary["run_count"] == 6
    assert summary["conditions"] == ["argus-v12-true", "bare-gpt54"]

    run_ids = [entry["run_id"] for entry in summary["runs"]]
    assert run_ids == [
        "argus-v12-true-r01-of03",
        "argus-v12-true-r02-of03",
        "argus-v12-true-r03-of03",
        "bare-gpt54-r01-of03",
        "bare-gpt54-r02-of03",
        "bare-gpt54-r03-of03",
    ]

    assert len(launched) == 6
    assert {entry["metadata"]["replicate_index"] for entry in launched} == {1, 2, 3}
    assert {entry["metadata"]["replicate_total"] for entry in launched} == {3}
    assert {entry["metadata"]["sweep_id"] for entry in launched} == {"tb2-sweep-smoke"}
    assert {tuple(entry["metadata"]["sweep_conditions"]) for entry in launched} == {
        ("argus-v12-true", "bare-gpt54")
    }

    sweep_root = tmp_path / "experiments" / "tb2-sweep-smoke"
    assert (sweep_root / "launch-summary.json").exists()
    for entry in summary["runs"]:
        run_dir = Path(entry["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_id"] == entry["run_id"]
        assert manifest["metadata"]["replicate_index"] == entry["replicate_index"]
        assert manifest["metadata"]["replicate_total"] == 3
        assert manifest["metadata"]["sweep_id"] == "tb2-sweep-smoke"
        assert manifest["metadata"]["sweep_conditions"] == ["argus-v12-true", "bare-gpt54"]
        assert (run_dir / "status.json").exists()
        assert (run_dir / "pid").exists()
        assert (run_dir / "stdout.log").exists()
        assert (run_dir / "stderr.log").exists()


def test_matrix_launcher_cli_prints_machine_readable_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = {
        "launcher": "tb2_fullbench_matrix_launcher",
        "sweep_id": "tb2-sweep-smoke",
        "run_root": "/tmp/experiments",
        "sweep_root": "/tmp/experiments/tb2-sweep-smoke",
        "conditions": ["argus-v12-true", "bare-gpt54"],
        "replicates": 3,
        "run_count": 6,
        "runs": [],
    }
    monkeypatch.setattr(
        tb2_fullbench_matrix_launcher,
        "launch_sweep",
        lambda **kwargs: summary,
    )

    exit_code = tb2_fullbench_matrix_launcher.main(
        [
            "--condition",
            "argus-v12-true",
            "--condition",
            "bare-gpt54",
            "--replicates",
            "3",
            "--sweep-id",
            "tb2-sweep-smoke",
            "--run-root",
            "/tmp/experiments",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == summary
