"""Explicit project embedding configuration; absent configuration stays local."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..core.file_lock import exclusive_file_lock
from ..core.json_codec import loads_finite_json
from ..core.scoped_file import open_regular_file
from .failure_experience_index import EmbeddingAdapter, LexicalHashEmbedding, _guard_recall_path

log = logging.getLogger(__name__)


class EmbeddingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RecallEmbeddingConfig:
    version: int = 1
    enabled: bool = False
    endpoint: str = ""
    model: str = ""
    dimensions: int = 1536
    credential_env: str = ""
    request_dimensions: bool = True
    timeout_seconds: float = 3.0
    batch_timeout_seconds: float = 5.0
    max_requests_per_batch: int = 16
    max_input_bytes: int = 8192
    max_response_bytes: int = 262144
    daily_request_budget: int = 256
    daily_input_bytes_budget: int = 1048576
    cache_entries: int = 1024
    cache_max_bytes: int = 8388608
    api_format: str = "openai"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validated(values: Mapping[str, Any]) -> RecallEmbeddingConfig:
    if set(values) - set(RecallEmbeddingConfig.__dataclass_fields__):
        raise EmbeddingConfigError("unknown embedding setting; credentials must use an environment reference")
    config = RecallEmbeddingConfig(**values)
    if type(config.version) is not int or config.version != 1:
        raise EmbeddingConfigError("unsupported embedding configuration version")
    if type(config.enabled) is not bool or type(config.request_dimensions) is not bool:
        raise EmbeddingConfigError("embedding switches must be boolean")
    if config.api_format not in ("openai", "copilot"):
        raise EmbeddingConfigError("embedding api_format must be openai or copilot")
    for name in ("endpoint", "model", "credential_env"):
        value = getattr(config, name)
        if not isinstance(value, str) or value != value.strip() or len(value) > 1024:
            raise EmbeddingConfigError("invalid embedding configuration text")
    if config.model and not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", config.model):
        raise EmbeddingConfigError("embedding model must be an explicit identifier")
    if config.credential_env and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", config.credential_env):
        raise EmbeddingConfigError("invalid embedding credential environment reference")
    if config.endpoint:
        try:
            url = urlsplit(config.endpoint)
            valid = (url.scheme in {"https", "http"} and url.hostname and url.port != 0
                     and not url.username and not url.password and not url.query and not url.fragment
                     and (url.scheme == "https" or url.hostname in {"127.0.0.1", "::1", "localhost"}))
        except ValueError:
            valid = False
        if not valid:
            raise EmbeddingConfigError("embedding endpoint requires HTTPS or loopback HTTP, without credentials or query")
    for name, low, high in (
        ("dimensions", 1, 4096), ("max_requests_per_batch", 1, 256),
        ("max_input_bytes", 1, 8192), ("max_response_bytes", 256, 1048576),
        ("daily_request_budget", 1, 100000), ("daily_input_bytes_budget", 1, 100000000),
        ("cache_entries", 1, 8192), ("cache_max_bytes", 1024, 67108864),
    ):
        value = getattr(config, name)
        if type(value) is not int or not low <= value <= high:
            raise EmbeddingConfigError(f"embedding {name} must be between {low} and {high}")
    for name in ("timeout_seconds", "batch_timeout_seconds"):
        value = getattr(config, name)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0.05 <= value <= 30:
            raise EmbeddingConfigError("embedding timeouts must be finite and between 0.05 and 30 seconds")
    if config.enabled and (not config.endpoint or not config.model):
        raise EmbeddingConfigError("enabled embedding requires an explicit endpoint and model")
    return config


def load_embedding_config(state_root: Path | str) -> RecallEmbeddingConfig:
    try:
        path = Path(state_root) / "embedding" / "config.json"
        _guard_recall_path(path)
        with open_regular_file(path) as handle:
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise EmbeddingConfigError("embedding configuration is oversized")
        values = loads_finite_json(raw)
        if not isinstance(values, dict):
            raise EmbeddingConfigError("embedding configuration must be an object")
        return _validated(values)
    except FileNotFoundError:
        return RecallEmbeddingConfig()
    except (OSError, ValueError, TypeError):
        raise EmbeddingConfigError("embedding configuration is unavailable or invalid") from None


def save_embedding_config(state_root: Path | str, values: RecallEmbeddingConfig | Mapping[str, Any]) -> RecallEmbeddingConfig:
    directory = Path(state_root) / "embedding"
    _guard_recall_path(directory / "config.json")
    _guard_recall_path(directory / "config.lock")
    directory.mkdir(parents=True, exist_ok=True)
    with open_regular_file(directory / "config.lock", os.O_RDWR | os.O_CREAT) as lock, exclusive_file_lock(lock):
        document = values.to_dict() if isinstance(values, RecallEmbeddingConfig) else {
            **load_embedding_config(state_root).to_dict(), **values,
        }
        config = _validated(document)
        descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(config.to_dict(), handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, directory / "config.json")
            from ..daemon.state import _fsync_directory

            _fsync_directory(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return config


def configured_embedder(state_root: Path | str) -> EmbeddingAdapter:
    try:
        config = load_embedding_config(state_root)
    except EmbeddingConfigError:
        log.warning("Embedding configuration unavailable; using local lexical hashing")
        return LexicalHashEmbedding()
    if not config.enabled:
        return LexicalHashEmbedding()
    from .http_embedding import HttpEmbeddingAdapter

    try:
        return HttpEmbeddingAdapter(Path(state_root), config)
    except (OSError, ValueError):
        log.warning("Embedding storage unavailable; using local lexical hashing")
        return LexicalHashEmbedding()


__all__ = ["RecallEmbeddingConfig", "EmbeddingConfigError", "configured_embedder", "load_embedding_config", "save_embedding_config"]
