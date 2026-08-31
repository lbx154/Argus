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

import logging
import os
from pathlib import Path

from .runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COPILOT,
    BACKEND_DSH,
    BACKEND_GROK,
    BACKEND_OPENCODE,
    BACKEND_PI,
    BACKEND_QODER,
    CLAUDE_FAMILY,
    RunnerBackend,
)

log = logging.getLogger(__name__)

# Shared with ``_prompt_delivery.py`` (the read-only OpenCode agent config
# injected into the child env must name the same agent this builder selects
# via ``--agent``).
_OPENCODE_READ_ONLY_AGENT = "argus-read-only"
_OPENCODE_FULL_ACCESS_AGENT = "argus-full-access"
_OPENCODE_NO_TOOLS_AGENT = "argus-no-tools"
_OPENCODE_OX_ALPHA_MODEL = "opencode/x-preview-f-free"
_OPENCODE_OX_ALPHA_WARNED = False


def _pi_session_dir() -> str:
    """Keep Argus-owned Pi sessions out of the operator's interactive history."""
    configured = str(os.environ.get("ARGUS_SKILL_PI_SESSION_DIR") or "").strip()
    if configured:
        return str(Path(configured).expanduser().resolve())
    from ..core.paths import global_root

    return str((global_root() / "pi-sessions").resolve())


def _pi_model(model: str) -> str:
    """Qualify a bare Pi model id ONLY when the operator named a provider.

    Pi is a provider-agnostic front: ``--model`` resolves against whichever
    catalogs are authenticated (DeepSeek, Anthropic, Azure, a local vLLM, a
    Copilot proxy). This used to force a ``github-copilot/`` prefix, so every
    Pi deployment that was NOT fronting Copilot failed on every single call
    with ``No API key found for github-copilot`` — while ``pi --list-models``,
    and therefore ``argus --doctor``, still reported the backend healthy.

    Passing a bare id through is both correct and provider-neutral. The knob
    stays for the one case Pi cannot resolve alone: two authenticated catalogs
    carrying the same id (``claude-opus-5`` lives on both ``anthropic`` and a
    Copilot proxy). ``argus --doctor`` names that collision — see
    ``core.backend_readiness``.

    中文：Pi 的 provider 由运维认证决定，Argus 不再替它假设 ``github-copilot``；
    仅当运维显式配置 ``ARGUS_SKILL_PI_PROVIDER`` 时才加前缀。
    """
    value = str(model or "").strip()
    if not value or "/" in value:
        return value
    provider = _configured_provider("ARGUS_SKILL_PI_PROVIDER")
    return f"{provider}/{value}" if provider else value


def _opencode_model(model: str) -> str:
    """Qualify a bare OpenCode model id, or return ``""`` when it must be dropped.

    ``opencode run --model`` only accepts ``provider/id``, so a bare id cannot
    be forwarded at all. Dropping it SILENTLY (the previous behaviour) made
    every Argus model knob a no-op on this backend: OpenCode ran its own
    default and nothing distinguished that from Argus honouring the setting.
    Qualify when the operator named a provider; otherwise still drop, but say
    so once.
    """
    value = str(model or "").strip()
    if not value:
        return ""
    provider_part, separator, model_id = value.partition("/")
    if separator:
        # Already qualified — forward verbatim. A malformed half ("a/" or
        # "/b") is not a usable selector, so it falls through to the warning.
        if provider_part and model_id:
            return value
    else:
        provider = _configured_provider("ARGUS_SKILL_OPENCODE_PROVIDER")
        if provider:
            return f"{provider}/{value}"
    _warn_unqualified_model_once(BACKEND_OPENCODE, value)
    return ""


def _opencode_variant(model: str, reasoning_effort: str | None) -> str:
    """Select a working OpenCode variant for the resolved model."""
    if model != _OPENCODE_OX_ALPHA_MODEL:
        return str(reasoning_effort or "").strip()
    global _OPENCODE_OX_ALPHA_WARNED
    requested = str(reasoning_effort or "").strip()
    if requested not in {"", "low"} and not _OPENCODE_OX_ALPHA_WARNED:
        _OPENCODE_OX_ALPHA_WARNED = True
        log.warning(
            "OpenCode Ox Alpha %r is unstable for tool turns; using variant "
            "'low' instead",
            requested,
        )
    return "low"


