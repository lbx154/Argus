"""Prompt delivery (stdin) and per-backend output-schema embedding.

Covers how a prompt reaches the child process (stdin, never argv — a large
reviewer/planner prompt would trip the kernel per-arg limit), the compact
JSON-Schema suffix appended for backends without a native ``--output-schema``
flag, and the sandboxed/isolated child environment. Extracted verbatim from
``agent_cli_runner.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..core.sandbox import sandboxed_child_env
from ._sandbox_commands import _OPENCODE_READ_ONLY_AGENT
from .runner_backend import BACKEND_COPILOT, BACKEND_OPENCODE

_OPENCODE_CONFIG_CONTENT_ENV = "OPENCODE_CONFIG_CONTENT"


def _opencode_read_only_env() -> dict[str, str]:
    """Inject a final-precedence OpenCode agent that cannot invoke write tools."""
    env = sandboxed_child_env()
    raw = str(env.get(_OPENCODE_CONFIG_CONTENT_ENV) or "").strip()
    if raw:
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{_OPENCODE_CONFIG_CONTENT_ENV} must be valid JSON for "
                "read-only OpenCode calls"
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
    agents[_OPENCODE_READ_ONLY_AGENT] = {
        "description": "Argus read-only inspection agent.",
        "mode": "primary",
        "permission": {
            "*": "deny",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
        },
    }
    config["agent"] = agents
    env[_OPENCODE_CONFIG_CONTENT_ENV] = json.dumps(
        config,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return env


class PromptDeliveryMixin:
    """Prompt-on-stdin delivery + embedded output-schema contract."""

    def _effective_prompt(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options,
    ) -> str:
        """Prompt actually delivered to the backend (via stdin).

        Copilot and OpenCode have no ``--output-schema`` flag, so the compact JSON Schema +
        strict "reply with ONLY schema-valid JSON" contract is appended to the
        prompt itself (skipped on a resumed thread, where the contract already
        lives in the conversation). codex/claude carry the schema out-of-band
        via their own flags, so their prompt is returned unchanged.
        """
        if self.backend not in (BACKEND_COPILOT, BACKEND_OPENCODE):
            return prompt
        if options.output_schema_path and not resume_thread_id:
            suffix = self._prompt_schema_suffix(options.output_schema_path)
            if suffix:
                return prompt + suffix
        return prompt

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

    def _prompt_via_stdin(self) -> bool:
        # All agent CLI backends receive the prompt through
        # stdin. Passing a large prompt via argv trips the kernel per-arg limit
        # (E2BIG / OSError: [Errno 7] Argument list too long); stdin has no such
        # cap. codex uses a trailing ``-``, claude runs ``-p`` print-mode with no
        # value, and copilot/opencode omit a prompt argument entirely.
        return True

    def _child_env(self, options) -> dict[str, str] | None:
        if not options.sandbox_mode and not options.isolate_workdir:
            return None
        if (
            self.backend == BACKEND_OPENCODE
            and options.sandbox_mode == "read-only"
        ):
            return _opencode_read_only_env()
        env = sandboxed_child_env()
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

    @staticmethod
    def _load_compact_schema_text(path: str) -> str:
        raw = Path(path).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))

    def _prompt_schema_suffix(self, schema_path: str) -> str:
        """Prompt-embedded output contract for backends without a schema flag.

        EN: Copilot and OpenCode have no ``--output-schema``. Append the compact JSON Schema +
        a strict "reply with ONLY schema-valid JSON" instruction so the
        reviewer/planner verdict parses instead of degrading to a prose reply
        (which the strict parser rejects → the reviewer, the sole done-authority,
        would fall back to ``continue``). Fail-soft to "" — a missing/invalid
        schema must never block a run.
        中文：copilot 没有 ``--output-schema``。把压缩后的 JSON Schema + 严格
        "只回合法 JSON"指令追加到 prompt，让 reviewer/planner 裁决可解析，而不是
        退化成散文（严格 parser 会拒 → reviewer 退回 ``continue``）。schema
        缺失/非法时返回 ""，绝不阻塞运行。
        """
        try:
            schema_text = self._load_compact_schema_text(schema_path)
        except Exception:  # noqa: BLE001 — no/invalid schema → no suffix, fail-open
            return ""
        if not schema_text.strip():
            return ""
        return (
            "\n\n--- OUTPUT CONTRACT (STRICT) ---\n"
            "Your FINAL message MUST be exactly one JSON object that validates "
            "against this JSON Schema. No prose, no markdown fences, nothing "
            "before or after it:\n"
            f"{schema_text}\n"
        )
