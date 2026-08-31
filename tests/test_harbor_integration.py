from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import types
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from argus_skill.integrations.harbor import (
    ArgusHarborAgent,
    HarborUnavailableError,
    _latest_project_root,
    harbor_available,
)
from argus_skill.life.supervisor._planning_cycle_enqueue import (
    _independent_review_forced,
    _stage_closing_forced,
)


def _assert_bash_syntax(command: str) -> None:
    # Harbor executes inside a Linux container. On native Windows, `bash` may
    # be a WSL relay without an installed distribution, so syntax validation is
    # covered by Linux CI rather than producing a host-environment false alarm.
    if os.name == "nt":
        return
    assert subprocess.run(
        ["bash", "-n"], input=command, text=True, check=False,
    ).returncode == 0


def test_latest_project_root_selects_latest_completed_state(tmp_path: Path) -> None:
    older = tmp_path / "projects" / "older"
    newer = tmp_path / "projects" / "newer"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    older_state = older / "continuous.json"
    newer_state = newer / "continuous.json"
    older_state.write_text("{}", encoding="utf-8")
    newer_state.write_text("{}", encoding="utf-8")
    # Some filesystems give back-to-back writes the same timestamp. Pin the
    # ordering explicitly so this test checks selection rather than clock
    # granularity.
    os.utime(older_state, (1, 1))
    os.utime(newer_state, (2, 2))

    assert _latest_project_root(tmp_path) == newer


def test_harbor_review_policy_reaches_planner_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_INDEPENDENT_REVIEW", "1")
    monkeypatch.setenv("ARGUS_SKILL_FORCE_STAGE_CLOSING", "1")
    assert _independent_review_forced() is True
    assert _stage_closing_forced() is True


def test_missing_harbor_dependency_has_actionable_error() -> None:
    if harbor_available():
        pytest.skip("Harbor is installed in this test environment")
    assert ArgusHarborAgent.name() == "argus"
    with pytest.raises(HarborUnavailableError, match=r"Python 3\.12"):
        ArgusHarborAgent()


