"""A synthetic ``argus-verticals`` release in a temporary directory.

The store reads a ``catalog.json`` and one zip per vertical; this helper builds
both from a few dicts so unit tests never touch the network or the community
repository. ``ARGUS_VERTICAL_CATALOG`` pointed at the written catalog makes the
store treat the directory as an offline mirror: archives next to the catalog
are used before their (fake) https URLs.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

STAGES_TEMPLATE = '''"""Synthetic vertical {name} for store tests."""
from argus.skills.stage_machine import ChecklistItem

ARGUS_VERTICAL_API_VERSION = {api_version}
VERTICAL_PURPOSE = "synthetic vertical {name}{marker}"
VERTICAL_SKILL_PARENTS = {parents!r}
CHECKLIST_STAGE_ORDER = ("work", "deliver")
CHECKLIST_ITEMS = {{
    "work": (ChecklistItem("work.output", "Work output exists", "work artifact"),),
    "deliver": (ChecklistItem("deliver.output", "Delivery is complete", "delivery artifact"),),
}}
completion_gate = "none"
MARKER = "{marker}"
'''

REPO_URL = "https://github.com/Argus-AiTeam/argus-verticals/releases/download/{tag}/{file}"


def spec(
    name: str,
    *,
    version: str = "0.1.0",
    requires: tuple[str, ...] = (),
    parents: tuple[str, ...] = (),
    shared: tuple[str, ...] = (),
    python_requirements: tuple[str, ...] = (),
    purpose_zh: str | None = None,
    marker: str = "",
    api_version: int = 1,
    skills: bool = True,
    extra_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name, "version": version, "requires": tuple(requires), "parents": tuple(parents),
        "shared": tuple(shared), "python_requirements": tuple(python_requirements),
        "purpose_zh": purpose_zh, "marker": marker, "api_version": api_version, "skills": skills,
        "extra_files": dict(extra_files or {}),
    }


def archive_members(item: dict[str, Any]) -> dict[str, str]:
    name = item["name"]
    members = {
        f"argus_verticals/{name}/__init__.py": "",
        f"argus_verticals/{name}/stages.py": STAGES_TEMPLATE.format(
            name=name, parents=tuple(item["parents"]), marker=item["marker"], api_version=item["api_version"],
        ),
    }
    if item["skills"]:
        members[f"argus_verticals/{name}/skills/engineer/{name}.md"] = (
            f"---\nname: {name}\ndescription: {name} skill{item['marker']}\n---\n"
        )
    for tree in item["shared"]:
        members[f"{tree}/__init__.py"] = f"SHARED = {item['marker']!r}\n"
    members.update(item["extra_files"])
    return members


def zip_bytes(members: dict[str, bytes | str], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member, content in sorted(members.items()):
            info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            zf.writestr(info, content.encode("utf-8") if isinstance(content, str) else content)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def catalog_entry(item: dict[str, Any], data: bytes, *, tag: str, url: str | None = None) -> dict[str, Any]:
    name = item["name"]
    file_name = f"{name}-{item['version']}.zip"
    return {
        "name": name,
        "version": item["version"],
        "module": f"argus_verticals.{name}.stages",
        "purpose": f"synthetic vertical {name}{item['marker']}",
        **({"purpose_zh": item["purpose_zh"]} if item["purpose_zh"] else {}),
        "paths": [f"argus_verticals/{name}"],
        "requires": list(item["requires"]),
        "shared": list(item["shared"]),
        "python_requirements": list(item["python_requirements"]),
        "tags": ["synthetic", "test"],
        "skill_parents": list(item["parents"]),
        "has_skills": bool(item["skills"]),
        "size_bytes": sum(len(v) for v in archive_members(item).values()),
        "api_version": item["api_version"],
        "min_argus": "test",
        "maintainers": [],
        "archive": {
            "file": file_name,
            "url": url or REPO_URL.format(tag=tag, file=file_name),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        },
    }


def write_catalog(dist: Path, entries: dict[str, dict[str, Any]], *, tag: str = "vtest") -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    catalog = {
        "schema": 1,
        "generated_from": {"repo": "Argus-AiTeam/argus-verticals", "commit": "0" * 40},
        "release": {"tag": tag},
        "verticals": entries,
    }
    path = dist / "catalog.json"
    path.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_release(dist: Path, items: list[dict[str, Any]], *, tag: str = "vtest") -> Path:
    """Write one zip per item plus ``catalog.json``; returns the catalog path."""
    dist.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, Any]] = {}
    for item in items:
        data = zip_bytes(archive_members(item))
        (dist / f"{item['name']}-{item['version']}.zip").write_bytes(data)
        entries[item["name"]] = catalog_entry(item, data, tag=tag)
    return write_catalog(dist, entries, tag=tag)


def write_raw_archive(
    dist: Path, item: dict[str, Any], members: dict[str, bytes | str], *, symlink: str | None = None,
    corrupt: bool = False, wrong_sha: bool = False,
) -> dict[str, Any]:
    """An archive with arbitrary members; the catalog entry describes the bytes as written."""
    data = zip_bytes(members, symlink=symlink)
    if corrupt:
        data = data[: len(data) // 2] + b"\0" * (len(data) - len(data) // 2)
    (dist / f"{item['name']}-{item['version']}.zip").write_bytes(data)
    entry = catalog_entry(item, data, tag="vtest")
    if wrong_sha:
        entry["archive"]["sha256"] = "0" * 64
    return entry


# --- the real community release, built offline from a copy of the repository ---

COMMUNITY_REPO_ENV = "ARGUS_VERTICALS_REPO"
DEFAULT_COMMUNITY_REPO = Path("/data/v-boxiuli/argus-verticals")


def community_repo() -> Path | None:
    candidate = Path(os.environ.get(COMMUNITY_REPO_ENV) or DEFAULT_COMMUNITY_REPO)
    return candidate if (candidate / "scripts" / "build_catalog.py").is_file() else None


def build_community_release(destination: Path, *, tag: str = "vtest") -> Path | None:
    """Copy the community repo and run its own ``build_catalog.py --release``; None when absent."""
    repo = community_repo()
    if repo is None:
        return None
    copy = destination / "repo"
    shutil.copytree(
        repo, copy,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", ".venv"),
    )
    dist = destination / "dist"
    subprocess.run(
        [sys.executable, str(copy / "scripts" / "build_catalog.py"), "--release", tag, "--dist", str(dist)],
        check=True, capture_output=True, text=True, cwd=copy,
    )
    return dist / "catalog.json"
