"""Keep the explanation a reader asked about, without a model.

Nothing is kept when an explanation is written or fetched. The explanation and
the step's records are saved once, at the moment the reader asks a question
about them, so the answer and any follow-up stay attached to what was read.
"""

from __future__ import annotations

import copy
import json
import math
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


def source_reference(record: dict) -> dict:
    return {"source_id": record["id"], **{key: record[key] for key in (
        "title", "generated_at", "path", "task_id", "card_key", "copy_revision",
    )}}


def _kept_material(value: dict) -> bool:
    """The explanation and the step's records are both present and belong together.

    Sources saved before 2026-09-20 were written with every explanation and
    keep a ``source_snapshot`` plus ``resolved_evidence``; they stay readable so
    questions asked about them still open.
    """
    if not isinstance(value.get("card"), dict):
        return False
    records = value.get("records")
    if isinstance(records, dict):
        return (records.get("task_id") == value.get("task_id") and isinstance(records.get("task"), dict)
                and isinstance(records.get("events"), list))
    earlier = value.get("source_snapshot")
    return (isinstance(earlier, dict) and earlier.get("card_key") == value.get("card_key")
            and earlier.get("task_id") == value.get("task_id"))


def source_context(record: dict) -> dict:
    """Only the explanation that was asked about and its records enter a reading request."""
    kept = ("card", "records") if isinstance(record.get("records"), dict) else (
        "card", "source_snapshot", "resolved_evidence")
    return {"kind": "progress_snapshot", **source_reference(record),
            **copy.deepcopy({key: record[key] for key in kept})}


def validate_source_context(value: dict, sid: str) -> None:
    if (not isinstance(value, dict) or value.get("kind") != "progress_snapshot"
            or str(UUID(str(value.get("source_id")))) != value.get("source_id")
            or value.get("path") != f"{ARTIFACT_DIRECTORY}/{sid}/{value['source_id']}.md"
            or not _kept_material(value)
            or ("records" not in value and not isinstance(value.get("resolved_evidence"), dict))):
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
                or not _kept_material(record)):
            return None
        source_context(record)
        return record
    except (OSError, ValueError, TypeError, KeyError):
        return None


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
                wording("这是读者提问时读到的说明，以及这一步当时的记录，不是新的研究结果。", "The explanation the reader was reading when they asked, with the step's records at that time; not a new research result."),
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
    records = record["records"]
    sections.append("## " + wording("提问时这一步的记录", "This step's records when the question was asked") + "\n\n"
                    + wording("下面是提问时这一步留下的记录原文。较长的记录只保留了节选；文中的文件路径和引用不代表已读取文件。",
                              "These are the step's own records as they stood when the question was asked. Long records are excerpted; paths and citations do not establish that files were read."))
    sections.append("### " + wording("任务记录", "Task record") + "\n\n" + material(records["task"]))
    for index, event in enumerate(records["events"], 1):
        role = event.get("role") or wording("角色未记录", "Role unrecorded")
        sections.append(f"### {wording('记录', 'Record')} {index} · {role} · {when(event.get('ts'))}\n\n{material(event)}")
    return "\n\n".join(sections) + "\n"


def retain_progress_source(
    global_root: Path, sid: str, *, copy_source: str, card_key: str, card: dict, locale: str, records: dict,
) -> dict:
    """Keep one explanation and its step's records when the reader asks about them.

    ``records`` is the task and step records as they stand now: ``task_id``,
    ``task`` and ``events``. One version of an explanation is kept once, so a
    second question about it joins the first. No model call and no change to
    the map's saved text or to any task happens here.
    """
    from .artifacts import artifact_workspace
    from .routes.workspace_v2 import _atomic_write_confined

    life_dir = project_life_dir(sid, global_root=global_root)
    if (life_dir is None or not isinstance(card, dict) or not isinstance(records, dict)
            or not isinstance(records.get("task_id"), str) or not records["task_id"]
            or not isinstance(records.get("events"), list) or not isinstance(records.get("task"), dict)
            or not isinstance(card.get("title"), str) or not card["title"].strip()
            or type(card.get("copy_revision")) is not int or card["copy_revision"] < 1
            or type(card.get("generated_at")) not in (int, float) or not math.isfinite(card["generated_at"])
            or locale not in {"zh-CN", "en-US"}):
        raise ReaderSourceUnavailable("no saved explanation to ask about")
    saved_card = copy.deepcopy({key: value for key, value in card.items() if key not in {"progress_source", "source_snapshot"}})
    records = copy.deepcopy({key: records[key] for key in ("task_id", "task", "events")})
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
            if previous:
                return source_reference(previous)
        return None

    # Atomic index reads and immutable manifests make a repeated question read-only.
    previous = existing_reference(read_index())
    if previous is not None:
        return previous
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
            "task_id": records["task_id"], "created_at": time.time(), "workspace": str(workspace.resolve()),
            "path": f"{ARTIFACT_DIRECTORY}/{life_dir.name}/{source_id}.md",
            "card": saved_card, "records": records,
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
    return {**row, "source": "progress_snapshot", "group_title": "提问时的说明与记录" if record["locale"] == "zh-CN" else "Explanation and records at question time",
            "progress_source": source_reference(record)}