class _ExecResult:
    def __init__(
        self,
        *,
        stdout: str | None = "",
        stderr: str | None = "",
        return_code: int = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


class _FakeEnvironment:
    default_user = "agent"

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []
        self.uploads: list[tuple[Path, str]] = []

    async def upload_file(self, source: Path, target: str) -> None:
        self.uploads.append((Path(source), target))

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> _ExecResult:
        self.commands.append(
            {
                "command": command,
                "cwd": cwd,
                "env": dict(env or {}),
                "timeout_sec": timeout_sec,
                "user": user,
            }
        )
        return _ExecResult(stdout="ok")


def _load_adapter_with_fake_harbor(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    class Access:
        api_key = "sk-test-sensitive-value"
        configured_base_url = "https://example.test/v1"

    class FakeCodex:
        SUPPORTS_ATIF = True

        def __init__(
            self,
            *args: Any,
            version: str | None = None,
            model_name: str | None = None,
            logs_dir: Path | None = None,
            extra_env: dict[str, str] | None = None,
            **kwargs: Any,
        ) -> None:
            _ = (args, extra_env)
            self._version = version
            self.model_name = model_name
            self.logs_dir = logs_dir or Path("/tmp/fake-harbor")
            self.logger = logging.getLogger("fake-harbor")
            self.model_connection = Access()
            self._config = kwargs
            self.system_dependencies: tuple[str, ...] = ()

        async def install(self, environment: _FakeEnvironment) -> None:
            await environment.exec(command="install-codex")

        async def ensure_system_dependencies(
            self,
            environment: _FakeEnvironment,
            dependencies: tuple[str, ...],
        ) -> None:
            _ = environment
            self.system_dependencies = dependencies

        async def exec_as_agent(
            self,
            environment: _FakeEnvironment,
            command: str,
            env: dict[str, str] | None = None,
            cwd: str | None = None,
            timeout_sec: int | None = None,
        ) -> _ExecResult:
            return await environment.exec(
                command=command,
                env=env,
                cwd=cwd,
                timeout_sec=timeout_sec,
                user=environment.default_user,
            )

        async def exec_as_root(
            self,
            environment: _FakeEnvironment,
            command: str,
            env: dict[str, str] | None = None,
            cwd: str | None = None,
            timeout_sec: int | None = None,
        ) -> _ExecResult:
            return await environment.exec(
                command=command,
                env=env,
                cwd=cwd,
                timeout_sec=timeout_sec,
                user="root",
            )

        def _resolve_auth_json_path(self) -> None:
            return None

        def _build_effective_config(self, base_url: str | None) -> dict[str, Any]:
            return {"base_url": base_url}

        async def _upload_effective_config(
            self,
            environment: _FakeEnvironment,
            config: dict[str, Any],
            target: str,
        ) -> None:
            _ = (environment, config, target)

    class FakeEnvironmentPaths:
        agent_dir = PurePosixPath("/logs/agent")

    modules: dict[str, types.ModuleType] = {}
    for name in (
        "harbor",
        "harbor.agents",
        "harbor.agents.installed",
        "harbor.environments",
        "harbor.models",
        "harbor.models.agent",
        "harbor.models.trial",
    ):
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        modules[name] = module
    codex_module = types.ModuleType("harbor.agents.installed.codex")
    codex_module.Codex = FakeCodex
    environment_module = types.ModuleType("harbor.environments.base")
    environment_module.BaseEnvironment = object
    context_module = types.ModuleType("harbor.models.agent.context")
    context_module.AgentContext = object
    paths_module = types.ModuleType("harbor.models.trial.paths")
    paths_module.EnvironmentPaths = FakeEnvironmentPaths
    modules.update(
        {
            codex_module.__name__: codex_module,
            environment_module.__name__: environment_module,
            context_module.__name__: context_module,
            paths_module.__name__: paths_module,
        }
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    source = Path(__file__).resolve().parents[1] / "argus_skill" / "integrations" / "harbor.py"
    module_name = "argus_skill.integrations._harbor_contract_test"
    spec = importlib.util.spec_from_file_location(module_name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_harbor_directly_installs_and_invokes_argus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_adapter_with_fake_harbor(monkeypatch)
    environment = _FakeEnvironment()
    agent = module.ArgusHarborAgent(
        logs_dir=tmp_path,
        model_name="openai/gpt-5.4-mini",
        argus_package="argus-skill @ https://packages.test/argus.whl",
        reasoning_effort="high",
        timeout="900",
    )

    asyncio.run(agent.install(environment))
    asyncio.run(agent.run("Fix the task through the full Argus team.", environment, object()))

    commands = [entry["command"] for entry in environment.commands]
    assert commands[0] == "install-codex"
    assert "python3" in agent.system_dependencies
    install_command = next(
        command
        for command in commands
        if "pip install" in command and "packages.test/argus.whl" in command
    )
    _assert_bash_syntax(install_command)
    auth_command = next(command for command in commands if "OPENAI_API_KEY" in command)
    assert "sk-test-sensitive-value" not in auth_command
    _assert_bash_syntax(auth_command)
    runtime = next(command for command in commands if "--daemon-fg" in command)
    assert "--continuous --bounded --new" in runtime
    assert "--objective-file /logs/agent/argus-objective.txt" in runtime
    assert "--life-dir /logs/agent/argus-state" in runtime
    assert "Fix the task" not in runtime
    _assert_bash_syntax(runtime)
    runtime_call = next(entry for entry in environment.commands if entry["command"] == runtime)
    assert runtime_call["timeout_sec"] == 900
    assert runtime_call["env"]["ARGUS_SKILL_RUNNER_BACKEND"] == "codex"
    assert runtime_call["env"]["ARGUS_SKILL_MODEL"] == "gpt-5.4-mini"
    assert runtime_call["env"]["ARGUS_SKILL_REQUIRE_INDEPENDENT_REVIEW"] == "1"
    assert runtime_call["env"]["ARGUS_SKILL_FORCE_STAGE_CLOSING"] == "1"
    assert environment.uploads[0][1] == "/logs/agent/argus-objective.txt"
    assert environment.uploads[0][0].read_text(encoding="utf-8") == (
        "Fix the task through the full Argus team."
    )


def test_harbor_uploads_current_source_wheel_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_adapter_with_fake_harbor(monkeypatch)
    source_root = tmp_path / "source"
    source_root.mkdir()

    def fake_build(_source_root: Path, output_dir: Path) -> Path:
        assert _source_root == source_root
        wheel = output_dir / "argus_skill-0.1.1-py3-none-any.whl"
        wheel.write_bytes(b"wheel")
        return wheel

    monkeypatch.setattr(module, "_argus_source_root", lambda: source_root)
    monkeypatch.setattr(module, "_build_local_wheel", fake_build)
    environment = _FakeEnvironment()
    agent = module.ArgusHarborAgent(
        logs_dir=tmp_path / "logs",
        model_name="openai/gpt-5.4-mini",
    )

    asyncio.run(agent.install(environment))

    assert any(target.endswith(".whl") for _source, target in environment.uploads)
    assert any(
        "pip install /tmp/argus_skill-0.1.1-py3-none-any.whl" in entry["command"]
        for entry in environment.commands
    )


def test_harbor_context_reports_argus_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_adapter_with_fake_harbor(monkeypatch)
    project = tmp_path / "argus-state" / "projects" / "s-test"
    project.mkdir(parents=True)
    (project / "continuous.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "done_reason": "planner declared project done",
                "done_at": "2026-08-14T06:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    summary = types.SimpleNamespace(
        input_tokens=100,
        cached_input_tokens=20,
        output_tokens=30,
        reasoning_output_tokens=40,
        cost_usd=1.25,
        call_count=8,
        pricing_status="complete",
    )
    monkeypatch.setattr(module, "project_usage_summary", lambda _root: summary)
    agent = module.ArgusHarborAgent(
        logs_dir=tmp_path,
        model_name="openai/gpt-5.4-mini",
    )
    context = types.SimpleNamespace(
        n_input_tokens=None,
        n_cache_tokens=None,
        n_output_tokens=None,
        cost_usd=None,
        metadata=None,
    )

    agent.populate_context_post_run(context)

    assert context.n_input_tokens == 100
    assert context.n_cache_tokens == 20
    assert context.n_output_tokens == 70
    assert context.cost_usd == 1.25
    assert context.metadata["argus"] == {
        "state_dir": "argus-state",
        "runtime_log": "argus-runtime.log",
        "project": "s-test",
        "completed": True,
        "done_reason": "planner declared project done",
        "done_at": "2026-08-14T06:00:00Z",
        "calls": 8,
        "pricing_status": "complete",
    }
