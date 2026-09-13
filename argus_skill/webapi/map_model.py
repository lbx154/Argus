"""Map text generation through the configured research runner."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator, ValidationError

from ..adapters.agent_cli_backend import AgentCliBackend
from ..agent_cli.runner_backend import default_runner_bin, normalize_runner_backend
from ..core.knobs import resolve_knob, resolve_runner_bin_setting
from ..core.models import RunnerOptions, RunnerResult
from ..core.role_config import resolve_role_config
from ..core.run_gateway import run_exec
from .map_view import digest

MapCopyPhase = Literal["waiting_for_source", "planning", "writing", "reviewing"]
MapProgress = Callable[[MapCopyPhase], None]


@dataclass(frozen=True)
class MapModel:
    backend: str
    model: str
    effort: str | None
    runner_bin: str
    extra_args: tuple[str, ...] = ()
    review_effort: str | None = None

    @property
    def available(self) -> bool:
        return self.backend != "memory" and bool(shutil.which(self.runner_bin))

    @property
    def revision(self) -> str:
        return digest([self.backend, self.model, self.effort, self.runner_bin, self.extra_args,
                       self.review_effort or self.effort])

    def for_review(self) -> MapModel:
        return replace(self, effort=self.review_effort or self.effort, review_effort=None)


def resolve_map_model() -> MapModel:
    research = resolve_role_config("engineer")
    model = resolve_knob("ARGUS_SKILL_MAP_MODEL", "auto").value
    effort = resolve_knob("ARGUS_SKILL_MAP_REASONING_EFFORT", "auto").value
    review_effort = resolve_knob("ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", "auto").value
    runner = resolve_runner_bin_setting("engineer", backend=research.backend)
    if not runner and research.backend != "memory":
        runner = default_runner_bin(normalize_runner_backend(research.backend))
    return MapModel(
        backend=research.backend,
        model=research.model if model.lower() == "auto" else model,
        effort=research.effort if effort.lower() == "auto" else effort,
        runner_bin=runner,
        extra_args=tuple(shlex.split(os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", ""))),
        review_effort=None if review_effort.lower() == "auto" else review_effort,
    )


def _literal_unknown_escapes(raw: str) -> str:
    """Preserve literal backslashes that cannot represent a JSON escape."""
    parts, quoted, index = [], False, 0
    while index < len(raw):
        char = raw[index]
        if char == '"':
            quoted = not quoted
        if quoted and char == "\\" and index + 1 < len(raw):
            if raw[index + 1] not in '\\"/bfnrtu':
                parts.append("\\")
            parts.append(raw[index:index + 2])
            index += 2
        else:
            parts.append(char)
            index += 1
    return "".join(parts)


def _document_value(raw: str):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as original:
        # One observed response closed the top-level object after `cards`, then
        # continued with ,"relations":[...]}. Parse both complete sections;
        # never strip arbitrary prose, invent fields, or accept a second result.
        try:
            cards, end = json.JSONDecoder().raw_decode(raw)
            tail = raw[end:].lstrip()
            if not isinstance(cards, dict) or set(cards) != {"cards"} or not isinstance(cards["cards"], dict):
                raise ValueError
            if not tail.startswith(","):
                raise ValueError
            relations = json.loads("{" + tail[1:])
            if (not isinstance(relations, dict) or set(relations) != {"relations"}
                    or not isinstance(relations["relations"], list)):
                raise ValueError
            value = {**cards, **relations}
        except (ValueError, TypeError):
            raise original from None
        logging.getLogger(__name__).warning("Recovered premature closure in map presentation object")
    return value


def _parse_document(raw: str, output_schema: dict) -> dict:
    """Read the shared draft/check format, preserving prose and schema checks."""
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = _document_value(raw)
    except json.JSONDecodeError:
        literal = _literal_unknown_escapes(raw)
        if literal == raw:
            raise
        value = _document_value(literal)
        logging.getLogger(__name__).warning("Preserved literal backslashes in map presentation JSON")
    return _checked_document(value, output_schema)


def _checked_document(value: object, output_schema: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid card document")
    try:
        Draft202012Validator(output_schema).validate(value)
    except ValidationError:
        # Do not include model content in server validation logs.
        raise ValueError("invalid card document schema") from None
    return value


def _parse_markdown_document(raw: str, output_schema: dict) -> dict:
    """Extract only the first H1 title; never JSON-decode or repair the body."""
    markdown = raw.strip()
    heading, separator, body = markdown.partition("\n")
    if not heading.startswith("# ") or not separator or not body.strip():
        raise ValueError("reading Markdown requires a first H1 title and a nonempty body")
    title = heading[2:].strip()
    if not title:
        raise ValueError("reading Markdown title is empty")
    return _checked_document({"title": title, "markdown": markdown}, output_schema)


def _run_map_turn(
    prompt: str,
    output_schema: dict | None,
    config: MapModel,
    *,
    project_root: Path,
    global_root: Path,
    deadline: float | None = None,
    on_progress: MapProgress | None = None,
    phase: MapCopyPhase = "writing",
    run_label: str = "map-summary",
    on_result: Callable[[RunnerResult], None] | None = None,
) -> RunnerResult:
    """Execute once and retain the real receipt before any format parsing."""
    deadline = deadline if deadline is not None else time.monotonic() + 180
    if time.monotonic() >= deadline:
        raise OSError("map text generation timed out")
    backend = AgentCliBackend(
        backend=config.backend, runner_bin=config.runner_bin,
        default_extra_args=list(config.extra_args),
    )
    backend.set_usage_context(project_root=project_root, global_root=global_root)
    scratch = global_root / "map-presentation"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        # A separate, read-only turn cannot resume or edit the research conversation.
        with tempfile.TemporaryDirectory(prefix="generation-", dir=scratch) as workdir:
            if on_progress is not None:
                on_progress(phase)
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
                    output_schema=output_schema if config.backend in {"codex", "pi"} else None,
                    external_interrupt_reason_provider=lambda: (
                        "Map text generation timed out" if time.monotonic() >= deadline else None
                    ),
                ),
                run_label=run_label,
            )
            if on_result is not None:
                # Preserve the real call receipt even when execution or output validation fails.
                on_result(result)
    finally:
        backend.close_acp_clients()
    if result.exit_code or result.fatal_error:
        raise OSError("map text generation did not complete")
    if result.tool_activity_observed:
        raise ValueError("map text generation attempted to use tools")
    return result


def run_map_model(
    prompt: str,
    output_schema: dict,
    config: MapModel,
    *,
    project_root: Path,
    global_root: Path,
    deadline: float | None = None,
    on_progress: MapProgress | None = None,
    phase: MapCopyPhase = "writing",
    run_label: str = "map-summary",
    on_result: Callable[[RunnerResult], None] | None = None,
    output_format: Literal["json", "markdown"] = "json",
) -> dict:
    """Share execution; Markdown uses its schema only for local field limits."""
    if output_format not in {"json", "markdown"}:
        raise ValueError("unsupported map output format")
    result = _run_map_turn(
        prompt, output_schema if output_format == "json" else None, config,
        project_root=project_root, global_root=global_root, deadline=deadline,
        on_progress=on_progress, phase=phase, run_label=run_label, on_result=on_result,
    )
    parse = _parse_document if output_format == "json" else _parse_markdown_document
    return parse(result.last_agent_message, output_schema)
