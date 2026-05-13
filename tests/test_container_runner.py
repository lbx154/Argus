from __future__ import annotations

import json
import shlex
from pathlib import Path

from argus_skill.core.models import RunnerOptions
from argus_skill.runners.container import (
    ContainerCodexRunner,
    ContainerCodexRunnerConfig,
)
from benchmarks.swebench_pro.docker_env import ExecResult, MinimalDockerEnvironment


class _RecordingEnvironment(MinimalDockerEnvironment):
    def __init__(
        self,
        *,
        results: list[ExecResult],
        default_workdir: str = "/workspace",
    ) -> None:
        super().__init__("fake-container")
        self.default_workdir = default_workdir
        self._results = list(results)
        self.calls: list[dict[str, object | None]] = []

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        self.calls.append(
            {
                "command": command,
                "cwd": cwd,
                "env": dict(env) if env is not None else None,
                "timeout_sec": timeout_sec,
                "user": user,
            }
        )
        if not self._results:
            raise AssertionError("no more fake ExecResult values queued")
        return self._results.pop(0)

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        return None


def _parse_agent_messages(stdout: str) -> list[str]:
    messages: list[str] = []
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            messages.append(str(item["text"]))
    return messages


def _extract_thread_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


def _make_runner(
    env: _RecordingEnvironment,
    *,
    skill_text: str = "",
    cli_flags_arg: str = "",
    model: str = "gpt-5.4",
) -> ContainerCodexRunner:
    return ContainerCodexRunner(
        environment=env,
        env_vars={"OPENAI_API_KEY": "test-key"},
        config=ContainerCodexRunnerConfig(
            model=model,
            cli_flags_arg=cli_flags_arg,
            skill_text=skill_text,
            round_timeout=9,
        ),
        loop=None,
        codex_run_result_cls=object,
        agent_message_parser=_parse_agent_messages,
        thread_id_extractor=_extract_thread_id,
    )


def test_run_exec_fresh_run_builds_command_and_parses_stdout() -> None:
    env = _RecordingEnvironment(
        results=[
            ExecResult(
                return_code=0,
                stdout=(
                    '{"type":"thread.started","thread_id":"thr-1"}\n'
                    '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
                ),
                stderr="",
            )
        ],
        default_workdir="/repo",
    )
    runner = _make_runner(
        env,
        skill_text="## Skill guide\n- follow it",
        cli_flags_arg='-c profile=tb',
    )
    options = RunnerOptions(
        model="gpt-5.4-mini",
        reasoning_effort="high",
        output_schema_path="/tmp/schema.json",
        full_auto=True,
        skip_git_repo_check=True,
        extra_args=["--alpha"],
    )

    result = runner.run_exec(
        prompt="Fix bug",
        options=options,
        run_label="engineer-r1",
    )

    expected_prompt = "## Skill guide\n- follow it\n\nFix bug"
    expected_command = shlex.join(
        [
            "codex",
            "exec",
            "--json",
            "-m",
            "gpt-5.4-mini",
            "--full-auto",
            "--skip-git-repo-check",
            "--output-schema",
            "/tmp/schema.json",
            "-c",
            "profile=tb",
            "-c",
            "model_reasoning_effort=high",
            "--alpha",
            "-",
        ]
    )

    assert env.calls == [
        {
            "command": f"printf %s {shlex.quote(expected_prompt)} | {expected_command}",
            "cwd": "/repo",
            "env": {"OPENAI_API_KEY": "test-key"},
            "timeout_sec": 9,
            "user": "root",
        }
    ]
    assert result.exit_code == 0
    assert result.agent_messages == ["done"]
    assert result.thread_id == "thr-1"
    assert result.fatal_error is None


def test_run_exec_resume_skips_schema_and_uses_stderr_on_failure() -> None:
    env = _RecordingEnvironment(
        results=[
            ExecResult(
                return_code=3,
                stdout='{"type":"thread.started","thread_id":"thr-2"}\n',
                stderr="codex failed",
            )
        ],
    )
    runner = _make_runner(
        env,
        cli_flags_arg='-c model_reasoning_effort=low',
    )
    options = RunnerOptions(
        model="gpt-5.4-mini",
        reasoning_effort="high",
        output_schema_path="/tmp/schema.json",
        dangerous_yolo=True,
        full_auto=True,
        skip_git_repo_check=True,
    )

    result = runner.run_exec(
        prompt="Resume work",
        options=options,
        run_label="engineer-r2",
        resume_thread_id="thr-prev",
    )

    expected_command = shlex.join(
        [
            "codex",
            "exec",
            "resume",
            "--json",
            "-m",
            "gpt-5.4-mini",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-c",
            "model_reasoning_effort=low",
            "thr-prev",
            "-",
        ]
    )

    assert env.calls == [
        {
            "command": f"printf %s {shlex.quote('Resume work')} | {expected_command}",
            "cwd": "/workspace",
            "env": {"OPENAI_API_KEY": "test-key"},
            "timeout_sec": 9,
            "user": "root",
        }
    ]
    assert result.exit_code == 3
    assert result.agent_messages == ["codex failed"]
    assert result.thread_id == "thr-2"
    assert result.fatal_error == "container exec failed with exit code 3"
