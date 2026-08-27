"""Restricted workspace APIs for the optional Research Workbench.

The browser never receives arbitrary filesystem access. Every path is confined
to a server-approved project or configured data root, symlink escapes are
rejected, and previews are size/type limited. These routes power the IDE,
literature index, paper watcher, and explicit final-review flow.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import mimetypes
import os
import selectors
import stat
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..attachments import _windows_guard_directory_chain, _windows_guard_path
from .context import ServerContext

_MAX_ENTRIES = 6_000
_MAX_DIRECTORY_ENTRIES = 2_000
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_RAW_BYTES = 64 * 1024 * 1024
_MAX_LITERATURE_BYTES = 32 * 1024 * 1024
_MAX_LITERATURE_RECORDS = 2_000
_SKIP_NAMES = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist"}
_HEAVY_NAMES = {"models", "hf_home", "pip-cache", "pip_cache", ".cache", "wandb"}
_TEXT_SUFFIXES = {
    ".bib", ".cfg", ".conf", ".css", ".csv", ".html", ".ini", ".ipynb", ".java", ".js", ".json",
    ".jsonl", ".jsx", ".log", ".md", ".markdown", ".mjs", ".py", ".rst", ".sh", ".sql", ".tex",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
_PRIORITY_NAMES = {"research", "paper", "technical_report", "review", "reviews", "src", "code", "scripts", "tests"}
_CACHE_LOCK = threading.Lock()
_TREE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LITERATURE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class FinalReviewIn(BaseModel):
    venue: str = Field(min_length=2, max_length=200)
    venue_type: str = Field(pattern="^(conference|journal|workshop)$")
    strictness: str = Field(pattern="^(preflight|standard|strict|red-team)$")
    manuscript_path: str = Field(default="", max_length=1_000)
    emphasis: list[str] = Field(default_factory=list, max_length=20)
    scope: str = Field(min_length=1, max_length=8_000)


def _allowed_bases() -> list[Path]:
    configured = [
        item
        for item in os.getenv("ARGUS_V2_ALLOWED_ROOTS", "").split(os.pathsep)
        if item.strip()
    ]
    bases: list[Path] = []
    for value in configured:
        try:
            base = Path(value).expanduser().resolve(strict=True)
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"configured workspace root unavailable: {value}") from exc
        if not base.is_dir():
            raise HTTPException(status_code=503, detail=f"configured workspace root is not a directory: {value}")
        bases.append(base)
    return bases


def _resolved_directory(raw: str) -> Path:
    candidate = Path(str(raw or "")).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=400, detail="workspace root must be an absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"workspace root unavailable: {exc}") from exc
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="workspace root is not a directory")
    return resolved


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_root(raw: str) -> Path:
    resolved = _resolved_directory(raw)
    bases = _allowed_bases()
    if not any(_inside(base, resolved) for base in bases):
        raise HTTPException(status_code=400, detail="workspace root is outside the allowed data roots")
    return resolved


def _workspace_profiles(ctx: ServerContext, sid: str) -> list[dict[str, Any]]:
    rows = ctx.machine_projects(limit=500, include_empty=True)
    profiles: dict[str, dict[str, Any]] = {}

    def add_profile(profile: dict[str, Any]) -> None:
        key = str(profile["path"])
        existing = profiles.get(key)
        if existing is None or (
            bool(profile["canonical"]) and not bool(existing["canonical"])
        ):
            profiles[key] = profile

    for row in rows:
        row_sid = str(row.get("id") or "").strip()
        raw_path = str(row.get("workdir") or row.get("launch_cwd") or "").strip()
        if not row_sid or not raw_path:
            continue
        try:
            # Project paths come from the authenticated server-side project
            # index, not from a browser-supplied absolute path.
            root = _resolved_directory(raw_path)
        except HTTPException:
            continue
        add_profile({
            "id": f"project:{row_sid}",
            "label": str(row.get("display_name") or row.get("label") or row_sid),
            "path": str(root),
            "source": "project",
            "project_sid": row_sid,
            "canonical": row_sid == sid,
        })
    # The running Argus source tree is a deliberate local profile used for its
    # technical report and frontend review; it is explicit rather than an
    # arbitrary browser-supplied absolute path.
    source_root = Path(__file__).resolve().parents[3]
    try:
        source_root = _safe_root(str(source_root))
        add_profile({
            "id": "system:argus-source",
            "label": "Argus source (local)",
            "path": str(source_root),
            "source": "system",
            "project_sid": "",
            "canonical": False,
        })
    except HTTPException:
        pass
    configured = os.getenv("ARGUS_V2_WORKSPACE_PROFILES", "").strip()
    if configured:
        try:
            extra_rows = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=503, detail="ARGUS_V2_WORKSPACE_PROFILES is not valid JSON") from exc
        if not isinstance(extra_rows, list):
            raise HTTPException(status_code=503, detail="ARGUS_V2_WORKSPACE_PROFILES must be a JSON list")
        for index, row in enumerate(extra_rows, start=1):
            if not isinstance(row, dict):
                continue
            try:
                root = _safe_root(str(row.get("path") or ""))
            except HTTPException:
                continue
            add_profile({
                "id": f"configured:{index}",
                "label": str(row.get("label") or root.name),
                "path": str(root),
                "source": "configured",
                "project_sid": "",
                "canonical": False,
            })
    return sorted(profiles.values(), key=lambda row: (not bool(row["canonical"]), str(row["label"]).casefold()))


def _approved_root(ctx: ServerContext, sid: str, workspace_id: str) -> Path:
    profile = next((row for row in _workspace_profiles(ctx, sid) if row["id"] == workspace_id), None)
    if profile is None:
        raise HTTPException(status_code=403, detail="workspace profile is not approved")
    return _resolved_directory(str(profile["path"]))


def _safe_target(root: Path, raw_path: str) -> Path:
    raw = str(raw_path or ".").replace("\\", "/")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="invalid workspace path")
    try:
        resolved = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"workspace path unavailable: {exc}") from exc
    if not _inside(root, resolved):
        raise HTTPException(status_code=400, detail="resolved path escapes workspace root")
    return resolved


def _open_workspace_root_fd(root: Path) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        current_fd = os.open(os.path.sep, directory_flags)
    except OSError as exc:
        raise HTTPException(status_code=400, detail="filesystem root is unavailable") from exc
    try:
        for part in root.parts[1:]:
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except OSError as exc:
                raise HTTPException(status_code=400, detail="workspace root changed or contains a symlink") from exc
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_confined_file(root: Path, raw_path: str) -> tuple[int, os.stat_result]:
    raw = str(raw_path or "").replace("\\", "/")
    relative = PurePosixPath(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise HTTPException(status_code=400, detail="invalid workspace file path")
    if os.name == "nt":
        return _open_confined_file_windows(root, relative)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    current_fd = _open_workspace_root_fd(root)
    try:
        for part in relative.parts[:-1]:
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except OSError as exc:
                raise HTTPException(status_code=400, detail=f"workspace directory unavailable: {part}") from exc
            os.close(current_fd)
            current_fd = next_fd
        try:
            file_fd = os.open(relative.parts[-1], file_flags, dir_fd=current_fd)
        except OSError as exc:
            raise HTTPException(status_code=400, detail="workspace file unavailable or symlinked") from exc
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_fd)
            raise HTTPException(status_code=400, detail="workspace path is not a regular file")
        return file_fd, info
    finally:
        os.close(current_fd)


def _stream_fd(fd: int, max_bytes: int, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    remaining = max(0, int(max_bytes))
    try:
        while remaining:
            chunk = os.read(fd, min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        os.close(fd)


def _read_confined_bytes(root: Path, path: str, max_bytes: int) -> bytes:
    fd, info = _open_confined_file(root, path)
    try:
        if info.st_size > max_bytes:
            raise HTTPException(status_code=413, detail=f"file exceeds {max_bytes} byte limit")
        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(fd, min(64 * 1024, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > max_bytes:
            raise HTTPException(status_code=413, detail=f"file exceeds {max_bytes} byte limit")
        return bytes(payload)
    finally:
        os.close(fd)


def _atomic_write_confined(root: Path, directory: str, name: str, payload: bytes) -> str:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid output filename")
    if os.name == "nt":
        return _atomic_write_confined_windows(root, directory, name, payload)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    current_fd = _open_workspace_root_fd(root)
    try:
        for part in PurePosixPath(directory).parts:
            if part in {"", ".", ".."}:
                raise HTTPException(status_code=400, detail="invalid output directory")
            try:
                os.mkdir(part, mode=0o755, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise HTTPException(status_code=409, detail="output directory unavailable") from exc
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except OSError as exc:
                raise HTTPException(status_code=409, detail="output directory is symlinked or unavailable") from exc
            os.close(current_fd)
            current_fd = next_fd
        temporary = f".{name}.tmp-{os.getpid()}-{time.time_ns()}"
        try:
            file_fd = os.open(temporary, file_flags, 0o600, dir_fd=current_fd)
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(file_fd, view)
                    view = view[written:]
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
            os.replace(temporary, name, src_dir_fd=current_fd, dst_dir_fd=current_fd)
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=current_fd)
            raise HTTPException(status_code=409, detail="failed to write review manifest") from exc
    finally:
        os.close(current_fd)
    return PurePosixPath(directory, name).as_posix()


def _windows_path_part(part: str, *, kind: str) -> str:
    if (
        not part
        or part in {".", ".."}
        or "/" in part
        or "\\" in part
        or ":" in part
        or part.endswith((" ", "."))
    ):
        raise HTTPException(status_code=400, detail=f"invalid {kind}")
    return part


def _open_confined_file_windows(
    root: Path,
    relative: PurePosixPath,
) -> tuple[int, os.stat_result]:
    """Open a Windows workspace file while rejecting links and junctions.

    Directory handles stay open without ``FILE_SHARE_DELETE`` until the file
    descriptor is acquired, so a verified component cannot be swapped during
    traversal. The returned descriptor pins the verified regular file after
    the guards close.
    """
    with contextlib.ExitStack() as stack:
        current = _windows_guard_directory_chain(root, stack)
        for raw_part in relative.parts[:-1]:
            part = _windows_path_part(raw_part, kind="workspace directory")
            current = current / part
            try:
                stack.enter_context(_windows_guard_path(current, directory=True))
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"workspace directory unavailable: {part}",
                ) from exc
        name = _windows_path_part(relative.parts[-1], kind="workspace file path")
        target = current / name
        try:
            stack.enter_context(_windows_guard_path(target, directory=False))
            descriptor = os.open(
                target,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOINHERIT", 0),
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="workspace file unavailable or symlinked",
            ) from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise HTTPException(
                    status_code=400,
                    detail="workspace path is not a regular file",
                )
            return descriptor, info
        except Exception:
            os.close(descriptor)
            raise


def _atomic_write_confined_windows(
    root: Path,
    directory: str,
    name: str,
    payload: bytes,
) -> str:
    name = _windows_path_part(name, kind="output filename")
    parts = PurePosixPath(str(directory or "").replace("\\", "/")).parts
    if not parts:
        raise HTTPException(status_code=400, detail="invalid output directory")
    temporary = f".{name}.tmp-{os.getpid()}-{time.time_ns()}"
    descriptor: int | None = None
    with contextlib.ExitStack() as stack:
        current = _windows_guard_directory_chain(root, stack)
        for raw_part in parts:
            part = _windows_path_part(raw_part, kind="output directory")
            child = current / part
            try:
                child.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise HTTPException(
                    status_code=409, detail="output directory unavailable"
                ) from exc
            try:
                stack.enter_context(_windows_guard_path(child, directory=True))
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail="output directory is symlinked or unavailable",
                ) from exc
            current = child
        target = current / name
        temporary_path = current / temporary
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOINHERIT", 0),
            )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary_path, target)
        except OSError as exc:
            raise HTTPException(
                status_code=409, detail="failed to write review manifest"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            with contextlib.suppress(FileNotFoundError):
                temporary_path.unlink()
    return PurePosixPath(directory, name).as_posix()


def _skip_dir(name: str) -> bool:
    return name in _SKIP_NAMES or name == ".venv" or name.startswith(".venv-")


def _scan_tree(root: Path) -> dict[str, Any]:
    cache_key = str(root)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _TREE_CACHE.get(cache_key)
        if cached and now - cached[0] < 3.0:
            return cached[1]
    entries: list[dict[str, Any]] = []
    truncated = False

    def walk(directory: Path, depth: int) -> None:
        nonlocal truncated
        if len(entries) >= _MAX_ENTRIES:
            truncated = True
            return
        try:
            bounded = list(itertools.islice(directory.iterdir(), _MAX_DIRECTORY_ENTRIES + 1))
            if len(bounded) > _MAX_DIRECTORY_ENTRIES:
                truncated = True
                bounded = bounded[:_MAX_DIRECTORY_ENTRIES]
            children = sorted(
                bounded,
                key=lambda path: (0 if path.name.lower() in _PRIORITY_NAMES else 1, path.name.casefold()),
            )
        except OSError:
            return
        for child in children:
            if len(entries) >= _MAX_ENTRIES:
                truncated = True
                return
            try:
                info = child.lstat()
            except OSError:
                continue
            relative = child.relative_to(root).as_posix()
            suffix = child.suffix.lower()
            is_reparse = bool(
                int(getattr(info, "st_file_attributes", 0))
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if child.is_symlink() or is_reparse:
                kind = "symlink"
                skipped = False
            elif child.is_dir():
                kind = "directory"
                skipped = _skip_dir(child.name) or child.name in _HEAVY_NAMES or depth >= 11
            elif child.is_file():
                kind = "file"
                skipped = False
            else:
                continue
            row = {
                "path": relative,
                "name": child.name,
                "type": kind,
                "size": int(info.st_size) if kind != "directory" else 0,
                "mtime": float(info.st_mtime),
                "extension": suffix if kind == "file" else "",
            }
            if skipped:
                row["skipped"] = True
            entries.append(row)
            if kind == "directory" and not skipped:
                walk(child, depth + 1)

    walk(root, 0)
    result = {"root": str(root), "entries": entries, "truncated": truncated}
    with _CACHE_LOCK:
        _TREE_CACHE[cache_key] = (time.monotonic(), result)
        if len(_TREE_CACHE) > 32:
            oldest = min(_TREE_CACHE, key=lambda key: _TREE_CACHE[key][0])
            _TREE_CACHE.pop(oldest, None)
    return result


def _git(root: Path, *args: str, max_bytes: int = 2 * 1024 * 1024) -> str:
    safe_env = os.environ.copy()
    safe_env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    })
    argv = [
        "git",
        "-c", "core.fsmonitor=false",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "diff.external=",
        "-c", "color.ui=false",
        "-C", str(root),
        *args,
    ]
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    payload = bytearray()
    truncated = False
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=safe_env)
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            for key, _mask in selector.select(timeout=.1):
                try:
                    chunk = os.read(key.fileobj.fileno(), min(64 * 1024, max_bytes + 1 - len(payload)))
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    payload.extend(chunk)
                    if len(payload) > max_bytes:
                        truncated = True
                        process.kill()
                        break
            if truncated:
                break
            if process.poll() is not None:
                while len(payload) <= max_bytes:
                    try:
                        chunk = os.read(process.stdout.fileno(), min(64 * 1024, max_bytes + 1 - len(payload)))
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    payload.extend(chunk)
                truncated = len(payload) > max_bytes
                break
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=.5)
    except (OSError, subprocess.SubprocessError):
        if process is not None:
            with contextlib.suppress(Exception):
                process.kill()
        return ""
    finally:
        selector.close()
    if process.returncode not in {0, -9} and not truncated:
        return ""
    text = bytes(payload[:max_bytes]).decode("utf-8", errors="replace")
    return text + ("\n… output truncated\n" if truncated else "")


def _sanitize_remote(value: str) -> str:
    remote = str(value or "").strip()
    if remote.startswith(("http://", "https://")) and "@" in remote:
        scheme, rest = remote.split("://", 1)
        remote = f"{scheme}://{rest.split('@', 1)[1]}"
    return remote


def _github_status() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["gh", "auth", "status", "--json", "hosts"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        payload = json.loads(result.stdout or "{}")
        rows = payload.get("hosts", {}).get("github.com", [])
        active = next((row for row in rows if row.get("active")), rows[0] if rows else None)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        active = None
    if not isinstance(active, dict):
        return {"authenticated": False, "host": "github.com", "login": "", "protocol": "", "scopes": []}
    return {
        "authenticated": active.get("state") == "success",
        "host": str(active.get("host") or "github.com"),
        "login": str(active.get("login") or ""),
        "protocol": str(active.get("gitProtocol") or ""),
        "scopes": [item.strip() for item in str(active.get("scopes") or "").split(",") if item.strip()],
    }


def _first_string(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value and isinstance(value[0], str):
            return str(value[0]).strip()
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return []


def _verified_evidence(root: Path, row: dict[str, Any]) -> tuple[str, int]:
    for key in ("raw_response_path", "source_artifact", "live_evidence_path"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        raw = value.strip()
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                raw = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
        try:
            payload = _read_confined_bytes(root, raw, 12 * 1024 * 1024)
        except HTTPException:
            continue
        if not payload.strip():
            continue
        return PurePosixPath(raw).as_posix(), len(payload)
    return "", 0


def _paper(root: Path, row: dict[str, Any], source_path: str, index: int) -> dict[str, Any] | None:
    title = _first_string(row, ["title", "paper_title", "name"])
    if len(title) < 4:
        return None
    url = _first_string(row, ["url", "publication_url", "primary_urls", "official_url", "query_url"])
    year: int | None = None
    try:
        raw_year = int(row.get("year") or row.get("publication_year") or 0)
        if 1900 < raw_year < 2200:
            year = raw_year
    except (TypeError, ValueError):
        pass
    topics = _string_list(row.get("topic_tags")) + _string_list(row.get("topics")) + _string_list(row.get("topic"))
    evidence_path, evidence_bytes = _verified_evidence(root, row)
    evidence = "verified_artifact" if evidence_path else "metadata" if url else "unresolved"
    return {
        "id": str(row.get("key") or row.get("citation_key") or row.get("id") or f"{source_path}:{index}"),
        "title": title,
        "authors": _string_list(row.get("authors") or row.get("author")),
        "year": year,
        "venue": _first_string(row, ["venue", "journal", "conference"]),
        "url": url,
        "abstract": _first_string(row, ["abstract", "paper_relevant_summary", "summary", "primary_claim_basis"]),
        "relevance": _first_string(row, ["relevance", "implication", "novelty_implication", "paper_relevant_summary", "note"]),
        "topics": list(dict.fromkeys(topics))[:12],
        "sourcePath": source_path,
        "retrievedAt": _first_string(row, ["retrieved_utc", "retrieved_at_utc", "retrieved_at", "last_verified_at_utc"]),
        "evidenceStatus": evidence,
        "evidencePath": evidence_path,
        "evidenceBytes": evidence_bytes,
    }


def _literature(root: Path) -> dict[str, Any]:
    cache_key = str(root)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _LITERATURE_CACHE.get(cache_key)
        if cached and now - cached[0] < 10.0:
            return cached[1]
    tree = _scan_tree(root)
    files = [entry for entry in tree["entries"] if entry["type"] == "file"]
    sources = [
        entry for entry in files
        if any(token in entry["path"].lower() for token in ("literature", "related_work", "related-work", "nearest_work", "prior_work", "citation", "bibliograph"))
        or entry["extension"] == ".bib"
    ]
    papers: list[dict[str, Any]] = []
    parsed_bytes = 0
    literature_truncated = False
    for entry in sources:
        if len(papers) >= _MAX_LITERATURE_RECORDS:
            literature_truncated = True
            break
        if entry["extension"] != ".json" or entry["size"] > 12 * 1024 * 1024:
            continue
        if parsed_bytes + int(entry["size"]) > _MAX_LITERATURE_BYTES:
            literature_truncated = True
            continue
        parsed_bytes += int(entry["size"])
        try:
            payload = json.loads(_read_confined_bytes(root, entry["path"], 12 * 1024 * 1024).decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("papers", "core_works", "entries", "retained_urls", "works", "references"):
            rows = payload.get(key)
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if len(papers) >= _MAX_LITERATURE_RECORDS:
                    literature_truncated = True
                    break
                if not isinstance(row, dict):
                    continue
                normalized = _paper(root, row, entry["path"], index)
                if normalized:
                    papers.append(normalized)
    deduped: dict[str, dict[str, Any]] = {}
    for paper in papers:
        key = str(paper["url"] or paper["title"]).casefold().strip()
        existing = deduped.get(key)
        if existing is None or len(str(paper["abstract"])) + len(str(paper["relevance"])) > len(str(existing["abstract"])) + len(str(existing["relevance"])):
            deduped[key] = paper
    ordered = sorted(deduped.values(), key=lambda row: (-(row["year"] or 0), str(row["title"]).casefold()))
    search_files = sorted(
        [entry for entry in files if "/_search/" in f"/{entry['path'].lower()}/" or "/literature_sources/" in f"/{entry['path'].lower()}/" or any(token in entry["name"].lower() for token in ("arxiv", "semantic_scholar", "crossref"))],
        key=lambda row: -float(row["mtime"]),
    )[:160]
    result = {"papers": ordered, "sourceFiles": sources[:500], "searchFiles": search_files, "scannedFiles": len(files), "truncated": literature_truncated, "parsedBytes": parsed_bytes}
    with _CACHE_LOCK:
        _LITERATURE_CACHE[cache_key] = (time.monotonic(), result)
        if len(_LITERATURE_CACHE) > 32:
            oldest = min(_LITERATURE_CACHE, key=lambda key: _LITERATURE_CACHE[key][0])
            _LITERATURE_CACHE.pop(oldest, None)
    return result


def _latest_manuscript(root: Path) -> str:
    candidates = [
        entry for entry in _scan_tree(root)["entries"]
        if entry["type"] == "file"
        and entry["extension"] in {".tex", ".md", ".pdf"}
        and any(part in {"paper", "manuscript", "technical_report"} for part in PurePosixPath(entry["path"]).parts)
        and "evidence" not in PurePosixPath(entry["path"]).parts
    ]
    def rank(entry: dict[str, Any]) -> tuple[int, float]:
        name = str(entry["name"]).casefold()
        priority = 0 if name in {"main.tex", "paper.pdf", "manuscript.pdf", "argus-technical-report.pdf"} else 1 if entry["extension"] in {".pdf", ".tex"} else 2
        return priority, -float(entry["mtime"])
    return str(sorted(candidates, key=rank)[0]["path"]) if candidates else ""


def register_workspace_v2_routes(app, ctx: ServerContext, server_mod) -> None:
    dependencies = [Depends(ctx.require_auth)]

    @app.get("/api/v2/workspaces", dependencies=dependencies)
    def workspace_profiles(sid: str = Query(..., min_length=1)) -> dict[str, Any]:
        profiles = _workspace_profiles(ctx, sid)
        return {"profiles": profiles, "default_id": next((row["id"] for row in profiles if row["canonical"]), profiles[0]["id"] if profiles else "")}

    def approved(sid: str, workspace_id: str) -> Path:
        return _approved_root(ctx, sid, workspace_id)

    @app.get("/api/v2/workspace/tree", dependencies=dependencies)
    def workspace_tree(sid: str = Query(...), workspace_id: str = Query(...)) -> dict[str, Any]:
        return _scan_tree(approved(sid, workspace_id))

    @app.get("/api/v2/workspace/file", dependencies=dependencies)
    def workspace_file(sid: str = Query(...), workspace_id: str = Query(...), path: str = Query(...)) -> dict[str, Any]:
        workspace = approved(sid, workspace_id)
        _safe_target(workspace, path)
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in _TEXT_SUFFIXES:
            raise HTTPException(status_code=415, detail="file is not a supported text preview")
        fd, info = _open_confined_file(workspace, path)
        try:
            if info.st_size > _MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail=f"file exceeds {_MAX_FILE_BYTES} byte preview limit")
            raw = bytearray()
            while len(raw) <= _MAX_FILE_BYTES:
                chunk = os.read(fd, min(64 * 1024, _MAX_FILE_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
        finally:
            os.close(fd)
        try:
            content = bytes(raw).decode("utf-8")
        except UnicodeError as exc:
            raise HTTPException(status_code=415, detail="binary file preview is blocked") from exc
        if "\x00" in content:
            raise HTTPException(status_code=415, detail="binary file preview is blocked")
        return {"root": str(workspace), "workspace_id": workspace_id, "path": path, "content": content, "size": info.st_size, "mtime": info.st_mtime, "extension": suffix}

    @app.get("/api/v2/workspace/raw", dependencies=dependencies)
    def workspace_raw(sid: str = Query(...), workspace_id: str = Query(...), path: str = Query(...)):
        workspace = approved(sid, workspace_id)
        suffix = PurePosixPath(path).suffix.lower()
        allowed = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".svg"}
        if suffix not in allowed:
            raise HTTPException(status_code=415, detail="raw preview type is not allowed")
        fd, info = _open_confined_file(workspace, path)
        if info.st_size > _MAX_RAW_BYTES:
            os.close(fd)
            raise HTTPException(status_code=413, detail=f"raw preview exceeds {_MAX_RAW_BYTES} byte limit")
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return StreamingResponse(
            _stream_fd(fd, info.st_size),
            media_type=mime,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Length": str(info.st_size)},
        )

    @app.get("/api/v2/workspace/git", dependencies=dependencies)
    def workspace_git(sid: str = Query(...), workspace_id: str = Query(...)) -> dict[str, Any]:
        workspace = approved(sid, workspace_id)
        github = _github_status()
        identity = {
            "name": _git(workspace, "config", "--get", "user.name", max_bytes=16_000).strip(),
            "email": _git(workspace, "config", "--get", "user.email", max_bytes=16_000).strip(),
        }
        identity["valid"] = bool(identity["name"] and identity["email"] and not identity["email"].casefold().endswith((".invalid", "@invalid")))
        if not (workspace / ".git").exists():
            return {"available": False, "branch": "", "status": "", "stat": "", "diff": "", "log": "", "remotes": [], "upstream": "", "ahead": 0, "behind": 0, "identity": identity, "github": github, "publish_ready": False}
        remote_names = [name for name in _git(workspace, "remote", max_bytes=64_000).splitlines() if name.strip()]
        remotes = [{
            "name": name,
            "fetch": _sanitize_remote(_git(workspace, "remote", "get-url", name, max_bytes=64_000).strip()),
            "push": _sanitize_remote(_git(workspace, "remote", "get-url", "--push", name, max_bytes=64_000).strip()),
        } for name in remote_names]
        upstream = _git(workspace, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", max_bytes=16_000).strip()
        ahead = behind = 0
        if upstream:
            counts = _git(workspace, "rev-list", "--left-right", "--count", f"HEAD...{upstream}", max_bytes=16_000).strip().split()
            if len(counts) == 2 and all(item.isdigit() for item in counts):
                ahead, behind = int(counts[0]), int(counts[1])
        status_text = _git(workspace, "status", "--short", max_bytes=128_000)
        stat_text = _git(workspace, "diff", "--no-ext-diff", "--no-textconv", "--stat", max_bytes=128_000)
        staged = _git(workspace, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--unified=2")
        unstaged = _git(workspace, "diff", "--no-ext-diff", "--no-textconv", "--unified=2")
        return {
            "available": True,
            "branch": _git(workspace, "branch", "--show-current", max_bytes=16_000).strip(),
            "status": status_text,
            "stat": stat_text,
            "diff": (staged + "\n" + unstaged).strip(),
            "log": _git(workspace, "log", "-20", "--date=iso-strict", "--pretty=format:%H%x09%ad%x09%an%x09%s", max_bytes=128_000),
            "remotes": remotes,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "identity": identity,
            "github": github,
            "publish_ready": bool(remotes and upstream and identity["valid"] and github["authenticated"]),
            "truncated": "… output truncated" in staged or "… output truncated" in unstaged,
        }

    @app.get("/api/v2/workspace/literature", dependencies=dependencies)
    def workspace_literature(sid: str = Query(...), workspace_id: str = Query(...)) -> dict[str, Any]:
        return _literature(approved(sid, workspace_id))

    @app.post("/api/projects/{sid}/reviews/final", dependencies=dependencies)
    async def request_final_review(sid: str, body: FinalReviewIn) -> dict[str, Any]:
        if body.manuscript_path and PurePosixPath(body.manuscript_path).suffix.lower() not in {".tex", ".md", ".pdf"}:
            raise HTTPException(status_code=415, detail="final manuscript must be TeX, Markdown, or PDF")
        profiles = _workspace_profiles(ctx, sid)
        canonical = next((row for row in profiles if row["canonical"]), None)
        if canonical is None:
            raise HTTPException(status_code=409, detail="project has no canonical approved workspace")
        workspace = _resolved_directory(str(canonical["path"]))
        manuscript_path = body.manuscript_path or _latest_manuscript(workspace)
        if not manuscript_path:
            raise HTTPException(status_code=409, detail="no final manuscript was found in the approved project workspace")
        suffix = PurePosixPath(manuscript_path).suffix.lower()
        if suffix not in {".tex", ".md", ".pdf"}:
            raise HTTPException(status_code=415, detail="final manuscript must be TeX, Markdown, or PDF")
        fd, _info = _open_confined_file(workspace, manuscript_path)
        os.close(fd)
        created_at = time.time()
        request_id = f"fr-{time.time_ns()}"
        report_path = f"reviews/final_review_{request_id}.md"
        manifest = {
            "schema_version": 1,
            "review_kind": "final",
            "status": "queued",
            "sid": sid,
            "venue": body.venue.strip(),
            "venue_type": body.venue_type,
            "strictness": body.strictness,
            "request_id": request_id,
            "manuscript_path": manuscript_path,
            "emphasis": list(dict.fromkeys(item.strip() for item in body.emphasis if item.strip())),
            "scope": body.scope.strip(),
            "created_at": created_at,
            "report_path": report_path,
        }
        manifest_name = f"final_review_request_{request_id}.json"
        manifest_relative = _atomic_write_confined(workspace, "reviews", manifest_name, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        task_text = (
            "[FINAL_REVIEW]\n"
            + json.dumps(manifest, ensure_ascii=False, indent=2)
            + "\n\nExecute an independent completion-stage simulated review. Read the selected final manuscript, "
            "literature, experiments, negative results, and process-review history. Do not fabricate evidence. "
            f"Write the report to exactly {report_path} with desk/scope checks, summary, strengths, fatal "
            "issues, repairable issues, emphasized dimensions, missing baselines/ablations/statistics, author "
            "questions, score/confidence, and a prioritized revision checklist. Do not submit or publish."
        )
        project_root = ctx.project_root_or_404(sid)
        try:
            dispatch = await run_in_threadpool(
                server_mod.enqueue_task_command,
                sid,
                task_text,
                autostart_daemon=True,
                global_root=project_root,
                lifecycle_root=server_mod._global_root(ctx.global_root),
            )
        except Exception:
            manifest["status"] = "dispatch_failed"
            _atomic_write_confined(workspace, "reviews", manifest_name, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            raise
        return {"ok": True, "request_id": request_id, "manifest_path": manifest_relative, "report_path": report_path, "manifest": manifest, "dispatch": dispatch}

    @app.get("/api/projects/{sid}/reviews/final/{request_id}", dependencies=dependencies)
    def final_review_status(sid: str, request_id: str) -> dict[str, Any]:
        if not request_id.startswith("fr-") or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for char in request_id):
            raise HTTPException(status_code=400, detail="invalid final review request id")
        canonical = next((row for row in _workspace_profiles(ctx, sid) if row["canonical"]), None)
        if canonical is None:
            raise HTTPException(status_code=404, detail="project workspace unavailable")
        workspace = _resolved_directory(str(canonical["path"]))
        manifest_path = f"reviews/final_review_request_{request_id}.json"
        try:
            manifest = json.loads(_read_confined_bytes(workspace, manifest_path, 512 * 1024).decode("utf-8"))
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="final review request not found") from exc
        report_path = str(manifest.get("report_path") or "")
        report_exists = False
        if report_path:
            try:
                fd, _info = _open_confined_file(workspace, report_path)
            except HTTPException:
                pass
            else:
                os.close(fd)
                report_exists = True
        return {
            "request_id": request_id,
            "status": "completed" if report_exists else str(manifest.get("status") or "queued"),
            "manifest": manifest,
            "report_path": report_path,
            "report_exists": report_exists,
        }
