"""Map text generation through the configured research runner."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..adapters.agent_cli_backend import AgentCliBackend
from ..agent_cli.runner_backend import default_runner_bin, normalize_runner_backend
from ..core.knobs import resolve_knob, resolve_runner_bin_setting
from ..core.models import RunnerOptions
from ..core.role_config import resolve_role_config
from ..core.run_gateway import run_exec
from .map_view import digest


@dataclass(frozen=True)
class MapModel:
    backend: str
    model: str
    effort: str | None
    runner_bin: str
    extra_args: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.backend != "memory" and bool(shutil.which(self.runner_bin))

    @property
    def revision(self) -> str:
        return digest([self.backend, self.model, self.effort, self.runner_bin, self.extra_args])


def resolve_map_model() -> MapModel:
    research = resolve_role_config("engineer")
    model = resolve_knob("ARGUS_SKILL_MAP_MODEL", "auto").value
    effort = resolve_knob("ARGUS_SKILL_MAP_REASONING_EFFORT", "auto").value
    runner = resolve_runner_bin_setting("engineer", backend=research.backend)
    if not runner and research.backend != "memory":
        runner = default_runner_bin(normalize_runner_backend(research.backend))
    return MapModel(
        backend=research.backend,
        model=research.model if model.lower() == "auto" else model,
        effort=research.effort if effort.lower() == "auto" else effort,
        runner_bin=runner,
        extra_args=tuple(shlex.split(os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", ""))),
    )


def run_map_model(
    prompt: str,
    output_schema: dict,
    config: MapModel,
    *,
    project_root: Path,
    global_root: Path,
) -> dict:
    backend = AgentCliBackend(
        backend=config.backend, runner_bin=config.runner_bin,
        default_extra_args=list(config.extra_args),
    )
    backend.set_usage_context(project_root=project_root, global_root=global_root)
    scratch = global_root / "map-presentation"
    scratch.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 180
    try:
        # A separate, read-only turn cannot resume or edit the research conversation.
        with tempfile.TemporaryDirectory(prefix="generation-", dir=scratch) as workdir:
            extra_args = []
            if config.backend == "codex":
                schema_path = Path(workdir) / "output-schema.json"
                schema_path.write_text(json.dumps(output_schema), encoding="utf-8")
                extra_args = ["--output-schema", str(schema_path)]
            result = run_exec(
                backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=config.model or None,
                    reasoning_effort=config.effort,
                    working_dir=workdir,
                    skip_git_repo_check=True,
                    sandbox_mode="read-only",
                    force_safe_mode=True,
                    disable_tools=True,
                    extra_args=extra_args,
                    external_interrupt_reason_provider=lambda: (
                        "Map text generation timed out" if time.monotonic() >= deadline else None
                    ),
                ),
                run_label="map-summary",
            )
    finally:
        backend.close_acp_clients()
    if result.exit_code or result.fatal_error:
        raise OSError("map text generation did not complete")
    if result.tool_activity_observed:
        raise ValueError("map text generation attempted to use tools")
    raw = result.last_agent_message.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid card document")
    return value
