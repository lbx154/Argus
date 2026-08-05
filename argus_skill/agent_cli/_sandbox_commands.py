"""Per-backend CLI argv construction and the codex sandbox policy chokepoint.

Everything here is reached only through :meth:`AgentCliRunner._build_command`
and :meth:`AgentCliRunner._apply_sandbox_policy` — the single gated place that
may emit codex's ``--dangerously-bypass-approvals-and-sandbox`` fallback (see
``tests/test_engineer_sandbox.py::test_no_raw_codex_spawn_bypasses_gate_anywhere``,
whose allowlist covers this file alongside ``agent_cli_runner.py`` and
``core/sandbox.py``). Extracted verbatim from ``agent_cli_runner.py`` — no argv,
flag ordering, or sandbox-mode semantics changed.
"""
from __future__ import annotations

import os
from pathlib import Path

from .runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COPILOT,
    BACKEND_OPENCODE,
    BACKEND_PI,
    RunnerBackend,
)

# Shared with ``_prompt_delivery.py`` (the read-only OpenCode agent config
# injected into the child env must name the same agent this builder selects
# via ``--agent``).
_OPENCODE_READ_ONLY_AGENT = "argus-read-only"
_OPENCODE_FULL_ACCESS_AGENT = "argus-full-access"


def _pi_session_dir() -> str:
    """Keep Argus-owned Pi sessions out of the operator's interactive history."""
    configured = str(os.environ.get("ARGUS_SKILL_PI_SESSION_DIR") or "").strip()
    if configured:
        return str(Path(configured).expanduser().resolve())
    from ..core.paths import global_root

    return str((global_root() / "pi-sessions").resolve())


_READ_ONLY_FLAG_SWITCHES = frozenset({
    "--allow-all",
    "--allow-all-paths",
    "--allow-all-tools",
    "--allowed-tools",
    "--allowedTools",
    "--allow-tool",
    "--available-tools",
    "--autopilot",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--dangerously-skip-permissions",
    "--auto",
    "--full-auto",
    "--permission-mode",
    "--sandbox",
    "--tools",
    "--yolo",
    "--agent",
    "--approve",
    "-a",
    "--extension",
    "-e",
    "--skill",
    "--dir",
    "-C",
    "-s",
    "--add-dir",
    "--cd",
})
_READ_ONLY_VALUE_SWITCHES = frozenset({
    "--allow-tool",
    "--allowed-tools",
    "--allowedTools",
    "--available-tools",
    "--agent",
    "--extension",
    "-e",
    "--skill",
    "--dir",
    "--permission-mode",
    "--sandbox",
    "--tools",
    "-C",
    "-s",
    "--add-dir",
    "--cd",
})


def _read_only_extra_args(args: list[str], *, backend: RunnerBackend) -> list[str]:
    """Drop any extra argument capable of broadening a read-only Manager call."""
    cleaned: list[str] = []
    index = 0
    while index < len(args):
        value = str(args[index] or "")
        config_switches = (
            {"-c", "--config"} if backend == BACKEND_CODEX else {"--config"}
        )
        if value in config_switches and index + 1 < len(args):
            payload = str(args[index + 1] or "")
            key = payload.partition("=")[0].strip().casefold()
            if key.startswith((
                "approval", "permission", "sandbox", "shell_environment", "tools",
            )):
                index += 2
                continue
            cleaned.extend([value, payload])
            index += 2
            continue
        if value.startswith("--config="):
            key = value.partition("=")[2].partition("=")[0].strip().casefold()
            if key.startswith((
                "approval", "permission", "sandbox", "shell_environment", "tools",
            )):
                index += 1
                continue
        if backend == BACKEND_CODEX and value.startswith("-c") and value != "-c":
            payload = value[2:].lstrip("=")
            key = payload.partition("=")[0].strip().casefold()
            if key.startswith((
                "approval", "permission", "sandbox", "shell_environment", "tools",
            )):
                index += 1
                continue
        if value.startswith(("-C", "-s")) and value not in {"-C", "-s"}:
            index += 1
            continue
        flag = value.partition("=")[0]
        if flag in _READ_ONLY_FLAG_SWITCHES:
            index += 2 if flag in _READ_ONLY_VALUE_SWITCHES and "=" not in value else 1
            continue
        cleaned.append(value)
        index += 1
    return cleaned