def _configured_provider(knob: str) -> str:
    """Operator-configured provider prefix for a backend, or ``""`` if unset."""
    from ..core.knobs import resolve_knob

    return resolve_knob(knob, "").value.strip().strip("/")


# Command construction runs once per provider call, so an unqualified model id
# must not narrate itself into every log line. Warn once per (backend, model).
_UNQUALIFIED_MODEL_WARNED: set[tuple[str, str]] = set()


def _warn_unqualified_model_once(backend: str, model: str) -> None:
    key = (backend, model)
    if key in _UNQUALIFIED_MODEL_WARNED:
        return
    _UNQUALIFIED_MODEL_WARNED.add(key)
    log.warning(
        "%s cannot use the configured model %r: its `--model` requires a "
        "provider-qualified id. Set ARGUS_SKILL_%s_PROVIDER, or configure the "
        "model as 'provider/%s'. Until then %s runs its OWN default model and "
        "the Argus model setting has no effect.",
        backend,
        model,
        backend.upper(),
        model,
        backend,
    )


def reset_unqualified_model_warnings() -> None:
    """Test seam: forget which unqualified-model warnings were already issued."""
    _UNQUALIFIED_MODEL_WARNED.clear()


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
        if self.backend in CLAUDE_FAMILY:
            # qoder is a Claude Code fork; it takes the same headless argv.
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
        if self.backend == BACKEND_GROK:
            return self._build_grok_command(
                resume_thread_id=resume_thread_id, options=options
            )
        if self.backend == BACKEND_DSH:
            return self._build_dsh_command(
                resume_thread_id=resume_thread_id, options=options
            )
        return self._build_codex_command(resume_thread_id=resume_thread_id, options=options)

    def _apply_sandbox_policy(self, options):
        """Apply the operator's single global access policy."""
        import dataclasses

        safe_mode = options.force_safe_mode or (
            os.environ.get("ARGUS_SKILL_SAFE_MODE", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if not safe_mode:
            return dataclasses.replace(
                options,
                sandbox_mode=None,
                isolate_workdir=False,
                dangerous_yolo=True,
                full_auto=False,
            )
        if self.backend in (
            BACKEND_CLAUDE,
            BACKEND_COPILOT,
            BACKEND_GROK,
            BACKEND_OPENCODE,
            BACKEND_PI,
            BACKEND_QODER,
            BACKEND_DSH,
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
        # Argus owns desktop/background completion notifications. Inheriting a
        # Codex Desktop ``notify`` hook makes every non-interactive turn wait for
        # an unrelated GUI helper after the authoritative ``turn.completed``
        # event (10.3–10.6 seconds per call in the packaged-host trace). Override
        # it only for this child invocation; the operator's config.toml is never
        # modified, and later explicit extra args may still opt back in.
        command.extend(["-c", "notify=[]"])
        if options.disable_tools:
            # Stateless Manager/Planner control calls need the operator's model
            # provider and auth, but not interactive plugins, MCP servers, JS
            # REPL startup or project exec-policy rules. Keeping the base config
            # while overriding only these tool surfaces preserves custom
            # providers and cuts several seconds of Codex startup per control
            # turn (measured doctor startup: ~12s -> ~2.5s on this host).
            command.extend([
                "--ignore-rules",
                "-c",
                "mcp_servers={}",
                "-c",
                "plugins={}",
                "-c",
                "features.js_repl=false",
                "-c",
                'web_search="disabled"',
            ])
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
        command = [self.agent_bin, "-p"]
        # qodercli is a Claude Code fork that shares claude's headless surface
        # but differs on three flags: it REJECTS --verbose, spells reasoning
        # effort as --reasoning-effort (claude uses --effort), and takes
        # snake_case permission modes (bypass_permissions / accept_edits).
        is_qoder = self.backend == BACKEND_QODER
        if not is_qoder:
            command.append("--verbose")
        command.extend(["--output-format", "stream-json"])
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            # Both Claude and Qoder accept the full configured effort range.
            command.extend(
                ["--reasoning-effort" if is_qoder else "--effort",
                 options.reasoning_effort]
            )
        if options.disable_tools:
            command.extend(["--tools", ""])
        elif options.sandbox_mode == "read-only":
            command.extend(["--tools", "Read,Glob,Grep"])
        elif options.dangerous_yolo:
            command.extend([
                "--permission-mode",
                "bypass_permissions" if is_qoder else "bypassPermissions",
            ])
        elif options.full_auto:
            command.extend([
                "--permission-mode",
                "accept_edits" if is_qoder else "acceptEdits",
            ])
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
        if options.disable_tools:
            command.extend(["--available-tools=", "--deny-tool=*"])
        elif options.sandbox_mode == "read-only":
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
        model = _opencode_model(options.model)
        if model:
            command.extend(["--model", model])
        variant = _opencode_variant(model, options.reasoning_effort)
        if variant:
            command.extend(["--variant", variant])
        if options.working_dir:
            command.extend(["--dir", options.working_dir])
        if options.disable_tools:
            command.extend(["--agent", _OPENCODE_NO_TOOLS_AGENT])
        elif options.sandbox_mode == "read-only":
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
            # Disable ambient resources. Explicit ``--skill`` paths below remain
            # additive, so only the current Argus role libraries are visible.
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
        ])
        for path in options.skill_paths or []:
            command.extend(["--skill", path])
        if options.model:
            command.extend(["--model", _pi_model(options.model)])
        if options.reasoning_effort:
            command.extend(["--thinking", options.reasoning_effort])
        if options.disable_tools:
            command.append("--no-tools")
        elif options.sandbox_mode == "read-only":
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

    def _build_grok_command(
        self,
        *,
        resume_thread_id: str | None,
        options,
    ) -> list[str]:
        """Build a Grok Build headless turn using its Messages-compatible stream."""
        if options.isolate_workdir:
            raise ValueError(
                "isolated Grok calls are not supported because Grok authentication "
                "and session state are intentionally hidden by worktree isolation"
            )
        command = [
            self.agent_bin,
            "--no-auto-update",
            "--output-format",
            "streaming-messages-json",
            "--verbatim",
        ]
        if options.working_dir:
            command.extend(["--cwd", options.working_dir])
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            command.extend(["--reasoning-effort", options.reasoning_effort])
        if options.disable_tools:
            command.extend(["--tools", ""])
        elif options.sandbox_mode == "read-only":
            command.extend(["--tools", "read_file,grep,list_dir"])
        elif options.dangerous_yolo or options.full_auto:
            command.append("--yolo")
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args,
                backend=BACKEND_GROK,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--resume", resume_thread_id])
        # PromptDeliveryMixin appends --prompt-file with a private temporary file.
        return command


    def _build_dsh_command(
        self,
        *,
        resume_thread_id: str | None,
        options,
    ) -> list[str]:
        """Build a DeepSeek Harness one-shot turn.

        dsh has no stream-json surface, no session resume, and no model flag:
        its headless profile runs one full agent turn and prints only the
        final assistant text (exit 0 on completion; see
        ``_finalize_turn_result`` in ``_run_exec.py``). The per-role model
        rides in through the env-driven overlay attached via ``--patch``
        (``ARGUS_DSH_PROVIDER`` / ``ARGUS_DSH_MODEL``) and the role's access
        policy through ``DSH_PERMISSION_MODE`` (see ``_apply_dsh_env`` in
        ``_prompt_delivery.py``). ``resume_thread_id`` is intentionally
        ignored: the headless runner creates a fresh session per boot, and
        round context travels in the prompt instead. The task positional is
        appended by ``_prepare_prompt_delivery``.
        """
        command = [
            self.agent_bin,
            "--profile",
            "headless",
            "--patch",
            _dsh_overlay_patch_path(),
        ]
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args,
                backend=BACKEND_DSH,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        return command

def _dsh_overlay_patch_path() -> str:
    """Path of the env-driven overlay attached to every dsh headless boot.

    The overlay re-targets the deployment default model from
    ``ARGUS_DSH_PROVIDER`` / ``ARGUS_DSH_MODEL`` and pins the approval
    policy; see the file itself for the evaluated rows.
    """
    return str(Path(__file__).parent / "_dsh_overlay.patch.yml")
