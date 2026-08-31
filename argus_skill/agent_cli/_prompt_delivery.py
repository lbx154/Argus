"""Safe prompt delivery and sandboxed/isolated child environments."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..core.sandbox import sandboxed_child_env
from ._sandbox_commands import (
    _OPENCODE_FULL_ACCESS_AGENT,
    _OPENCODE_NO_TOOLS_AGENT,
    _OPENCODE_READ_ONLY_AGENT,
)
from .copilot_home import apply_copilot_home
from .runner_backend import (
    BACKEND_COPILOT,
    BACKEND_DSH,
    BACKEND_GROK,
    BACKEND_OPENCODE,
    BACKEND_PI,
    CLAUDE_FAMILY,
    runner_child_environment,
)

_OPENCODE_CONFIG_CONTENT_ENV = "OPENCODE_CONFIG_CONTENT"


def _apply_pi_automation_env(env: dict[str, str]) -> dict[str, str]:
    """Disable per-turn Pi update pings without changing provider traffic."""
    env.setdefault("PI_SKIP_VERSION_CHECK", "1")
    env.setdefault("PI_TELEMETRY", "0")
    return env


def _opencode_agent_env(
    *,
    agent_name: str,
    description: str,
    permission: dict[str, str],
) -> dict[str, str]:
    env = sandboxed_child_env()
    raw = str(env.get(_OPENCODE_CONFIG_CONTENT_ENV) or "").strip()
    if raw:
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{_OPENCODE_CONFIG_CONTENT_ENV} must be valid JSON for "
                "Argus OpenCode calls"
            ) from exc
        if not isinstance(config, dict):
            raise ValueError(
                f"{_OPENCODE_CONFIG_CONTENT_ENV} must contain a JSON object"
            )
    else:
        config = {}

    configured_agents = config.get("agent")
    if configured_agents is None:
        agents: dict[str, object] = {}
    elif isinstance(configured_agents, dict):
        agents = dict(configured_agents)
    else:
        raise ValueError(
            f"{_OPENCODE_CONFIG_CONTENT_ENV}.agent must contain a JSON object"
        )
    agents[agent_name] = {
        "description": description,
        "mode": "primary",
        "permission": permission,
    }
    config["agent"] = agents
    env[_OPENCODE_CONFIG_CONTENT_ENV] = json.dumps(
        config,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return env


def _opencode_read_only_env() -> dict[str, str]:
    """Inject a final-precedence OpenCode agent that cannot invoke write tools."""
    return _opencode_agent_env(
        agent_name=_OPENCODE_READ_ONLY_AGENT,
        description="Argus read-only inspection agent.",
        permission={
            "*": "deny",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
        },
    )


def _opencode_no_tools_env() -> dict[str, str]:
    return _opencode_agent_env(
        agent_name=_OPENCODE_NO_TOOLS_AGENT,
        description="Argus restricted noninteractive agent.",
        permission={"*": "deny"},
    )


def _opencode_full_access_env() -> dict[str, str]:
    """Inject a noninteractive OpenCode agent with explicit tool permission."""
    return _opencode_agent_env(
        agent_name=_OPENCODE_FULL_ACCESS_AGENT,
        description="Argus noninteractive execution agent.",
        permission={"*": "allow"},
    )


class PromptDeliveryMixin:
    """Deliver large role prompts without exposing them in process arguments."""

    @staticmethod
    def _write_prompt(*, process: subprocess.Popen[str], prompt: str) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.write(prompt)
            if not prompt.endswith("\n"):
                process.stdin.write("\n")
        except BrokenPipeError:
            return
        finally:
            try:
                process.stdin.close()
            except OSError:
                return

    @staticmethod
    def _close_stdin(process: subprocess.Popen[str]) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.close()
        except OSError:
            return

    def _prepare_prompt_delivery(
        self,
        command: list[str],
        prompt: str,
        *,
        working_dir: str | None = None,
    ) -> tuple[list[str], str | None, Path | None]:
        if self.backend == BACKEND_DSH:
            # dsh's headless profile reads its one-shot task from the argv
            # positional alone (`dsh --profile headless "<task>"`); it has no
            # stdin or --prompt-file form. Keep the prompt in argv up to the
            # same safety bound as claude's positional delivery; oversized
            # prompts are written into the child's working directory (so the
            # agent can read them) and the task becomes a short directive.
            prepared = list(command)
            if len(prompt.encode("utf-8")) <= _DSH_ARGV_PROMPT_LIMIT_BYTES:
                prepared.append(prompt)
                return prepared, None, None
            target_dir = Path(working_dir) if working_dir else Path.cwd()
            target_dir.mkdir(parents=True, exist_ok=True)
            fd, raw_path = tempfile.mkstemp(
                prefix=".argus-dsh-prompt-",
                suffix=".md",
                dir=str(target_dir),
                text=True,
            )
            prompt_path = Path(raw_path)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                    stream.write(prompt)
            except BaseException:
                prompt_path.unlink(missing_ok=True)
                raise
            prepared.append(
                "Execute the mission specified in the file "
                f"{prompt_path} (absolute path, inside your workspace). "
                "Act on it; do not just summarize it."
            )
            return prepared, None, prompt_path
        if self.backend == BACKEND_GROK:
            fd, raw_path = tempfile.mkstemp(
                prefix="argus-grok-prompt-",
                suffix=".txt",
                text=True,
            )
            prompt_path = Path(raw_path)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                    stream.write(prompt)
            except BaseException:
                prompt_path.unlink(missing_ok=True)
                raise
            prepared = list(command)
            prepared.extend(["--prompt-file", str(prompt_path)])
            return prepared, None, prompt_path
        if self.backend not in CLAUDE_FAMILY:
            return command, prompt, None
        prepared = list(command)
        if "--bare" in prepared:
            if "--input-format" not in prepared:
                prepared.extend(["--input-format", "stream-json"])
            session_id = ""
            if "--resume" in prepared:
                index = prepared.index("--resume") + 1
                if index < len(prepared):
                    session_id = prepared[index]
            payload = json.dumps({
                "type": "user",
                "session_id": session_id,
                "message": {"role": "user", "content": prompt},
                "parent_tool_use_id": None,
            }, ensure_ascii=False, separators=(",", ":"))
            return prepared, payload, None
        executable = str(prepared[0] if prepared else "").casefold()
        if executable.endswith((".cmd", ".bat")):
            return prepared, prompt, None
        max_bytes = 24_000 if os.name == "nt" else 100_000
        if len(prompt.encode("utf-8")) > max_bytes:
            raise RuntimeError(
                "Claude prompt exceeds the safe positional argument limit; "
                "configure API-key bare mode or reduce the prompt"
            )
        try:
            prompt_index = prepared.index("-p") + 1
        except ValueError:
            prompt_index = 1
        prepared.insert(prompt_index, prompt)
        return prepared, None, None

    def _child_env(
        self,
        options,
        *,
        executable: str | None = None,
    ) -> dict[str, str] | None:
        """Build a child environment and repair Node-backed npm wrappers.

        A desktop GUI can discover ``%APPDATA%\\npm\\codex.cmd`` even when it
        inherited a stale PATH without Node.  Keep existing backend-specific
        isolation intact, then give a verified Node runtime directory to that
        batch wrapper only when it needs one.
        """
        env: dict[str, str] | None = None
        if self.backend == BACKEND_OPENCODE:
            if options.disable_tools:
                env = _opencode_no_tools_env()
            elif options.sandbox_mode == "read-only":
                env = _opencode_read_only_env()
            elif options.dangerous_yolo or options.full_auto:
                env = _opencode_full_access_env()
        if env is None and not options.sandbox_mode and not options.isolate_workdir:
            # Normally the child inherits our environment untouched. Copilot is
            # the exception: left alone it writes every session, log and
            # store row into the operator's personal ~/.copilot, which on a
            # 7x24 host is tens of thousands of Argus sessions burying their own
            # history. Relocate the working state, change nothing else.
            if self.backend == BACKEND_COPILOT:
                env = apply_copilot_home(dict(os.environ))
            elif self.backend == BACKEND_PI:
                env = _apply_pi_automation_env(dict(os.environ))
            elif self.backend == BACKEND_DSH:
                env = _apply_dsh_env(dict(os.environ), options, self.agent_bin)
        if env is None and (options.sandbox_mode or options.isolate_workdir):
            env = sandboxed_child_env()
            if self.backend == BACKEND_COPILOT:
                apply_copilot_home(env)
            elif self.backend == BACKEND_PI:
                _apply_pi_automation_env(env)
            if self.backend == BACKEND_DSH:
                _apply_dsh_env(env, options, self.agent_bin)
        if env is not None and options.isolate_workdir:
            secret_markers = (
                "TOKEN",
                "SECRET",
                "PASSWORD",
                "CREDENTIAL",
                "API_KEY",
                "PRIVATE_KEY",
                "ACCESS_KEY",
                "COOKIE",
            )
            secret_prefixes = (
                "AWS_",
                "AZURE_",
                "GOOGLE_",
                "OPENAI_",
                "ANTHROPIC_",
                "HF_",
                "WANDB_",
                "KUBE_",
            )
            for key in list(env):
                upper = key.upper()
                if (
                    any(marker in upper for marker in secret_markers)
                    or upper.startswith(secret_prefixes)
                    or upper == "KUBECONFIG"
                ):
                    env.pop(key, None)
            env["GIT_CONFIG_GLOBAL"] = os.devnull
            env["GIT_CONFIG_NOSYSTEM"] = "1"
            env["GH_CONFIG_DIR"] = str(
                Path(tempfile.gettempdir()) / "argus-no-gh-auth"
            )
        repaired = runner_child_environment(
            executable or self.agent_bin,
            env=env,
        )
        return repaired if repaired is not None else env


_DSH_ARGV_PROMPT_LIMIT_BYTES = 90_000


def _apply_dsh_env(
    env: dict[str, str],
    options,
    agent_bin: str,
) -> dict[str, str]:
    """Prepare the child environment for one dsh headless boot.

    dsh resolves its model and access policy at load time from the env-driven
    overlay (``agent_cli/_dsh_overlay.patch.yml``), so the per-role model and
    sandbox mode ride in through environment variables rather than argv:

    * ``options.model`` maps to ``ARGUS_DSH_PROVIDER`` / ``ARGUS_DSH_MODEL``
      (a ``provider/model`` value splits; a bare id selects the provider the
      overlay defaults to);
    * ``sandbox_mode == "read-only"`` maps to ``DSH_PERMISSION_MODE=read-only``
      (dsh's sandbox denies writes outright), everything else runs with
      ``danger-full-access`` because an Argus turn has no approver to answer
      an "ask" prompt;
    * the directory holding the resolved dsh binary is prepended to PATH so
      nvm-installed Node resolves the ``#!/usr/bin/env node`` shebang even
      from a non-interactive daemon PATH.
    """
    model = str(getattr(options, "model", "") or "").strip()
    provider, sep, model_id = model.partition("/")
    if sep:
        if provider and model_id:
            env["ARGUS_DSH_PROVIDER"] = provider
            env["ARGUS_DSH_MODEL"] = model_id
    elif model:
        env["ARGUS_DSH_MODEL"] = model
    disable_tools = bool(getattr(options, "disable_tools", False))
    read_only = getattr(options, "sandbox_mode", None) == "read-only"
    if disable_tools:
        env["ARGUS_DSH_DISABLE_TOOLS"] = "1"
    if read_only:
        env["DSH_PERMISSION_MODE"] = "read-only"
    else:
        env["DSH_PERMISSION_MODE"] = "danger-full-access"
    agent_dir = str(Path(agent_bin).expanduser().resolve().parent)
    existing = env.get("PATH", "")
    if agent_dir and agent_dir not in existing.split(os.pathsep):
        env["PATH"] = agent_dir + os.pathsep + existing
    return env
