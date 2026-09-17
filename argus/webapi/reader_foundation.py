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

FOUNDATION_VERSION = 3
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
    kind = value.get("kind", "foundation")
    if kind not in {"foundation", "clarification", "progress_answer"}:
        raise ValueError("invalid reading record kind")
    if kind == "foundation" and (value.get("parent_id") or value.get("root_id", value["id"]) != value["id"]):
        raise ValueError("invalid foundation binding")
    snapshot = value.get("source_snapshot")
    progress = snapshot.get("progress_source") if isinstance(snapshot, dict) else None
    if progress is not None:
        from .reader_progress import validate_source_context

        validate_source_context(progress, life_dir.name)
        if kind == "foundation":
            raise ValueError("a progress snapshot is not a foundation")
    if kind == "progress_answer":
        if (progress is None or value.get("parent_id") or value.get("root_id") != value["id"]
                or value.get("progress_source") != {"source_id": progress["source_id"]}
                or snapshot.get("sources") != []):
            raise ValueError("invalid progress answer binding")
    if kind == "clarification":
        if any(_request_id(value.get(key, "")) != value.get(key) for key in ("parent_id", "root_id")):
            raise ValueError("invalid clarification binding")
        if value["id"] in {value["parent_id"], value["root_id"]}:
            raise ValueError("invalid clarification ancestry")
        sources = snapshot.get("sources") if isinstance(snapshot, dict) else None
        expected_ids = {value["parent_id"]} if progress is not None else {value["root_id"], value["parent_id"]}
        if (not isinstance(sources, list) or len(sources) != len(expected_ids)
                or any(not isinstance(source, dict) for source in sources)
                or {source.get("id") for source in sources} != expected_ids
                or any(snapshot.get(key) != value[key] for key in ("root_id", "parent_id"))):
            raise ValueError("invalid clarification source snapshot")
        for source in sources:
            if (source.get("path") != f"{ARTIFACT_DIRECTORY}/{life_dir.name}/{source['id']}.md"
                    or not isinstance(source.get("markdown"), str) or not source["markdown"].strip()
                    or not isinstance(source.get("question"), str)
                    or (source.get("kind") not in {"progress_answer", "clarification"} if progress is not None
                        else source.get("kind") != ("foundation" if source["id"] == value["root_id"] else "clarification"))):
                raise ValueError("invalid clarification source content")
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
    if record is not None:
        # The registered file is authoritative; a manifest field is not a readback.
        record.pop("markdown", None)
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
    metadata.update(
        kind=record.get("kind", "foundation"),
        parent_id=record.get("parent_id"),
        root_id=record.get("root_id", record["id"]),
    )
    snapshot = record.get("source_snapshot", {})
    progress = snapshot.get("progress_source")
    if progress is not None:
        from .reader_progress import source_reference

        metadata["progress_source"] = source_reference({**progress, "id": progress["source_id"]})
    if record.get("kind") in {"clarification", "progress_answer"}:
        metadata["sources"] = [
            *([{"id": progress["source_id"], "path": progress["path"], "title": progress["title"],
                "kind": "progress_snapshot"}] if progress is not None else []),
            *[
            {"id": source["id"], "path": source["path"], "title": source.get("title") or source["question"]}
            for source in snapshot["sources"]
            ],
        ]
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
    source_task_id: str | None = None, parent_id: str | None = None, progress_source: dict | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist a real explicit POST before a single worker may begin its call."""
    from .artifacts import artifact_workspace

    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        raise ValueError("unknown project")
    request_id = _request_id(request_id)
    body = {"question": question, "locale": locale, "source_task_id": source_task_id}
    kind = "clarification" if parent_id is not None else "foundation"
    if progress_source is not None:
        if (parent_id is not None or source_task_id is not None or not isinstance(progress_source, dict)
                or set(progress_source) != {"source_id"}):
            raise ValueError("progress questions require only a retained source reference")
        body["progress_source"] = {"source_id": _request_id(progress_source["source_id"])}
        kind = "progress_answer"
    if parent_id is not None:
        body["parent_id"] = _request_id(parent_id)
    directory = life_dir / MANIFEST_DIRECTORY
    directory.mkdir(exist_ok=True)
    with (directory / (request_id + ".lock")).open("a+b") as handle, exclusive_file_lock(handle):
        previous = _record(life_dir, request_id)
        if previous is not None:
            if (previous.get("kind", "foundation") != kind
                    or any(previous.get(key) != value for key, value in body.items())):
                raise FoundationConflict("request_id already belongs to another question request")
            return previous, False
        sources = None
        if progress_source is not None:
            from .reader_progress import progress_question_sources

            sources = progress_question_sources(global_root, sid, body["progress_source"]["source_id"])
        if parent_id is not None:
            from .reader_clarification import clarification_sources

            sources = clarification_sources(global_root, sid, body["parent_id"])
            if request_id in {sources["root_id"], sources["parent_id"]}:
                raise FoundationConflict("clarification request_id must differ from its sources")
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
        if sources is not None:
            record.update(kind=kind, root_id=sources.get("root_id", request_id), source_snapshot=sources)
            record["provenance"]["run_label"] = "reader-progress-question" if kind == "progress_answer" else "reader-clarification"
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
        clarification = record.get("kind") in {"clarification", "progress_answer"}
        if clarification:
            from .reader_clarification import clarification_request

            prompt, schema = clarification_request(
                record["question"], record["locale"], record["source_snapshot"],
            )
        else:
            prompt, schema = foundation_request(record["question"], record["locale"])
        result = run_map_model(
            prompt, schema, config, project_root=life_dir, global_root=global_root,
            deadline=time.monotonic() + max(0, record["deadline_at"] - time.time()),
            on_progress=on_progress,
            run_label=record["provenance"]["run_label"], on_result=receipt,
            output_format="markdown",
        )
        if time.time() > record["deadline_at"]:
            raise TimeoutError("question foundation generation deadline exceeded")
        markdown = result["markdown"].strip()
        if not markdown:
            raise ValueError("question foundation is empty")
        introduction, question_heading = (
            ("这是一份用于理解原问题的背景说明。研究进展与复核结论请查看对应任务记录。", "原问题")
            if record["locale"] == "zh-CN" else
            ("Background reading for understanding the original question. Research progress and reviewed results are tracked with the task.", "Original question")
        )
        if clarification:
            introduction, question_heading = (
                ("这是一份针对阅读疑点的补充回答。原说明保留不变，背景讨论不计作研究进展。", "这次追问")
                if record["locale"] == "zh-CN" else
                ("A clarification of the saved reading. The original documents are unchanged; this discussion is not research progress.", "Your follow-up question")
            )
            if record["source_snapshot"].get("progress_source") is not None:
                introduction, question_heading = (
                    ("这是一份针对所选进展解释的阅读回答，依据保留的解释和记录快照。它不会修改任务或研究指令，也不计作新的研究进展。", "这次问题")
                    if record["locale"] == "zh-CN" else
                    ("A reading answer about the selected progress explanation and its retained records. It does not change tasks or research instructions and is not new research progress.", "Your question")
                )
        references = ""
        if clarification:
            links = []
            sources = record["source_snapshot"]["sources"]
            if record["source_snapshot"].get("progress_source") is not None:
                sources = [record["source_snapshot"]["progress_source"], *sources]
            for source in sources:
                label = " ".join((source.get("title") or source["question"]).split())
                label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                links.append(f"- [{label}]({source['path']})")
            sources_heading = "参考来源" if record["locale"] == "zh-CN" else "Reading sources"
            references = f"\n\n## {sources_heading}\n\n" + "\n".join(links)
        document = f"{markdown}\n\n---\n\n{introduction}\n\n## {question_heading}\n\n{record['question']}{references}\n"
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
