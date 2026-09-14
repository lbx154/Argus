"""Read-only browsing of the Skill sources used by the runtime."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Any

import yaml

from ..core import paths
from .builtins import builtin_skill_source_path, vertical_skill_source_path
from .layered import shared_skill_scope_dir
from .store import role_of_path
from .vertical_select import VERTICALS, resolve_skill_scope

SCOPES = ("global", "vertical", "project")
MAX_DOCUMENT_BYTES = 256 * 1024


@dataclass(frozen=True)
class Library:
    id: str
    scope: str
    root: Path
    source: str
    vertical: str = ""


def _inside(path: Path, owner: Path) -> bool:
    try:
        return path.resolve().is_relative_to(owner.resolve())
    except (OSError, RuntimeError):
        return False


def library_roots(
    global_root: Path, project_state: Path | None = None, workdir: Path | None = None,
) -> list[Library]:
    shared = Path(os.environ.get("ARGUS_SKILL_SKILLS_DIR") or paths.shared_skills_root(global_root)).expanduser()
    result = [
        Library("global:bundled", "global", builtin_skill_source_path(), "bundled"),
    ]
    shared_allowed = bool(os.environ.get("ARGUS_SKILL_SKILLS_DIR")) or _inside(shared, global_root)
    if shared_allowed:
        result.append(Library("global:shared", "global", shared, "shared"))
    for vertical in VERTICALS:
        result.append(Library(f"vertical:{vertical}:bundled", "vertical", vertical_skill_source_path(vertical), "bundled", vertical))
    domains = builtin_skill_source_path().parent / "domains"
    if domains.is_dir():
        for domain in sorted(domains.iterdir()):
            if domain.is_dir() and not domain.name.startswith((".", "_")):
                result.append(Library(f"domain:{domain.name}:bundled", "vertical", domain / "skills", "bundled", domain.name))
    shared_verticals = shared / "_shared_verticals"
    if shared_allowed and _inside(shared_verticals, shared) and shared_verticals.is_dir():
        for vertical in sorted(shared_verticals.iterdir()):
            if vertical.is_dir() and _inside(vertical, shared_verticals) and not vertical.name.startswith((".", "_")):
                result.append(Library(f"vertical:{vertical.name}:shared", "vertical", vertical, "shared", vertical.name))
    if workdir is not None:
        active = resolve_skill_scope(workdir)
        active_root = shared_skill_scope_dir(shared, active)
        if (shared_allowed and active_root is not None and _inside(active_root, shared)
                and not any(library.root == active_root for library in result)):
            result.append(Library(f"vertical:{active}:shared", "vertical", active_root, "shared", active))
    if project_state is not None:
        project = Path(os.environ.get("ARGUS_SKILL_PROJECT_SKILLS_DIR") or project_state / "skills").expanduser()
        if os.environ.get("ARGUS_SKILL_PROJECT_SKILLS_DIR") or _inside(project, project_state):
            result.append(Library("project:learned", "project", project, "project"))
    if workdir is not None:
        native = workdir / ".agents" / "skills"
        if _inside(native, workdir):
            result.append(Library("project:native", "project", native, "native"))
    return result


def _skill_path(relative: Path, *, native: bool = False) -> bool:
    return (
        not relative.is_absolute()
        and relative.suffix.lower() == ".md"
        and relative.name.lower() != "index.md"
        and not any(part.startswith((".", "_")) for part in relative.parts)
        and (not native or relative.name == "SKILL.md")
    )


def _header(text: str) -> tuple[str, str, str]:
    if not text.startswith("---\n"):
        return "", "", text
    delimiter = re.search(r"^---\s*$", text[4:], re.MULTILINE)
    if delimiter is None:
        return "", "", text
    front, body = text[4:][:delimiter.start()], text[4:][delimiter.end():]
    try:
        metadata = yaml.safe_load(front)
    except (yaml.YAMLError, RecursionError):
        return "", "", text
    if not isinstance(metadata, dict):
        return "", "", text
    name, description = metadata.get("name"), metadata.get("description")
    return (
        name.strip() if isinstance(name, str) else "",
        description.strip() if isinstance(description, str) else "",
        body.lstrip("\r\n"),
    )


def catalog(libraries: list[Library], *, active_vertical: str = "") -> dict[str, Any]:
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    common = next(library.root for library in libraries if library.id == "global:bundled")
    errors: list[str] = []
    defaults = {(library.scope, library.vertical): library.root for library in libraries if library.source == "bundled"}
    for library in libraries:
        if not library.root.is_dir():
            continue
        for path in sorted(library.root.rglob("*.md")):
            relative = path.relative_to(library.root)
            if not _skill_path(relative, native=library.source == "native"):
                continue
            if not _inside(path, library.root):
                continue
            try:
                resolved = path.resolve()
                stat = resolved.stat()
                if not S_ISREG(stat.st_mode):
                    continue
                # Seeding a vertical also copies common defaults. Do not count
                # those unchanged copies as new vertical-specific knowledge.
                base = common / relative
                if (library.scope == "vertical" and library.source == "shared"
                        and base.is_file() and stat.st_size <= MAX_DOCUMENT_BYTES
                        and stat.st_size == base.stat().st_size
                        and resolved.read_bytes() == base.read_bytes()):
                    continue
                default_root = defaults.get((library.scope, library.vertical))
                baseline = default_root / relative if default_root else None
                is_default = library.source == "bundled" or bool(
                    baseline and baseline.is_file() and stat.st_size <= MAX_DOCUMENT_BYTES
                    and stat.st_size == baseline.stat().st_size
                    and resolved.read_bytes() == baseline.read_bytes()
                )
                with resolved.open(encoding="utf-8-sig") as handle:
                    name, description, _ = _header(handle.read(8192))
            except (OSError, UnicodeError):
                errors.append(f"Cannot read {library.id}/{relative.as_posix()}")
                continue
            key = (library.scope, library.vertical, relative.as_posix())
            if library.source == "native":
                key = (library.scope, "native", relative.as_posix())
            rows[key] = {
                "library": library.id, "scope": library.scope,
                "vertical": library.vertical, "source": library.source,
                "path": relative.as_posix(),
                "name": name or relative.with_suffix("").as_posix(),
                "description": description,
                "role": role_of_path(path, library.root),
                "is_default": is_default,
                "updated_at": None if is_default else stat.st_mtime,
            }
    entries = sorted(rows.values(), key=lambda row: (SCOPES.index(row["scope"]), row["vertical"], row["name"].casefold()))
    return {"scopes": list(SCOPES), "items": entries,
            "verticals": sorted({library.vertical for library in libraries if library.vertical}),
            "active_vertical": active_vertical, "errors": errors}


def read_document(libraries: list[Library], library_id: str, relative_path: str) -> dict[str, Any]:
    library = next((item for item in libraries if item.id == library_id), None)
    relative = Path(relative_path)
    if (library is None or "\\" in relative_path or "\x00" in relative_path
            or not _skill_path(relative, native=library.source == "native")):
        raise FileNotFoundError("Unknown Skill document")
    candidate = library.root / relative
    if not _inside(candidate, library.root):
        raise FileNotFoundError("Unknown Skill document")
    path = candidate.resolve()
    if not path.is_file():
        raise FileNotFoundError("Unknown Skill document")
    with path.open("rb") as handle:
        raw = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Skill document exceeds the 256 KiB preview limit")
    markdown = raw.decode("utf-8-sig").replace("\r\n", "\n")
    name, description, content = _header(markdown)
    return {"name": name or relative.with_suffix("").as_posix(),
            "description": description, "content": content, "markdown": markdown,
            "path": relative.as_posix(), "source": library.source, "scope": library.scope,
            "vertical": library.vertical, "role": role_of_path(path, library.root)}
