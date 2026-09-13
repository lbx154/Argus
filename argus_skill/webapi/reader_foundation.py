"""Explicit question requests and their persistent Markdown reading artifacts.

Each request is registered in its own session before generation. Reads never
generate, and the registered workspace stays fixed when the campaign moves.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from ..core.file_lock import exclusive_file_lock
from ..core.process_identity import capture_process_identity, process_identity_is_running
from ..life.memory import Backlog
from .map_model import MapProgress, resolve_map_model, run_map_model
from .project_state import project_life_dir

FOUNDATION_VERSION = 1
GENERATION_SECONDS = 180
MANIFEST_DIRECTORY = "reader-foundations"
ARTIFACT_DIRECTORY = "reader-notes"


class FoundationConflict(ValueError):
    """The request ID already names a different explicit user request."""


def _request_id(value: str) -> str:
    return str(UUID(str(value)))


def foundation_id_from_path(value: str) -> str | None:
    parts = PurePosixPath(str(value).replace("\\", "/")).parts
    if len(parts) != 3 or parts[0] != ARTIFACT_DIRECTORY or not parts[2].endswith(".md"):
        return None
    try:
        request_id = _request_id(parts[2][:-3])
    except (ValueError, TypeError, AttributeError):
        return None
    return request_id if parts[2] == request_id + ".md" else None


def _record(life_dir: Path, request_id: str) -> dict[str, Any] | None:
    path = life_dir / MANIFEST_DIRECTORY / (request_id + ".json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(value, dict) or value.get("id") != request_id
        or value.get("path") != f"{ARTIFACT_DIRECTORY}/{life_dir.name}/{request_id}.md"
        or value.get("state") not in {"generating", "complete", "failed"}
        or not isinstance(value.get("workspace"), str)
        or not Path(value["workspace"]).is_absolute()
    ):
        raise ValueError("invalid question foundation record")
    # Deadline passage is only an observation: a live worker may be waiting for
    # provider termination or saving its receipt. Only a confirmed dead owner
    # can establish interruption on read. Start ticks distinguish PID reuse.
    if value["state"] == "generating":
        owner = value.get("owner")
        if isinstance(owner, dict) and not process_identity_is_running(owner.get("pid"), owner):
            value = {**value, "state": "failed", "error": "generation_owner_terminated"}
        elif time.time() > value["deadline_at"]:
            value = {**value, "deadline_exceeded": True}
    return value


def _save_record(life_dir: Path, record: dict[str, Any]) -> None:
    # Reuse the existing confined atomic writer, including its POSIX/Windows
    # implementations; no client-supplied directory or file contents are used.
    from .routes.workspace_v2 import _atomic_write_confined

    _atomic_write_confined(
        life_dir, MANIFEST_DIRECTORY, record["id"] + ".json",
        (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def read_foundation(global_root: Path, sid: str, request_id: str) -> dict[str, Any] | None:
    """Read one registered request, including complete Markdown when available."""
    from .artifacts import safe_artifact_path

    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    try:
        record = _record(life_dir, _request_id(request_id))
    except (ValueError, OSError, TypeError, KeyError):
        return None
    if record is None or record["state"] != "complete":
        return record
    safe = safe_artifact_path(Path(record["workspace"]), record["path"])
    if safe is not None:
        try:
            record["markdown"] = safe[1].read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
    return record


def foundation_artifact(record: dict[str, Any], *, preview_bytes: int = 0) -> dict[str, Any] | None:
    """Project an explicitly registered request into the shared artifact shape."""
    from .artifacts import artifact_metadata

    row = artifact_metadata(
        Path(record["workspace"]), record["path"], why=record["question"],
        preview_bytes=preview_bytes if record["state"] == "complete" else 0,
    )
    if row is None:
        return None
    if record["state"] != "complete":
        row.update(exists=False, size=0, mtime=None)
    metadata = {key: record[key] for key in (
        "id", "question", "locale", "source_task_id", "created_at", "version", "state",
        "title", "error", "provenance", "deadline_exceeded",
    ) if key in record}
    return {
        **row, "source": "reader_foundation", "group_title": (
            "问题基础说明" if record["locale"] == "zh-CN" else "Question foundations"
        ), "reader_foundation": metadata,
    }


def registered_foundation_artifacts(global_root: Path, sid: str) -> list[dict[str, Any]]:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return []
    rows = []
    for path in (life_dir / MANIFEST_DIRECTORY).glob("*.json"):
        try:
            record = _record(life_dir, _request_id(path.stem))
            row = foundation_artifact(record) if record is not None else None
        except (ValueError, OSError, TypeError, KeyError):
            continue
        if row is not None:
            rows.append(row)
    return sorted(rows, key=lambda row: row["reader_foundation"]["created_at"])


def registered_foundation_artifact(
    global_root: Path, sid: str, path: str, *, preview_bytes: int = 0,
) -> dict[str, Any] | None:
    request_id = foundation_id_from_path(path)
    life_dir = project_life_dir(sid, global_root=global_root)
    if request_id is None or life_dir is None:
        return None
    try:
        record = _record(life_dir, request_id)
        if record is None or record["path"] != PurePosixPath(str(path).replace("\\", "/")).as_posix():
            return None
        return foundation_artifact(record, preview_bytes=preview_bytes)
    except (ValueError, OSError, TypeError, KeyError):
        return None


def reserve_foundation(
    global_root: Path, sid: str, *, request_id: str, question: str, locale: str,
    source_task_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist a real explicit POST before a single worker may begin its call."""
    from .artifacts import artifact_workspace

    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        raise ValueError("unknown project")
    request_id = _request_id(request_id)
    body = {"question": question, "locale": locale, "source_task_id": source_task_id}
    directory = life_dir / MANIFEST_DIRECTORY
    directory.mkdir(exist_ok=True)
    with (directory / (request_id + ".lock")).open("a+b") as handle, exclusive_file_lock(handle):
        previous = _record(life_dir, request_id)
        if previous is not None:
            if any(previous.get(key) != value for key, value in body.items()):
                raise FoundationConflict("request_id already belongs to another question request")
            return previous, False
        if source_task_id and not any(
            item.id == source_task_id for item in Backlog(life_dir / "backlog.jsonl").history()
        ):
            raise ValueError("source task does not belong to this project")
        workspace = artifact_workspace(sid, global_root=global_root)
        if workspace is None:
            raise OSError("question foundation workspace unavailable")
        now = time.time()
        record = {
            "id": request_id, **body, "created_at": now, "version": FOUNDATION_VERSION,
            "state": "generating", "deadline_at": now + GENERATION_SECONDS,
            "owner": capture_process_identity(os.getpid()),
            "workspace": str(workspace.resolve()),
            "path": f"{ARTIFACT_DIRECTORY}/{life_dir.name}/{request_id}.md",
            "provenance": {
                "origin": "explicit_user_request", "sid": sid,
                "request_id": request_id, "run_label": "reader-foundation",
            },
        }
        _save_record(life_dir, record)
        return record, True


