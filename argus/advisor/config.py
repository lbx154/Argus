"""Project-scoped advisor settings, independent of the execution model knobs.

``project_root`` always means the harness state directory, not the workspace.
Only explicit advisor environment variables override the persisted settings.
No provider credential or implicit default model belongs in this document.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..core.file_lock import exclusive_file_lock


class AdvisorConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AdvisorConfig:
    schema_version: int = 1
    enabled: bool = False
    backend: str = ""
    model: str = ""
    effort: str = ""
    runner_bin: str = ""
    timeout_seconds: int = 120
    max_calls_per_turn: int = 2
    max_evidence_bytes: int = 65536

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validated(values: Mapping[str, Any]) -> AdvisorConfig:
    if set(values) - set(AdvisorConfig.__dataclass_fields__):
        raise AdvisorConfigError("unknown advisor setting")
    config = AdvisorConfig(**values)
    if type(config.schema_version) is not int or config.schema_version != 1:
        raise AdvisorConfigError("unsupported advisor config version")
    if type(config.enabled) is not bool:
        raise AdvisorConfigError("advisor enabled must be a boolean")
    for name in ("backend", "model", "effort", "runner_bin"):
        value = getattr(config, name)
        if not isinstance(value, str) or value != value.strip() or len(value) > 1024:
            raise AdvisorConfigError(f"invalid advisor {name}")
    if config.backend:
        from ..agent_cli.runner_backend import SUPPORTED_BACKENDS

        if config.backend not in SUPPORTED_BACKENDS:
            raise AdvisorConfigError("unsupported advisor backend")
    if config.model and (config.model.lower() in {"auto", "default"} or any(c.isspace() for c in config.model)):
        raise AdvisorConfigError("advisor model must be an explicit model identifier")
    if config.effort not in {"", "minimal", "low", "medium", "high", "xhigh", "max"}:
        raise AdvisorConfigError("invalid advisor reasoning effort")
    for name, low, high in (
        ("timeout_seconds", 1, 1800), ("max_calls_per_turn", 1, 20),
        ("max_evidence_bytes", 1024, 262144),
    ):
        value = getattr(config, name)
        if type(value) is not int or not low <= value <= high:
            raise AdvisorConfigError(f"advisor {name} must be between {low} and {high}")
    if config.enabled and (not config.backend or not config.model):
        raise AdvisorConfigError("enabled advisor requires an explicit backend and model")
    return config


def load_advisor_config(project_root: Path | str, *, env: Mapping[str, str] | None = None) -> AdvisorConfig:
    path = Path(project_root) / "advisor" / "config.json"
    values = AdvisorConfig().to_dict()
    try:
        with path.open("rb") as handle:
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise AdvisorConfigError("advisor config is oversized")
        stored = json.loads(raw)
        if not isinstance(stored, dict):
            raise AdvisorConfigError("invalid advisor config document")
        values.update(stored)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise AdvisorConfigError("cannot read advisor config") from exc
    source = os.environ if env is None else env
    for name in AdvisorConfig.__dataclass_fields__:
        if name == "schema_version":
            continue
        value = source.get(f"ARGUS_SKILL_ADVISOR_{name.upper()}")
        if value is None:
            continue
        if name == "enabled":
            if value.lower() not in {"0", "1", "false", "true"}:
                raise AdvisorConfigError("invalid advisor enabled override")
            values[name] = value.lower() in {"1", "true"}
        elif name in {"timeout_seconds", "max_calls_per_turn", "max_evidence_bytes"}:
            try:
                values[name] = int(value)
            except ValueError as exc:
                raise AdvisorConfigError(f"invalid advisor {name} override") from exc
        else:
            values[name] = value.strip()
    return _validated(values)


def save_advisor_config(project_root: Path | str, values: AdvisorConfig | Mapping[str, Any]) -> AdvisorConfig:
    directory = Path(project_root) / "advisor"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "config.lock").open("a+b") as lock, exclusive_file_lock(lock):
        document = (values.to_dict() if isinstance(values, AdvisorConfig)
                    else {**load_advisor_config(project_root, env={}).to_dict(), **values})
        config = _validated(document)
        fd, temporary = tempfile.mkstemp(prefix=".config-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(config.to_dict(), output, ensure_ascii=False, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, directory / "config.json")
        finally:
            Path(temporary).unlink(missing_ok=True)
    return config
