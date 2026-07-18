"""Load, query, and probe the curated professional kernel-tool registry."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

REGISTRY_PATH = Path(__file__).with_name("references") / "specialized_tool_registry.json"
LEGACY_STATUSES = frozenset({"archived", "deprecated", "moved"})


def load_registry(path: Path | None = None) -> dict[str, Any]:
    source = path or REGISTRY_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError(f"invalid kernel tool registry: {source}")
    return payload


def filter_entries(
    registry: dict[str, Any],
    *,
    categories: Iterable[str] = (),
    platforms: Iterable[str] = (),
    query: str = "",
    include_legacy: bool = False,
) -> list[dict[str, Any]]:
    wanted_categories = {str(value).strip().lower() for value in categories if str(value).strip()}
    wanted_platforms = {str(value).strip().lower() for value in platforms if str(value).strip()}
    needle = query.strip().lower()
    result: list[dict[str, Any]] = []
    for raw in registry.get("entries", []):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "active").strip().lower()
        if not include_legacy and status in LEGACY_STATUSES:
            continue
        entry_categories = {str(value).lower() for value in raw.get("categories", [])}
        entry_platforms = {str(value).lower() for value in raw.get("platforms", [])}
        if wanted_categories and not wanted_categories.intersection(entry_categories):
            continue
        if wanted_platforms and not wanted_platforms.intersection(entry_platforms):
            continue
        if needle:
            searchable = " ".join(
                [
                    str(raw.get("id") or ""),
                    str(raw.get("name") or ""),
                    str(raw.get("use_when") or ""),
                    " ".join(str(value) for value in raw.get("categories", [])),
                    " ".join(str(value) for value in raw.get("platforms", [])),
                ]
            ).lower()
            if needle not in searchable:
                continue
        result.append(raw)
    return result


def _run(argv: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _probe_python_entries(
    entries: list[dict[str, Any]], target_python: str
) -> dict[str, dict[str, Any]]:
    mapping = {
        str(entry["id"]): {
            "imports": [str(value) for value in entry.get("python_imports", [])],
            "distributions": [str(value) for value in entry.get("distributions", [])],
        }
        for entry in entries
        if entry.get("python_imports") or entry.get("distributions")
    }
    if not mapping:
        return {}
    probe = r"""
import importlib.metadata, importlib.util, json, sys
mapping = json.loads(sys.argv[1])
out = {}
for key, spec in mapping.items():
    imports = spec.get("imports") or []
    dists = spec.get("distributions") or []
    found_imports = []
    for name in imports:
        try:
            if importlib.util.find_spec(name) is not None:
                found_imports.append(name)
        except (ImportError, ModuleNotFoundError, ValueError):
            pass
    version = ""
    distribution = ""
    for name in dists:
        try:
            version = importlib.metadata.version(name)
            distribution = name
            break
        except importlib.metadata.PackageNotFoundError:
            pass
    out[key] = {
        "found_imports": found_imports,
        "distribution": distribution,
        "version": version,
    }
print(json.dumps(out))
"""
    result = _run([target_python, "-c", probe, json.dumps(mapping)])
    if result is None or result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def probe_entries(
    registry: dict[str, Any],
    *,
    target_python: str,
    project_root: Path,
) -> list[dict[str, Any]]:
    entries = [entry for entry in registry.get("entries", []) if isinstance(entry, dict)]
    python_records = _probe_python_entries(entries, target_python)
    root = project_root.resolve()
    detected: list[dict[str, Any]] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        py = python_records.get(entry_id, {})
        executables: dict[str, str] = {}
        for name in entry.get("executables", []):
            path = shutil.which(name)
            if path:
                executables[name] = path
        env_vars = [name for name in entry.get("env_vars", []) if os.environ.get(name)]
        source_markers = [
            marker for marker in entry.get("source_markers", []) if (root / str(marker)).exists()
        ]
        found_imports = list(py.get("found_imports") or [])
        distribution = str(py.get("distribution") or "")
        available = bool(found_imports or distribution or executables or env_vars or source_markers)
        detectable = bool(
            entry.get("python_imports")
            or entry.get("distributions")
            or entry.get("executables")
            or entry.get("env_vars")
            or entry.get("source_markers")
        )
        detected.append(
            {
                "id": entry_id,
                "name": entry.get("name"),
                "status": entry.get("status", "active"),
                "categories": entry.get("categories", []),
                "platforms": entry.get("platforms", []),
                "available": available,
                "detectable": detectable,
                "version": str(py.get("version") or ""),
                "found_imports": found_imports,
                "distribution": distribution,
                "executables": executables,
                "env_vars": env_vars,
                "source_markers": source_markers,
            }
        )
    return detected


def render_catalog(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No matching kernel tools."
    lines = ["id\tstatus\tplatforms\tcategories\tname\tuse_when"]
    for entry in entries:
        lines.append(
            "\t".join(
                [
                    str(entry.get("id") or ""),
                    str(entry.get("status") or "active"),
                    ",".join(str(value) for value in entry.get("platforms", [])),
                    ",".join(str(value) for value in entry.get("categories", [])),
                    str(entry.get("name") or ""),
                    str(entry.get("use_when") or "").replace("\t", " ").replace("\n", " "),
                ]
            )
        )
    return "\n".join(lines)


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    allowed_statuses = {"active", "maintenance", "experimental", *LEGACY_STATUSES}
    for index, entry in enumerate(registry.get("entries", [])):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} is not an object")
            continue
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            errors.append(f"{prefix}.id is empty")
        elif entry_id in seen:
            errors.append(f"duplicate id: {entry_id}")
        seen.add(entry_id)
        for key in ("name", "official_url", "use_when"):
            if not str(entry.get(key) or "").strip():
                errors.append(f"{prefix}.{key} is empty")
        if not str(entry.get("official_url") or "").startswith("https://"):
            errors.append(f"{prefix}.official_url must be https")
        if not entry.get("categories"):
            errors.append(f"{prefix}.categories is empty")
        if not entry.get("platforms"):
            errors.append(f"{prefix}.platforms is empty")
        status = str(entry.get("status") or "active")
        if status not in allowed_statuses:
            errors.append(f"{prefix}.status is invalid: {status}")
    return errors


if __name__ == "__main__":  # pragma: no cover - use environment_audit catalog
    registry = load_registry()
    errors = validate_registry(registry)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(2)
    print(render_catalog(filter_entries(registry)))
