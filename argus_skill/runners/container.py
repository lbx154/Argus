"""Compatibility wrapper for the legacy container runner API."""
from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from typing import Callable

from ..core.models import RunnerOptions, RunnerResult

log = logging.getLogger(__name__)


@dataclass
class ContainerCodexRunnerConfig:
    model: str = ""
    cli_flags_arg: str = ""
    skill_text: str = ""
    skill_name: str | None = None
    round_timeout: int = 1800
    output_filename: str = "argus-skill-codex.txt"
    agent_dir_posix: str = "/agent"
    verify_cmd: str = ""
    verify_timeout: int = 0
    state_probe_cmd: str = ""
    tests_src_dir: str = ""
    verify_advisory: bool = False
    augmented_max_chars: int = 24 * 1024


class ContainerCodexRunner:
    def __init__(
        self,
        *,
        environment,
        env_vars: dict[str, str] | None,
        config: ContainerCodexRunnerConfig,
        loop,
        codex_run_result_cls,
        agent_message_parser: Callable[[str], list[str]] | None,
        thread_id_extractor: Callable[[str], str | None] | None,
        logger: logging.Logger | None = None,
        event_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self.environment = environment
        self.env_vars = dict(env_vars or {})
        self.config = config
        self.loop = loop
        self.codex_run_result_cls = codex_run_result_cls
        self.agent_message_parser = agent_message_parser
        self.thread_id_extractor = thread_id_extractor
        self.logger = logger or log
        self.event_sink = event_sink

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        return asyncio.run(
            self._run_exec_async(
                prompt=prompt,
                options=options,
                run_label=run_label,
                resume_thread_id=resume_thread_id,
            )
        )

    async def _run_exec_async(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        full_prompt = self._compose_prompt(prompt)
        command = self._build_command(options=options, resume_thread_id=resume_thread_id)
        shell_cmd = f"printf %s {shlex.quote(full_prompt)} | {shlex.join(command)}"
        result = await self.environment.exec(
            shell_cmd,
            cwd=getattr(self.environment, "default_workdir", None),
            env=self.env_vars or None,
            timeout_sec=max(1, self.config.round_timeout),
            user="root",
        )
        stdout = str(getattr(result, "stdout", "") or "")
        stderr = str(getattr(result, "stderr", "") or "")
        exit_code = int(getattr(result, "return_code", 0) or 0)
        messages = self.agent_message_parser(stdout) if self.agent_message_parser else [stdout.strip()]
        messages = [msg for msg in messages if msg.strip()]
        thread_id = self.thread_id_extractor(stdout) if self.thread_id_extractor else None
        if exit_code and not messages and stderr.strip():
            messages = [stderr.strip()]
        return RunnerResult(
            exit_code=exit_code,
            agent_messages=messages,
            stdout_lines=stdout.splitlines(),
            stderr_lines=stderr.splitlines(),
            thread_id=thread_id,
            fatal_error=None if exit_code == 0 else f"container exec failed with exit code {exit_code}",
        )

    def _compose_prompt(self, prompt: str) -> str:
        skill = self.config.skill_text.strip()
        if not skill:
            return prompt
        return f"{skill}\n\n{prompt}"

    def _build_command(
        self,
        *,
        options: RunnerOptions,
        resume_thread_id: str | None,
    ) -> list[str]:
        command = ["codex", "exec"]
        if resume_thread_id:
            command.append("resume")
        command.append("--json")

        model = options.model or self.config.model
        if model:
            command.extend(["-m", model])

        extra_args = shlex.split(self.config.cli_flags_arg) if self.config.cli_flags_arg else []
        if options.reasoning_effort and not any("model_reasoning_effort" in arg for arg in extra_args):
            extra_args.extend(["-c", f"model_reasoning_effort={options.reasoning_effort}"])
        if options.dangerous_yolo:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif options.full_auto:
            command.append("--full-auto")
        if options.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if options.output_schema_path and not resume_thread_id:
            command.extend(["--output-schema", options.output_schema_path])
        if extra_args:
            command.extend(extra_args)
        if options.extra_args:
            command.extend(options.extra_args)
        if resume_thread_id:
            command.append(resume_thread_id)
        command.append("-")
        return command


class ContainerReviewerBackend(ContainerCodexRunner):
    pass