def generate_foundation(
    global_root: Path, sid: str, record: dict[str, Any], *,
    on_progress: MapProgress | None = None,
) -> dict[str, Any]:
    """Generate once for a reserved request and atomically publish its Markdown."""
    from .reader_foundation_prompt import foundation_request
    from .routes.workspace_v2 import _atomic_write_confined

    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        raise ValueError("unknown project")

    def receipt(result):
        record["provenance"].update({
            "call_id": result.call_id,
            "call_id_log_correlated": result.call_id_log_correlated,
            "exit_code": result.exit_code,
        })
        # Save the real receipt before parsing or validation can reject output.
        _save_record(life_dir, record)

    try:
        config = resolve_map_model()
        if not config.available:
            raise OSError("question foundation model unavailable")
        prompt, schema = foundation_request(record["question"], record["locale"])
        result = run_map_model(
            prompt, schema, config, project_root=life_dir, global_root=global_root,
            deadline=time.monotonic() + max(0, record["deadline_at"] - time.time()),
            on_progress=on_progress, run_label="reader-foundation", on_result=receipt,
        )
        if time.time() > record["deadline_at"]:
            raise TimeoutError("question foundation generation deadline exceeded")
        markdown = result["markdown"].strip()
        if not markdown:
            raise ValueError("question foundation is empty")
        title = " ".join(result["title"].splitlines()).strip()
        introduction, question_heading = (
            ("这是一份用于理解原问题的背景说明。研究进展与复核结论请查看对应任务记录。", "原问题")
            if record["locale"] == "zh-CN" else
            ("Background reading for understanding the original question. Research progress and reviewed results are tracked with the task.", "Original question")
        )
        document = f"# {title}\n\n{introduction}\n\n## {question_heading}\n\n{record['question']}\n\n---\n\n{markdown}\n"
        _atomic_write_confined(
            Path(record["workspace"]), f"{ARTIFACT_DIRECTORY}/{life_dir.name}",
            record["id"] + ".md", document.encode("utf-8"),
        )
        record.update(state="complete", title=result["title"], completed_at=time.time())
        _save_record(life_dir, record)
    except Exception as exc:
        record.update(state="failed", error=type(exc).__name__, finished_at=time.time())
        _save_record(life_dir, record)
        raise
    artifact = foundation_artifact(record)
    if artifact is None:
        raise OSError("question foundation artifact unavailable")
    return artifact
