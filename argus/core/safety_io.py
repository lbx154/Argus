"""Strict JSON decoding and atomic file/directory persistence; no admission policy."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .json_codec import loads_finite_json


def loads_strict_json(raw: str | bytes) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result
    # Check non-finite constants/exponents as well as nested duplicate keys.
    loads_finite_json(raw)
    return json.loads(raw, object_pairs_hook=unique)


def fsync_directory(path: Path) -> None:
    # Windows does not expose POSIX directory descriptors. Windows durability
    # remains unverified; file fsync and atomic replace still must succeed.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic file + directory durability; never swallow ENOSPC/fsync errors."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        fsync_directory(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)
