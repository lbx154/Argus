"""Map text generation through the front-door (Manager) runner at a light effort tier."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator, ValidationError

from ..adapters.agent_cli_backend import AgentCliBackend
from ..agent_cli.runner_backend import default_runner_bin, normalize_runner_backend
from ..core.knobs import normalize_cockpit_knob_value, resolve_knob, resolve_runner_bin_setting
from ..core.models import RunnerOptions, RunnerResult
from ..core.role_config import RoleConfig, resolve_role_config
from ..core.run_gateway import run_exec
from .map_view import digest

MapCopyPhase = Literal["waiting_for_source", "planning", "writing", "reviewing"]
MapProgress = Callable[[MapCopyPhase], None]


class MapGenerationError(OSError):
    """A public classification without provider text, prompts or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"map text generation did not complete: {code}")


def map_limit(name: str, default: str) -> int:
    return int(normalize_cockpit_knob_value(name, resolve_knob(name, default).value))


def map_timeout_seconds() -> int:
    return map_limit("ARGUS_SKILL_MAP_TIMEOUT_SECONDS", "600")


@dataclass(frozen=True)
class MapModel:
    backend: str
    model: str
    effort: str | None
    runner_bin: str
    extra_args: tuple[str, ...] = ()
    review_effort: str | None = None
    # Why a pinned map model was set aside for ``auto``; empty when the pin
    # (or auto) is used as configured. Not part of the revision: the model
    # that actually runs already is.
    note: str = field(default="", compare=False)

    @property
    def available(self) -> bool:
        return self.backend != "memory" and bool(shutil.which(self.runner_bin))

    @property
    def revision(self) -> str:
        return digest([self.backend, self.model, self.effort, self.runner_bin, self.extra_args,
                       self.review_effort or self.effort])

    def for_review(self) -> MapModel:
        return replace(self, effort=self.review_effort or self.effort, review_effort=None)


# Map copy is read-time presentation, not research: opening a project must not
# spend the engineer's deep-reasoning budget. ``auto`` follows the front-door
# role (the lighter triage model) at a low drafting effort, with a medium
# check pass; explicit operator settings still win.
MAP_AUTO_EFFORT = "low"
MAP_AUTO_REVIEW_EFFORT = "medium"
MAP_AUTO_ROLE = "manager"


MAP_MODEL_KNOB = "ARGUS_SKILL_MAP_MODEL"
MAP_MODEL_BACKEND_KNOB = "ARGUS_SKILL_MAP_MODEL_BACKEND"


def map_base_role() -> tuple[str, RoleConfig]:
    """The role whose runner writes map text: the front door, else the engineer."""
    base = resolve_role_config(MAP_AUTO_ROLE)
    if base.backend == "memory":
        # A front door without a real runner cannot write map text; the map
        # keeps the research runner rather than going dark.
        research = resolve_role_config("engineer")
        if research.backend != "memory":
            return "engineer", research
    return MAP_AUTO_ROLE, base


def pinned_map_model_unavailable(model: str, backend: str, *, global_root: Path | None = None) -> str:
    """Why ``backend`` cannot run the pinned map ``model`` (empty when it can).

    A pin is chosen from one backend's list; switching the runner keeps the
    config entry but not its meaning. A backend that publishes its models
    decides by its list. One that does not is trusted only with a model pinned
    while it was the active backend, or one that has already answered
    through it here.
    """
    from .mission_items import _seen_model_ids, backend_model_ids

    try:
        listed = backend_model_ids(backend, global_root)
    except Exception:  # noqa: BLE001 - an unreachable list falls through to the history check
        listed = []
    if listed:
        if model in listed:
            return ""
        return f"{model} is not in the {backend} model list"
    recorded = resolve_knob(MAP_MODEL_BACKEND_KNOB, "").value.strip()
    if recorded == backend:
        return ""
    try:
        if model in _seen_model_ids(global_root, provider=backend):
            return ""
    except Exception:  # noqa: BLE001 - history is a hint, never a failure
        pass
    origin = f"while {recorded} was the runner" if recorded else "for another runner"
    return f"{model} was chosen {origin} and has not answered through {backend}"


def resolve_map_model(*, global_root: Path | None = None) -> MapModel:
    role, base = map_base_role()
    pinned = resolve_knob(MAP_MODEL_KNOB, "auto")
    model, note = pinned.value, ""
    # Only a saved cockpit choice is vetted: it outlives the runner it was
    # picked for. A process-env pin is the deployment speaking for itself.
    if pinned.source == "persisted" and model.lower() != "auto" and base.backend != "memory":
        reason = pinned_map_model_unavailable(model, base.backend, global_root=global_root)
        if reason:
            note = f"Map model follows auto: {reason}."
            logging.getLogger(__name__).warning(note)
            model = "auto"
    effort = resolve_knob("ARGUS_SKILL_MAP_REASONING_EFFORT", "auto").value
    review_effort = resolve_knob("ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", "auto").value
    runner = resolve_runner_bin_setting(role, backend=base.backend)
    if not runner and base.backend != "memory":
        runner = default_runner_bin(normalize_runner_backend(base.backend))
    reasoning = base.effort is not None or model.lower() != "auto"
    auto_effort = MAP_AUTO_EFFORT if reasoning else None
    auto_review = MAP_AUTO_REVIEW_EFFORT if reasoning else None
    return MapModel(
        backend=base.backend,
        model=base.model if model.lower() == "auto" else model,
        effort=auto_effort if effort.lower() == "auto" else effort,
        runner_bin=runner,
        extra_args=tuple(shlex.split(os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", ""))),
        review_effort=auto_review if review_effort.lower() == "auto" else review_effort,
        note=note,
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


def _parse_document(raw: str, output_schema: dict, prepare: Callable[[dict], dict] | None = None) -> dict:
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
    if prepare is not None and isinstance(value, dict):
        value = prepare(value)
    return _checked_document(value, output_schema)


def _checked_document(value: object, output_schema: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid card document")
    try:
        Draft202012Validator(output_schema).validate(value)
    except ValidationError:
        # Do not include model content in server validation logs.
        raise ValueError("map document does not satisfy its bounded output schema") from None
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
    timeout = map_timeout_seconds()  # Validate before constructing a provider.
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    if time.monotonic() >= deadline:
        raise MapGenerationError("map_timeout")
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
        reason = str(result.fatal_error or "").lower()
        code = (
            "map_timeout" if time.monotonic() >= deadline else
            "cost_unreconciled" if result.stop_kind == "cost_unreconciled" or "unresolved provider cost" in reason else
            "budget_exhausted" if result.stop_kind == "budget_exhausted" else
            "provider_error"
        )
        raise MapGenerationError(code)
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
    prepare: Callable[[dict], dict] | None = None,
) -> dict:
    """Share execution; Markdown uses its schema only for local field limits.

    `prepare` sees the parsed JSON before the schema does, for a caller that
    knows a wrong shape its model keeps producing and can move the model's own
    text to where the schema says it goes. It must not write text."""
    if output_format not in {"json", "markdown"}:
        raise ValueError("unsupported map output format")
    result = _run_map_turn(
        prompt, output_schema if output_format == "json" else None, config,
        project_root=project_root, global_root=global_root, deadline=deadline,
        on_progress=on_progress, phase=phase, run_label=run_label, on_result=on_result,
    )
    if output_format == "json":
        return _parse_document(result.last_agent_message, output_schema, prepare)
    return _parse_markdown_document(result.last_agent_message, output_schema)
