"""Retain the exact progress explanation a reader can ask about, without a model."""

from __future__ import annotations

import copy
import json
import logging
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from ..core.file_lock import exclusive_file_lock
from .project_state import project_life_dir
from .reader_clarification import ReaderSourceUnavailable

DIRECTORY = "reader-progress-sources"
ARTIFACT_DIRECTORY = "reader-progress"
SOURCE_VERSION = 1
log = logging.getLogger(__name__)


def source_reference(record: dict) -> dict:
    return {"source_id": record["id"], **{key: record[key] for key in (
        "title", "generated_at", "path", "task_id", "card_key", "copy_revision",
    )}}


def source_context(record: dict) -> dict:
    """Only this retained explanation and its evidence enter a reading request."""
    return {"kind": "progress_snapshot", **source_reference(record),
            **copy.deepcopy({key: record[key] for key in ("card", "source_snapshot", "resolved_evidence")})}


def validate_source_context(value: dict, sid: str) -> None:
    if (not isinstance(value, dict) or value.get("kind") != "progress_snapshot"
            or str(UUID(str(value.get("source_id")))) != value.get("source_id")
            or value.get("path") != f"{ARTIFACT_DIRECTORY}/{sid}/{value['source_id']}.md"
            or not isinstance(value.get("card"), dict) or not isinstance(value.get("source_snapshot"), dict)
            or value["source_snapshot"].get("card_key") != value.get("card_key")
            or value["source_snapshot"].get("task_id") != value.get("task_id")
            or not isinstance(value.get("resolved_evidence"), dict)):
        raise ValueError("invalid retained progress context")


def progress_question_sources(global_root: Path, sid: str, source_id: str) -> dict:
    record = read_progress_source(global_root, sid, source_id)
    if record is None:
        raise ReaderSourceUnavailable("retained progress source unavailable")
    artifact = registered_progress_artifact(global_root, sid, record["path"])
    if not artifact or not artifact["exists"]:
        raise ReaderSourceUnavailable("retained progress source artifact unavailable or changed")
    return {"progress_source": source_context(record), "sources": []}


def read_progress_source(global_root: Path, sid: str, source_id: str) -> dict | None:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    try:
        source_id = str(UUID(str(source_id)))
        record = json.loads((life_dir / DIRECTORY / (source_id + ".json")).read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("id") != source_id or record.get("sid") != sid
                or record.get("kind") != "progress_snapshot"
                or record.get("path") != f"{ARTIFACT_DIRECTORY}/{life_dir.name}/{source_id}.md"
                or not isinstance(record.get("workspace"), str) or not Path(record["workspace"]).is_absolute()
                or not isinstance(record.get("document"), str) or not record["document"].strip()
                or not isinstance(record.get("card"), dict) or not isinstance(record.get("source_snapshot"), dict)
                or record["source_snapshot"].get("card_key") != record.get("card_key")
                or record["source_snapshot"].get("task_id") != record.get("task_id")):
            return None
        source_context(record)
        return record
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _resolved_evidence(root: Path, life_dir: Path, card: dict, snapshot: dict, evidence: dict | None) -> dict:
    """Reuse map record revisions; a later record never fills an earlier excerpt."""
    from .map_history import indexed_evidence
    from .map_view import with_revisions

    evidence = evidence or {}
    originals = snapshot.get("events", [])
    ids = [row["id"] for row in originals if isinstance(row, dict) and isinstance(row.get("id"), str)]
    try:
        indexed = with_revisions({"events": indexed_evidence(root, life_dir, ids)})["events"]
    except (OSError, ValueError, sqlite3.Error):
        indexed = []
    candidates = [*evidence.get("events", []), *indexed]
    task_id = snapshot["task_id"]
    resolved = []
    for original in originals:
        if not isinstance(original, dict) or not isinstance(original.get("id"), str):
            continue
        expected = original.get("revision")
        match = next((row for row in candidates if expected and row.get("id") == original["id"]
                      and row.get("item_id") == task_id and row.get("revision") == expected), None)
        resolved.append({"id": original["id"], "revision": expected,
                         "state": "same_revision" if match is not None else "unavailable",
                         **({"record": copy.deepcopy(match)} if match is not None else {})})
    task = next((row for row in evidence.get("tasks", []) if row.get("id") == task_id
                 and card.get("task_revision") and row.get("revision") == card["task_revision"]
                 and (not card.get("task_content_revision") or not row.get("content_revision")
                      or card["task_content_revision"] == row["content_revision"])), None)
    return {"captured_at": time.time(), "events": resolved,
            "task": {"state": "same_revision" if task is not None else "unavailable",
                     **({"record": copy.deepcopy(task)} if task is not None else {})}}


