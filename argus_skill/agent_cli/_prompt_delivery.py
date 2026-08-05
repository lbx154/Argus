"""Safe prompt delivery and sandboxed/isolated child environments."""
from __future__ import annotations

import json
import os
import subprocess

from ..core.sandbox import sandboxed_child_env
from ._sandbox_commands import (
    _OPENCODE_FULL_ACCESS_AGENT,
    _OPENCODE_READ_ONLY_AGENT,
)
from .copilot_home import apply_copilot_home
from .runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_COPILOT,
    BACKEND_OPENCODE,
    BACKEND_PI,
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
    ) -> tuple[list[str], str | None]:
        if self.backend != BACKEND_CLAUDE:
            return command, prompt
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
            return prepared, payload
        executable = str(prepared[0] if prepared else "").casefold()
        if executable.endswith((".cmd", ".bat")):
            raise RuntimeError(
                "Claude requires a native executable for safe prompt delivery"
            )
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
        return prepared, None

    def _child_env(self, options) -> dict[str, str] | None:
        if not options.sandbox_mode and not options.isolate_workdir:
            # Normally the child inherits our environment untouched. Copilot is
            # the exception: left alone it writes every session, log and
            # store row into the operator's personal ~/.copilot, which on a
            # 7x24 host is tens of thousands of Argus sessions burying their own
            # history. Relocate the working state, change nothing else.
            if self.backend == BACKEND_COPILOT:
                return apply_copilot_home(dict(os.environ))
            if self.backend == BACKEND_PI:
                return _apply_pi_automation_env(dict(os.environ))
            if (
                self.backend == BACKEND_OPENCODE
                and (options.dangerous_yolo or options.full_auto)
            ):
                return _opencode_full_access_env()
            return None
        if (
            self.backend == BACKEND_OPENCODE
            and options.sandbox_mode == "read-only"
        ):
            return _opencode_read_only_env()
        if (
            self.backend == BACKEND_OPENCODE
            and (options.dangerous_yolo or options.full_auto)
        ):
            env = _opencode_full_access_env()
        else:
            env = sandboxed_child_env()
        if self.backend == BACKEND_COPILOT:
            apply_copilot_home(env)
        elif self.backend == BACKEND_PI:
            _apply_pi_automation_env(env)
        if options.isolate_workdir:
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
            env["GH_CONFIG_DIR"] = "/tmp/argus-no-gh-auth"
        return env
