"""Cached presentation copy. Generated relationships never change the task DAG."""

from __future__ import annotations

import copy
import json
import logging
import math
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from weakref import WeakValueDictionary

from ..core.file_lock import exclusive_file_lock
from .map_model import (
    resolve_map_model,
)
from .map_outcomes import project_task_outcome
from .map_view import facts, task_content_revision, text

PROMPT_VERSION = 26
# The versions the retired source-first and learning-path readers saved at.
SOURCE_FIRST_VERSION = 25
LEARNING_PATH_VERSION = 27
Preview = bool | Literal["learning-path", "question-foundation"]
_LOCK = threading.Lock()
_SOURCES: WeakValueDictionary = WeakValueDictionary()


log = logging.getLogger(__name__)

def copy_source(dataset_id: str, locale: str, *, preview: Preview = False, foundation_id: str | None = None) -> str:
    """A preview never replaces the normal reader's retained explanation."""
    if preview == "question-foundation":
        if not foundation_id:
            raise ValueError("select a question foundation")
        return dataset_id + ":" + locale + ":question-foundation:" + foundation_id
    suffix = ":learning-path" if preview == "learning-path" else ":source-first" if preview else ""
    return dataset_id + ":" + locale + suffix


def copy_version(*, preview: Preview = False) -> int:
    """The version saved explanations of each reader were written at."""
    if preview == "question-foundation":
        from .reader_application import PREVIEW_VERSION

        return PREVIEW_VERSION
    if preview == "learning-path":
        return LEARNING_PATH_VERSION
    if preview:
        return SOURCE_FIRST_VERSION
    return PROMPT_VERSION


@contextmanager
def _source_lock(root: Path, source: str):
    key = (str(root.resolve()), source)
    with _LOCK:
        lock = _SOURCES.get(key)
        if lock is None:
            lock = threading.Lock()
            _SOURCES[key] = lock
    with lock:
        path = cache_path(root, source).with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle, exclusive_file_lock(
            handle, timeout_seconds=210, lock_name="map summaries",
        ):
            yield


def _write_cache(path: Path, value: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def configured() -> bool:
    try:
        return resolve_map_model().available
    except (OSError, ValueError, RuntimeError):
        return False


def card_evidence(dataset: dict, cards: list[dict]) -> list[dict]:
    tasks = {t["id"]: t for t in dataset["tasks"]}
    events = {e["id"]: e for e in dataset["events"]}
    result, seen = [], set()
    for card in cards:
        task = tasks.get(card["task_id"])
        if task is None or card["key"] in seen:
            raise ValueError("unknown or duplicate card")
        owned = [e for e in events.values() if e["item_id"] == task["id"]]
        keys = {task["id"], *(task["id"] + suffix for suffix in (":brief", ":active", ":outcome"))}
        keys.update(e["id"] for e in owned)
        keys.update(e["id"] + ":next" for e in owned if e.get("next_action"))
        if card["key"] not in keys:
            raise ValueError("card does not belong to this task")
        seen.add(card["key"])
        selected = []
        for event_id in card["event_ids"]:
            event = events.get(event_id)
            if event is None or event["item_id"] != task["id"]:
                raise ValueError("event does not belong to this task")
            selected.append(event)
        if len(selected) > 16:
            raise ValueError("too many observations")
        if task.get("turn_kind") == "qa":
            # The recorded answer is already readable. Viewing a Q&A must
            # never start a second model to rewrite or review that answer.
            continue
        dynamic = card["key"] in (task["id"], task["id"] + ":active", task["id"] + ":outcome")
        source_task = project_task_outcome(task, owned) if dynamic else task
        result.append(
            {
                "key": card["key"],
                "kind": card["kind"],
                "task_id": task["id"],
                "task_revision": task.get("revision", facts(task)),
                "task_content_revision": task_content_revision(task),
                "dynamic": dynamic,
                "task": {
                    k: source_task.get(k)
                    for k in ((
                        "title",
                        "objective",
                        "summary",
                        "status",
                        "acceptance_check",
                        "pending_question",
                        "goal_contribution",
                        "plan_hypothesis",
                        "non_goals",
                        "outcome",
                        "outcome_source",
                        "attempt",
                        "started_ts",
                        "finished_ts",
                    ) if dynamic else (
                        "title", "objective", "acceptance_check", "goal_contribution",
                        "plan_hypothesis", "non_goals",
                    ))
                },
                "events": [
                    {**e, "text": e["text"][:2500], "next_action": e.get("next_action", "")[:1500],
                     **({"text_truncated": True} if len(e["text"]) > 2500 else {}),
                     **({"next_action_truncated": True} if len(e.get("next_action", "")) > 1500 else {})}
                    for e in selected
                ],
            }
        )
    return result


def cache_path(root: Path, source: str) -> Path:
    return root / "map-presentation" / (quote(source, safe="") + ".json")


def read_cache(root: Path, source: str) -> dict:
    try:
        value = json.loads(cache_path(root, source).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _bounded_document(document: dict) -> dict:
    """Bound presentation inputs without rewriting canonical research evidence."""
    original_task = document.get("task", {})
    original_events = document.get("events", [])
    task = copy.deepcopy(original_task)
    for key, value in original_task.items():
        if isinstance(value, str):
            task[key] = text(value, 160 if key == "title" else 1000)
            if task[key] != value:
                task[key + "_truncated"] = True
    events = copy.deepcopy(original_events[-4:])
    for event in events:
        for key, limit in (("text", 1200), ("next_action", 600)):
            if isinstance(event.get(key), str):
                original = event[key]
                event[key] = text(original, limit)
                if event[key] != original:
                    event[key + "_truncated"] = True
    return {**document, "task": task, "events": events,
            "events_truncated": bool(document.get("events_truncated")) or len(original_events) > 4,
            "evidence_truncated": bool(document.get("evidence_truncated"))
            or task != original_task or events != original_events}


def _failure_metadata(cache: dict) -> dict:
    try:
        retry_at = float(cache.get("retry_at", 0))
        remaining = min(3600, max(0, math.ceil(retry_at - time.time()))) if math.isfinite(retry_at) else 0
    except (TypeError, ValueError, OverflowError):
        remaining = 0
    error = cache.get("generation_error")
    manual = isinstance(error, dict) and bool(error.get("manual_retry"))
    if isinstance(error, dict) and (remaining > 0 or manual):
        code = error.get("code")
        if code not in {"map_timeout", "cost_unreconciled", "budget_exhausted", "invalid_response", "provider_error", "map_input_too_large"}:
            code = "provider_error"
        error = {"code": code, "message": "Map summary unavailable; existing evidence and cached text are retained."}
        if manual:
            error["manual_retry"] = True
    else:
        # A failure is news for as long as its cooldown lasts. After that the
        # next request simply tries again; showing "unavailable" for hours
        # over a cache nobody has asked to regenerate (s-009c3ec3, 2026-09-16
        # 04:07 onward) told the reader something was wrong when nothing was.
        error = None
    # Explicit zero clears a previous cooldown in clients that merge cache fields.
    return {"generation_error": error, "retry_after": remaining}