def _markdown(record: dict) -> str:
    zh = record["locale"] == "zh-CN"

    def wording(chinese, english):
        return chinese if zh else english

    def when(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            return wording("时间未记录", "Time unrecorded")
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def prose(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n\n".join(f"- {prose(item)}" for item in value)
        if isinstance(value, dict):
            return "\n\n".join(f"- {key}: {prose(item)}" for key, item in value.items())
        return str(value)

    fields = {
        "objective": wording("记录中的任务目标", "Recorded task objective"),
        "summary": wording("记录摘要", "Recorded summary"), "text": "",
        "acceptance_check": wording("验收要求", "Acceptance requirements"),
        "next_action": wording("记录中的后续安排", "Recorded next actions"),
        "status": wording("记录状态", "Recorded status"), "outcome": wording("记录结果", "Recorded outcome"),
        "outcome_source": wording("结果依据", "Outcome basis"),
        "goal_contribution": wording("目标关联", "Goal contribution"),
        "plan_hypothesis": wording("待检验的设想", "Recorded hypothesis"), "non_goals": wording("范围限制", "Scope limits"),
        "pending_question": wording("记录中的待答问题", "Recorded pending question"),
        "review_source": wording("复核来源", "Review source"), "review_skipped": wording("是否跳过复核", "Review skipped"),
        "stage_certification": wording("阶段核验状态", "Stage certification"),
    }

    def material(value):
        passages = []
        for key, label in fields.items():
            if key not in value or value[key] is None or value[key] == "":
                continue
            passages.append((f"**{label}**\n\n" if label else "") + prose(value[key]))
            if value.get(key + "_truncated") is True:
                passages.append(wording("此处为保留的摘录，原字段有截断。", "This retained field is an excerpt and was truncated."))
        return "\n\n".join(passages)

    card = record["card"]
    sections = [f"# {record['title']}",
                wording("这是当时保存的进展解释及其记录来源，不是新的研究结果。", "The saved progress explanation and its recorded sources, not a new research result."),
                wording("解释生成时间：", "Explanation generated: ") + when(record["generated_at"])]
    brief = card.get("reader_brief", {})
    for key, title in (("why", wording("问题背景", "Background")),
                       ("scope", wording("进展解释", "Progress explanation")),
                       ("next", wording("后续安排的解释", "Explanation of next actions"))):
        if isinstance(brief.get(key), str):
            sections.append(f"## {title}\n\n{brief[key]}")
        if key == "why" and isinstance(brief.get("concept"), dict):
            concept = brief["concept"]
            sections.append(f"## {concept.get('name') or wording('帮助理解的例子', 'An explanatory example')}\n\n"
                            + "\n\n".join(concept[name] for name in ("explanation", "example", "connection") if isinstance(concept.get(name), str)))
    for key, title in (("summary", "摘要" if zh else "Summary"), ("detail", "详细解释" if zh else "Detail")):
        if isinstance(card.get(key), str):
            sections.append(f"## {title}\n\n{card[key]}")
    if card.get("learning_path"):
        sections.append("## " + wording("说明中保留的学习示例", "Learning example retained in the explanation") + "\n\n" + prose(card["learning_path"]))
    snapshot = record["source_snapshot"]
    sections.append("## " + wording("生成解释时的来源摘录", "Source excerpts used for the explanation") + "\n\n"
                    + wording("下面是当时保留的记录原文。摘录不能代表未展示的全部工作，文中的文件路径和引用也不代表已读取文件。",
                              "These are the retained record passages. Excerpts do not establish everything that happened; paths and citations do not establish that files were read."))
    sections.append("### " + wording("当时的任务记录", "Task record at that time") + "\n\n" + material(snapshot["task"]))
    for index, event in enumerate(snapshot.get("events", []), 1):
        role = event.get("role") or wording("角色未记录", "Role unrecorded")
        sections.append(f"### {wording('记录', 'Record')} {index} · {role} · {when(event.get('ts'))}\n\n{material(event)}")
    for related in snapshot.get("related_tasks", []):
        sections.append("### " + wording("关联任务：", "Related task: ") + str(related.get("title") or related.get("id", ""))
                        + "\n\n" + material(related))
    resolved = record["resolved_evidence"]
    sections.append("## " + wording("同版本的完整保留记录", "Full retained records of the same revision") + "\n\n"
                    + wording("这里只列出与上面来源身份和版本一致的记录，不用更新后的记录替换原摘录。它们仍是系统保留的记录，不是所引用文件的全文。",
                              "Only records with the same source identity and revision are included here. Newer records do not replace the original excerpts. These are retained map records, not the contents of cited files."))
    if resolved.get("task", {}).get("state") == "same_revision":
        sections.append("### " + wording("同版本任务记录", "Task record of the same revision") + "\n\n" + material(resolved["task"]["record"]))
    for index, event in enumerate(resolved.get("events", []), 1):
        if event["state"] == "same_revision":
            full = event["record"]
            sections.append(f"### {wording('记录', 'Record')} {index} · {full.get('role') or wording('角色未记录', 'Role unrecorded')} · {when(full.get('ts'))}\n\n{material(full)}")
        else:
            sections.append(f"### {wording('记录', 'Record')} {index}\n\n" + wording(
                "未取得同版本完整记录。上面的摘录仍保留；不能据此断言被省略的内容没有发生。",
                "A full record of the same revision was unavailable. The original excerpt remains; this does not prove omitted work never happened."))
    foundation = snapshot.get("foundation")
    if isinstance(foundation, dict) and isinstance(foundation.get("markdown"), str):
        sections.append("## " + wording("当时使用的背景说明", "Background explanation used at that time") + "\n\n"
                        + wording("这是解释所用的背景，不是本次研究完成的证据。", "This supplies background, not evidence that this research completed its goal.")
                        + "\n\n" + foundation["markdown"])
    return "\n\n".join(sections) + "\n"


def retain_progress_source(
    global_root: Path, sid: str, *, copy_source: str, card_key: str, card: dict, locale: str,
    evidence: dict | None = None,
) -> dict:
    """Deduplicate server-owned card contents under a short, separate registry lock.

    No map-generation lock, model call, current-cache replacement or task write
    occurs here. Sources already published under an ID are never overwritten.
    """
    from .artifacts import artifact_workspace
    from .routes.workspace_v2 import _atomic_write_confined

    life_dir = project_life_dir(sid, global_root=global_root)
    snapshot = card.get("source_snapshot") if isinstance(card, dict) else None
    if (life_dir is None or not isinstance(snapshot, dict) or snapshot.get("card_key") != card_key
            or not isinstance(snapshot.get("task_id"), str) or not snapshot["task_id"]
            or not isinstance(snapshot.get("events"), list) or not isinstance(snapshot.get("task"), dict)
            or not isinstance(card.get("title"), str) or not card["title"].strip()
            or type(card.get("copy_revision")) is not int or card["copy_revision"] < 1
            or type(card.get("generated_at")) not in (int, float) or not math.isfinite(card["generated_at"])
            or locale not in {"zh-CN", "en-US"}):
        raise ReaderSourceUnavailable("progress source identity or snapshot unavailable")
    saved_card = copy.deepcopy({key: value for key, value in card.items() if key not in {"progress_source", "source_snapshot"}})
    snapshot = copy.deepcopy(snapshot)
    identity = json.dumps([copy_source, card_key, card.get("version"), card["copy_revision"],
                           card["generated_at"], card.get("input_revision")], ensure_ascii=False, separators=(",", ":"))
    directory = life_dir / DIRECTORY
    directory.mkdir(exist_ok=True)
    index_path = directory / "index.json"

    def read_index():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def existing_reference(index):
        for source_id in index.get(identity, []):
            previous = read_progress_source(global_root, sid, source_id)
            if previous and previous["card"] == saved_card and previous["source_snapshot"] == snapshot:
                return source_reference(previous)
        return None

    # Atomic index reads and immutable manifests make repeat GETs read-only.
    previous = existing_reference(read_index())
    if previous is not None:
        return previous
    # Resolve existing map evidence before this short registry transaction.
    resolved = _resolved_evidence(global_root, life_dir, card, snapshot, evidence)
    with (directory / "registry.lock").open("a+b") as handle, exclusive_file_lock(
        handle, timeout_seconds=5, lock_name="reader progress sources",
    ):
        index = read_index()
        previous = existing_reference(index)
        if previous is not None:
            return previous
        workspace = artifact_workspace(sid, global_root=global_root)
        if workspace is None:
            raise OSError("progress snapshot workspace unavailable")
        source_id = str(uuid4())
        record = {
            "id": source_id, "kind": "progress_snapshot", "version": SOURCE_VERSION,
            "sid": sid, "locale": locale, "copy_source": copy_source, "card_key": card_key,
            "title": card["title"], "copy_revision": card["copy_revision"], "generated_at": card["generated_at"],
            "task_id": snapshot["task_id"], "created_at": time.time(), "workspace": str(workspace.resolve()),
            "path": f"{ARTIFACT_DIRECTORY}/{life_dir.name}/{source_id}.md",
            "card": saved_card, "source_snapshot": snapshot, "resolved_evidence": resolved,
        }
        record["document"] = _markdown(record)
        _atomic_write_confined(workspace, f"{ARTIFACT_DIRECTORY}/{life_dir.name}", source_id + ".md", record["document"].encode())
        _atomic_write_confined(life_dir, DIRECTORY, source_id + ".json",
                               (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode())
        index.setdefault(identity, []).append(source_id)
        _atomic_write_confined(life_dir, DIRECTORY, "index.json", (json.dumps(index, ensure_ascii=False) + "\n").encode())
        return source_reference(record)


def registered_progress_artifact(global_root: Path, sid: str, path: str, *, preview_bytes: int = 0) -> dict | None:
    from .artifacts import artifact_metadata

    parts = PurePosixPath(str(path).replace("\\", "/")).parts
    if len(parts) != 3 or parts[0] != ARTIFACT_DIRECTORY or not parts[2].endswith(".md"):
        return None
    record = read_progress_source(global_root, sid, parts[2][:-3])
    if record is None or record["path"] != "/".join(parts):
        return None
    row = artifact_metadata(Path(record["workspace"]), record["path"], why=record["title"], preview_bytes=preview_bytes)
    if row is None:
        return None
    try:
        if Path(row["storage_path"]).read_text(encoding="utf-8") != record["document"]:
            return None
    except (OSError, UnicodeError):
        return None
    return {**row, "source": "progress_snapshot", "group_title": "进展来源快照" if record["locale"] == "zh-CN" else "Progress snapshots",
            "progress_source": source_reference(record)}


def bind_progress_cards(root: Path, sid: str, copy_source: str, cards: dict, locale: str, *, evidence: dict | None = None) -> dict:
    """Add retained references to a response without rewriting the map cache."""
    result = {}
    for key, card in cards.items():
        if not isinstance(card, dict):
            result[key] = card
            continue
        row = {**card}
        try:
            row["progress_source"] = retain_progress_source(
                root, sid, copy_source=copy_source, card_key=key, card=card, locale=locale, evidence=evidence,
            )
        except ReaderSourceUnavailable:
            row.pop("progress_source", None)
        except (OSError, ValueError, TimeoutError):
            # A failed source registration must not erase an existing reading.
            log.exception("Could not retain reader progress source")
            row.pop("progress_source", None)
        result[key] = row
    return result
