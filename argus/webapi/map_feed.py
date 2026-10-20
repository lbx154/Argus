"""Bounded, session-scoped map projections and incremental responses."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from ..core.session import read_session_meta
from ..life.memory import Backlog
from .map_view import read_map


def _stamp(path: Path):
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


class MapFeed:
    def __init__(self):
        self._sessions: OrderedDict[tuple, dict] = OrderedDict()
        # Guards only entry lookup and LRU maintenance. Reads hold their
        # per-entry lock, so one slow session cannot serialize the others.
        self._lock = threading.Lock()

    def _entry(self, key: tuple) -> dict:
        with self._lock:
            entry = self._sessions.setdefault(key, {
                "events": {}, "versions": OrderedDict(), "invalidated": OrderedDict(),
                "lock": threading.Lock(), "epoch": uuid.uuid4().hex, "count": 0,
            })
            self._sessions.move_to_end(key)
            while len(self._sessions) > 16:
                self._sessions.popitem(last=False)
            return entry

    def read(
        self, sid: str, root: Path, life_dir: Path, after: str | None = None, *,
        include_events: bool = True, include_task_index: bool = False,
    ) -> dict:
        key = (sid, str(root.resolve()), str(life_dir.resolve()), include_events)
        entry = self._entry(key)
        with entry["lock"]:
            meta = read_session_meta(root, sid)
            stamp = (
                # Include the storage owner's recovery files: a completion can
                # commit before either materialized backlog file changes.
                *(_stamp(path) for path in (
                    *Backlog(life_dir / "backlog.jsonl").storage_paths,
                    *((life_dir / "events.jsonl",) if include_events else ()),
                )),
                meta.display_name if meta else sid,
            )
            if entry.get("stamp") != stamp:
                value = read_map(sid, root, life_dir, event_state=entry["events"],
                                 include_events=include_events)
                versions = entry["versions"]
                if entry["events"].get("reset"):
                    entry["invalidated"].update((cursor, None) for cursor in versions)
                    while len(entry["invalidated"]) > 64:
                        entry["invalidated"].popitem(last=False)
                    versions.clear()
                if value == entry.get("value"):
                    revision = entry["revision"]  # an unchanged map keeps its cursor
                else:
                    # A revision is the item's own canonical JSON; an unchanged
                    # item shares the string the newest version already holds.
                    newest = versions[next(reversed(versions))] if versions else ({}, {})
                    for items, known in zip((value["tasks"], value["events"]), newest):
                        for item in items:
                            if known.get(item["id"]) == item["revision"]:
                                item["revision"] = known[item["id"]]
                    entry["count"] += 1
                    revision = f"{entry['epoch']}.{entry['count']}"
                versions[revision] = (
                    {t["id"]: t["revision"] for t in value["tasks"]},
                    {e["id"]: e["revision"] for e in value["events"]},
                )
                while len(versions) > 8:
                    versions.popitem(last=False)
                entry.update(stamp=stamp, value=value, revision=revision)
            value, revision = entry["value"], entry["revision"]
            # The full task ordering, so current-range callers can filter an
            # incremental response without a second signature read.
            extra = (
                {"task_index": [(t["id"], t.get("ts") or 0) for t in value["tasks"]]}
                if include_task_index else {}
            )
            previous = entry["versions"].get(after)
            if previous is None:
                return {**value, **extra, "cursor": revision, "incremental": False,
                        "reset_history": after in entry["invalidated"]}
            tasks, events = previous
            ids = {t["id"] for t in value["tasks"]}
            return {
                **value,
                **extra,
                "cursor": revision,
                "incremental": True,
                "tasks": [t for t in value["tasks"] if tasks.get(t["id"]) != t["revision"]],
                "events": [e for e in value["events"] if events.get(e["id"]) != e["revision"]],
                "removed_task_ids": sorted(tasks.keys() - ids),
            }
