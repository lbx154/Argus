"""SE-2 against the real agent CLIs: their own tools are refused other tenants' files.

Opt-in, like the other real-CLI tests: set ``ARGUS_TEST_BOUNDARY_CLIS`` to a
comma-separated subset of ``pi,copilot,claude,codex`` whose CLIs are on PATH.
Each CLI is driven through Argus's own runner (``AgentCliRunner.run_exec``)
against a local scripted model (``scripted_provider.py``): no credentials, no
network. The model asks the CLI's own file and shell tools for the other
projects' and the other tenant's files, then for its own; the test reads back
what the tools returned.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.core.workspace_boundary import FOREIGN_ROOTS_ENV, role_workspace_boundary
from tests.agent_cli.scripted_provider import ScriptedProvider

SELECTED = {name.strip() for name in os.environ.get("ARGUS_TEST_BOUNDARY_CLIS", "").split(",") if name.strip()}
AGENTS = [("tenant-a", "alpha"), ("tenant-b", "beta")]


def _script(backend: str, others: list[Path], own: Path) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    for target in others:
        if backend == "pi":
            calls += [("read", {"path": str(target)}), ("bash", {"command": f"cat {target}"})]
        elif backend == "copilot":
            calls += [("view", {"path": str(target)}), ("bash", {"command": f"cat {target}", "description": "read"})]
        elif backend == "claude":
            calls += [("Read", {"file_path": str(target)}), ("Bash", {"command": f"cat {target}"})]
        else:
            calls += [("exec_command", {"cmd": f"cat {target}"}),
                      ("exec_command", {"cmd": f"cat {target.name}", "workdir": str(target.parent)})]
    own_call = {"pi": ("read", {"path": str(own)}), "copilot": ("view", {"path": str(own)}),
                "claude": ("Read", {"file_path": str(own)}), "codex": ("exec_command", {"cmd": f"cat {own}"})}
    return [*calls, own_call[backend]]


@pytest.mark.parametrize("tenant,project", AGENTS)
@pytest.mark.parametrize("backend", ["pi", "copilot", "claude", "codex"])
def test_real_cli_tools_cannot_read_another_project_or_tenant(backend, tenant, project, tmp_path, monkeypatch):
    if backend not in SELECTED:
        pytest.skip(f"set ARGUS_TEST_BOUNDARY_CLIS to include {backend}")
    secrets: dict[tuple[str, str], Path] = {}
    for t in ("tenant-a", "tenant-b"):
        home = tmp_path / t / "home"
        for p in ("alpha", "beta"):
            workdir = tmp_path / t / "work" / p
            workdir.mkdir(parents=True)
            secret = workdir / "secret.txt"
            secret.write_text(f"PRIVATE-{t}-{p}\n")
            secrets[t, p] = secret
            state = home / "projects" / p
            state.mkdir(parents=True)
            (state / "session.json").write_text(json.dumps({"id": p, "workdir": str(workdir)}))
    other_tenant = "tenant-b" if tenant == "tenant-a" else "tenant-a"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / tenant / "home"))
    monkeypatch.setenv(FOREIGN_ROOTS_ENV, str(tmp_path / other_tenant))
    own = secrets[tenant, project]
    boundary = role_workspace_boundary(working_dir=own.parent, own_dirs=[tmp_path / tenant / "home" / "projects" / project])
    others = [path for key, path in secrets.items() if key != (tenant, project)]

    with ScriptedProvider(_script(backend, others, own)) as provider:
        model, extra = None, None
        if backend == "pi":
            agent = tmp_path / "pi-agent"
            agent.mkdir()
            (agent / "models.json").write_text(json.dumps({"providers": {"scripted": {
                "baseUrl": provider.base_url + "/v1", "api": "openai-completions", "apiKey": "offline",
                "models": [{"id": "scripted", "reasoning": False}]}}}))
            monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
            monkeypatch.setenv("PI_OFFLINE", "1")
            model = "scripted/scripted"
        elif backend == "copilot":
            monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", provider.base_url + "/v1")
            monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "offline")
            monkeypatch.setenv("COPILOT_MODEL", "gpt-4.1")
        elif backend == "claude":
            monkeypatch.setenv("ANTHROPIC_BASE_URL", provider.base_url)
            monkeypatch.setenv("ANTHROPIC_API_KEY", "offline")
            monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
            monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
            model = "claude-sonnet-4-5"
        else:
            monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
            (tmp_path / "codex").mkdir()
            monkeypatch.setenv("SCRIPTED_KEY", "offline")
            model = "scripted"
            extra = ["-c", "model_provider=scripted", "-c",
                     'model_providers.scripted={name="scripted",base_url="%s/v1",env_key="SCRIPTED_KEY",'
                     'wire_api="responses"}' % provider.base_url]
        result = AgentCliRunner(backend=backend).run_exec(
            prompt="Read the files.", resume_thread_id=None,
            options=RunnerOptions(model=model, dangerous_yolo=True, skip_git_repo_check=True,
                                  working_dir=str(own.parent), workspace_boundary=boundary, extra_args=extra),
        )
        results = provider.tool_results()

    assert result.exit_code == 0 or backend == "copilot", result.fatal_error
    assert len(results) == len(others) * 2 + 1, results
    for text in results[:-1]:
        assert "workspace boundary" in text, text
    transcript = "\n".join(results)
    for (t, p), secret in secrets.items():
        assert (f"PRIVATE-{t}-{p}" in transcript) == ((t, p) == (tenant, project)), (t, p)
