"""Deterministic release identity shared by backend, daemon, and frontends."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

MANIFEST_FILE = "release_manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def _source_files(root: Path) -> Iterable[Path]:
    patterns = (
        "argus_skill/**/*.py",
        "argus_skill/**/*.json",
        "argus_skill/builtin_skills/**/*.md",
        "frontend/core/src/**/*.ts",
        "frontend/tui/src/**/*.ts",
        "frontend/tui/src/**/*.tsx",
        "frontend/web/src/**/*.ts",
        "frontend/web/src/**/*.tsx",
        "scripts/generate_event_payload_types.py",
        "scripts/generate_release_manifest.py",
        "pyproject.toml",
    )
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if (
                not path.is_file()
                or path.name == MANIFEST_FILE
                or path.name.endswith(".generated.ts")
            ):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def compute_source_digest(root: Path | str) -> str:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    for path in sorted(_source_files(base), key=lambda item: item.as_posix()):
        relative = path.resolve().relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def release_manifest() -> dict[str, Any]:
    path = Path(__file__).with_name(MANIFEST_FILE)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "package_version": "unknown",
            "source_digest": "",
            "release_id": "unknown",
        }
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=4)
def release_identity(source_root: Path | str | None = None) -> dict[str, Any]:
    manifest = release_manifest()
    root = Path(source_root).resolve() if source_root is not None else None
    runtime_digest: str | None = None
    if (
        root is not None
        and (root / "pyproject.toml").is_file()
        and (root / "frontend" / "core" / "src").is_dir()
    ):
        try:
            runtime_digest = compute_source_digest(root)
        except OSError:
            runtime_digest = None
    expected = str(manifest.get("source_digest") or "")
    return {
        "release_id": str(manifest.get("release_id") or "unknown"),
        "manifest_source_digest": expected or None,
        "runtime_source_digest": runtime_digest,
        "release_matches_source": (
            None if runtime_digest is None or not expected else runtime_digest == expected
        ),
    }


__all__ = [
    "MANIFEST_FILE",
    "MANIFEST_SCHEMA_VERSION",
    "compute_source_digest",
    "release_identity",
    "release_manifest",
]
