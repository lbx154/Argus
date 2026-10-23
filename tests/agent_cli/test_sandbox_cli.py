"""The agent sandbox against the real agent CLIs and their own tools.

Opt-in, like the other real-CLI tests: set ``ARGUS_TEST_SANDBOX_CLIS`` to a
comma-separated subset of ``pi,copilot,claude,codex`` (``name=/path/to/cli``
picks a binary). Each CLI is driven through ``AgentCliRunner.run_exec``
against a local scripted model (``scripted_provider.py``, from PR #302): no
credentials, no network. The model asks the CLI's file and shell tools for
every other project's and tenant's file, then for its own; the test reads back
what the tools returned.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from tests.agent_cli.scripted_provider import ScriptedProvider

SELECTED = dict(
    (name.strip(), path.strip() or None)
    for entry in os.environ.get("ARGUS_TEST_SANDBOX_CLIS", "").split(",") if entry.strip()
    for name, _, path in [entry.partition("=")]
)
AGENTS = [("tenant-a", "alpha"), ("tenant-b", "beta")]


def _script(backend: str, others: list[Path], own: Path) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    for target in others:
        shell = f"D={target.parent}; cat $D/{target.name}"
        calls += {
            "pi": [("read", {"path": str(target)}), ("bash", {"command": shell})],
            "copilot": [("view", {"path": str(target)}), ("bash", {"command": shell, "description": "read"})],
            "claude": [("Read", {"file_path": str(target)}), ("Bash", {"command": shell})],
            "codex": [("exec_command", {"cmd": shell}),
                      ("exec_command", {"cmd": f"cat {target.name}", "workdir": str(target.parent)})],
        }[backend]
    own_call = {"pi": ("read", {"path": str(own)}), "copilot": ("view", {"path": str(own)}),
                "claude": ("Read", {"file_path": str(own)}), "codex": ("exec_command", {"cmd": f"cat {own}"})}
    return [*calls, own_call[backend]]


@pytest.mark.parametrize("tenant,project", AGENTS)
@pytest.mark.parametrize("backend", ["pi", "copilot", "claude", "codex"])
def test_real_cli_tools_cannot_read_another_project_or_tenant(
    backend, tenant, project, tmp_path, monkeypatch, host_sandbox,
):
    if backend not in SELECTED:
        pytest.skip(f"set ARGUS_TEST_SANDBOX_CLIS to include {backend}")
    secrets: dict[tuple[str, str], Path] = {}
    for t in ("tenant-a", "tenant-b"):
        for p in ("alpha", "beta"):
            workdir = tmp_path / t / "work" / p
            workdir.mkdir(parents=True)
            secrets[t, p] = workdir / "secret.txt"
            secrets[t, p].write_text(f"PRIVATE-{t}-{p}\n")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / tenant / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / tenant / "home"))
    own = secrets[tenant, project]
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
        result = AgentCliRunner(SELECTED[backend], backend=backend).run_exec(
            prompt="Read the files.", resume_thread_id=None,
            options=RunnerOptions(model=model, dangerous_yolo=True, skip_git_repo_check=True,
                                  working_dir=str(own.parent), extra_args=extra),
        )
        results = provider.tool_results()

    assert result.command[0] == host_sandbox
    assert result.exit_code == 0 or backend == "copilot", result.fatal_error
    assert len(results) == len(others) * 2 + 1, results
    transcript = "\n".join(results)
    for (t, p), secret in secrets.items():
        assert (f"PRIVATE-{t}-{p}" in transcript) == ((t, p) == (tenant, project)), (t, p, transcript)