class CommandBuilderMixin:
    """Builds the per-backend argv and applies the codex sandbox gate."""

    def _build_command(
        self, *, resume_thread_id: str | None, options
    ) -> list[str]:
        if self.backend == BACKEND_CLAUDE:
            return self._build_claude_command(resume_thread_id=resume_thread_id, options=options)
        if self.backend == BACKEND_COPILOT:
            return self._build_copilot_command(
                resume_thread_id=resume_thread_id, options=options
            )
        if self.backend == BACKEND_OPENCODE:
            return self._build_opencode_command(
                resume_thread_id=resume_thread_id, options=options
            )
        if self.backend == BACKEND_PI:
            return self._build_pi_command(
                resume_thread_id=resume_thread_id, options=options
            )
        return self._build_codex_command(resume_thread_id=resume_thread_id, options=options)

    def _apply_sandbox_policy(self, options):
        """Gated, default-OFF containment chokepoint for codex builder roles.

        When ``ARGUS_SKILL_ENGINEER_SANDBOX`` is set, convert EVERY codex role
        into ``-s <mode>`` confined to its workdir plus the writable allowlist,
        clear the dangerous flags, and pin a ``-C`` (falling closed to a private
        scratch dir when the caller passed no workdir, so the writable workspace
        is NEVER the inherited cwd ``/``). This single chokepoint covers every
        AgentCliRunner role (engineer / reviewer / planner / manager classify /
        plan-mode), including ones that today fall through to codex's config
        default (danger-full-access on the box) because they set neither
        ``dangerous_yolo`` nor ``full_auto``. No-op when the gate is off, when an
        explicit ``sandbox_mode`` was already chosen, or for non-codex backends —
        so the default path stays byte-for-byte unchanged.
        """
        if self.backend in (
            BACKEND_CLAUDE,
            BACKEND_COPILOT,
            BACKEND_OPENCODE,
            BACKEND_PI,
        ):
            return options
        if options.sandbox_mode is not None:
            return options
        from ..core.sandbox import (
            engineer_sandbox_mode,
            fail_closed_workdir,
            writable_roots,
        )

        mode = engineer_sandbox_mode()
        if mode is None:
            # Gate OFF: byte-for-byte legacy behaviour for EVERY role.
            return options
        import dataclasses

        merged = list(dict.fromkeys([*(options.add_dirs or []), *writable_roots()]))
        # Fail closed: a sandboxed role with no -C would root its writable
        # workspace at the inherited cwd (the daemon's "/"). Pin a contained dir.
        working_dir = options.working_dir or fail_closed_workdir()
        return dataclasses.replace(
            options,
            sandbox_mode=mode,
            dangerous_yolo=False,
            full_auto=False,
            add_dirs=merged,
            working_dir=working_dir,
        )

    def _build_codex_command(
        self, *, resume_thread_id: str | None, options
    ) -> list[str]:
        command = [self.agent_bin, "exec"]
        if resume_thread_id:
            command.append("resume")
        command.append("--json")
        if options.model:
            command.extend(["-m", options.model])
        if options.reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{options.reasoning_effort}"'])
            # Stream a reasoning summary DURING the turn so the operator sees the
            # model is actively working instead of a silent "no stream output"
            # gap — gpt-5.x at high effort reasons server-side for tens of
            # seconds emitting nothing otherwise, which reads like a hang. "auto"
            # lets the model size the summary; ARGUS_SKILL_REASONING_SUMMARY=none
            # opts back out.
            summary = (os.environ.get("ARGUS_SKILL_REASONING_SUMMARY") or "auto").strip()
            if summary.lower() not in {"none", "off", "0", "false", ""}:
                command.extend(["-c", f'model_reasoning_summary="{summary}"'])
        if options.sandbox_mode and resume_thread_id:
            command.extend(["-c", f'sandbox_mode="{options.sandbox_mode}"'])
        elif options.sandbox_mode:
            # Sandboxed role: confine writes to the workspace (-C) plus the
            # explicit --add-dir allowlist; keep network on for research. This
            # replaces the dangerous bypass so the engineer cannot write the
            # package source / edit its own gate. The writable allowlist is the
            # caller's responsibility (it MUST exclude ~/.argus-skill, the
            # package, and ~/.codex).
            command.extend(["-s", options.sandbox_mode])
            # Always pin -C. Emitting -s workspace-write with no -C roots the
            # writable workspace at the inherited cwd (the daemon's "/"), which
            # would expose the whole FS — fall closed to a private scratch dir.
            if options.working_dir:
                command.extend(["-C", options.working_dir])
            else:
                from ..core.sandbox import fail_closed_workdir

                command.extend(["-C", fail_closed_workdir()])
            for extra_dir in options.add_dirs or []:
                command.extend(["--add-dir", extra_dir])
            if options.sandbox_mode == "workspace-write":
                # workspace-write defaults network OFF; force it on explicitly
                # rather than relying on the agent-writable config.toml.
                command.extend(["-c", "sandbox_workspace_write.network_access=true"])
        elif options.dangerous_yolo:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif options.full_auto:
            command.append("--full-auto")
        if options.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if getattr(options, "live_search", False):
            # codex exec enables live web search via CONFIG, not a flag (there is
            # no `--search` on `exec`). Valid ``web_search`` variants are
            # disabled/cached/indexed/live; force ``live`` so idea discovery does
            # real live searches instead of the cached default.
            command.extend(["-c", 'web_search="live"'])
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args, backend=BACKEND_CODEX,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.append(resume_thread_id)
        # Always stream the prompt through stdin so multiline prompts survive
        # Windows `.cmd` wrappers and do not appear in process lists.
        command.append("-")
        return command

    def _build_claude_command(
        self, *, resume_thread_id: str | None, options
    ) -> list[str]:
        command = [
            self.agent_bin,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
        ]
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            effort = (
                "high"
                if options.reasoning_effort == "xhigh"
                else options.reasoning_effort
            )
            command.extend(["--effort", effort])
        if options.sandbox_mode == "read-only":
            command.extend(["--tools", "Read,Glob,Grep"])
        elif options.dangerous_yolo:
            command.extend(["--permission-mode", "bypassPermissions"])
        elif options.full_auto:
            command.extend(["--permission-mode", "acceptEdits"])
        # --add-dir
        if options.add_dirs:
            for dir_path in options.add_dirs:
                command.extend(["--add-dir", dir_path])

        # --plugin-dir
        if options.plugin_dirs:
            for dir_path in options.plugin_dirs:
                command.extend(["--plugin-dir", dir_path])

        # --file
        if options.file_specs:
            for file_spec in options.file_specs:
                command.extend(["--file", file_spec])

        # --worktree
        if options.worktree_name:
            command.extend(["--worktree", options.worktree_name])

        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args, backend=BACKEND_CLAUDE,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--resume", resume_thread_id])
        return command

    def _build_copilot_command(
        self,
        *,
        resume_thread_id: str | None,
        options,
    ) -> list[str]:
        command = [
            self.agent_bin,
            "--output-format",
            "json",
            "--stream",
            "on",
            "--no-auto-update",
            "--no-ask-user",
        ]
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            command.extend(["--reasoning-effort", options.reasoning_effort])
        if options.isolate_workdir:
            command.extend([
                "--no-custom-instructions",
                "--disable-builtin-mcps",
            ])
        if options.sandbox_mode == "read-only":
            command.extend([
                "--available-tools", "view,rg,glob",
                "--allow-tool", "view,rg,glob",
            ])
        elif options.dangerous_yolo:
            command.append("--yolo")
        else:
            # Copilot prompt mode requires automatic tool approval in non-interactive runs.
            command.append("--allow-all-tools")
        if options.add_dirs:
            for dir_path in options.add_dirs:
                command.extend(["--add-dir", dir_path])
        if options.plugin_dirs:
            for dir_path in options.plugin_dirs:
                command.extend(["--plugin-dir", dir_path])
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args, backend=BACKEND_COPILOT,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--resume", resume_thread_id])
        # Copilot CLI (@github/copilot) reads the prompt from STDIN when no
        # ``-p/--prompt <text>`` argv is given (non-interactive because stdin is
        # not a TTY). We deliberately DO NOT pass the prompt via argv: a large
        # reviewer/planner prompt (full-pipeline checklist + embedded schema)
        # blows past the kernel per-arg limit (MAX_ARG_STRLEN, 128 KiB) and
        # ``execve`` fails with OSError: [Errno 7] Argument list too long,
        # crashing the reviewer every round. Streaming through stdin (same as
        # codex/claude) has no such limit. The schema contract that used to be
        # appended here now rides along in the stdin prompt via
        # ``_effective_prompt`` so the reviewer/planner verdict still parses.
        # copilot CLI 在不传 ``-p`` 时从 stdin 读 prompt；把大 prompt 放进 argv 会
        # 超过内核单参数上限触发 E2BIG（Errno 7）导致 reviewer 每轮崩溃，故与
        # codex/claude 一样统一走 stdin；schema 契约改由 ``_effective_prompt``
        # 拼进 stdin prompt。
        return command

    def _build_opencode_command(
        self,
        *,
        resume_thread_id: str | None,
        options,
    ) -> list[str]:
        command = [self.agent_bin, "run", "--format", "json"]
        model = str(options.model or "").strip()
        provider, separator, model_id = model.partition("/")
        if separator and provider and model_id:
            command.extend(["--model", model])
        if options.reasoning_effort:
            command.extend(["--variant", options.reasoning_effort])
        if options.working_dir:
            command.extend(["--dir", options.working_dir])
        if options.sandbox_mode == "read-only":
            command.extend(["--agent", _OPENCODE_READ_ONLY_AGENT])
        elif options.dangerous_yolo or options.full_auto:
            command.extend(["--agent", _OPENCODE_FULL_ACCESS_AGENT])
        if options.file_specs:
            for file_spec in options.file_specs:
                command.extend(["--file", file_spec])
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args, backend=BACKEND_OPENCODE,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--session", resume_thread_id])
        # With no positional message, ``opencode run`` reads the prompt from
        # stdin. This avoids exposing prompts in argv and supports large schemas.
        return command

    def _build_pi_command(
        self,
        *,
        resume_thread_id: str | None,
        options,
    ) -> list[str]:
        """Build a deterministic Pi JSON-stream turn with stdin prompt delivery."""
        command = [
            self.agent_bin,
            "--mode",
            "json",
        ]
        if options.isolate_workdir:
            if resume_thread_id:
                raise ValueError("isolated Pi calls cannot resume a persisted session")
            command.append("--no-session")
        else:
            command.extend(["--session-dir", _pi_session_dir()])
        command.extend([
            # Argus supplies the complete role prompt and owns tool policy. Do
            # not let interactive Pi packages or project context alter it.
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
        ])
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            command.extend(["--thinking", options.reasoning_effort])
        if options.sandbox_mode == "read-only":
            command.extend(["--tools", "read,grep,find,ls"])
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args,
                backend=BACKEND_PI,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--session", resume_thread_id])
        # Pi reads non-TTY stdin into the initial message in JSON mode. Keeping
        # the prompt out of argv avoids E2BIG and process-list disclosure.
        return command
