"""Bounded, session-scoped map projections and incremental responses."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from ..core.session import read_session_meta
from .map_team import source_snapshot
from .map_view import digest, read_map


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
                "lock": threading.Lock(),
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
            bindings = entry["events"].get("team_bindings", {})
            sources = source_snapshot(sid, root, life_dir, bindings) if include_events else None
            stamp = (
                *(_stamp(life_dir / name) for name in (
                    "backlog.jsonl", "backlog.archive.jsonl",
                    *(("events.jsonl",) if include_events else ()),
                )),
                meta.display_name if meta else sid,
                sources[0] if include_events else (),
            )
            if entry.get("stamp") != stamp:
                value = read_map(sid, root, life_dir, event_state=entry["events"],
                                 include_events=include_events,
                                 team_sources=(bindings, sources) if include_events else None)
                versions = entry["versions"]
                if entry["events"].get("reset"):
                    entry["invalidated"].update((cursor, None) for cursor in versions)
                    while len(entry["invalidated"]) > 64:
                        entry["invalidated"].popitem(last=False)
                    versions.clear()
                revision = digest(value)
                versions[revision] = (
                    {t["id"]: t["revision"] for t in value["tasks"]},
                    {e["id"]: e["revision"] for e in value["events"]},
                )
                while len(versions) > 8:
                    versions.popitem(last=False)
                # Use the fingerprint taken before reading the Team records.
                # A later writer must invalidate, not get blessed as cached.
                stamp = (*stamp[:-1], entry["events"].get("team_signature", ()))
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
            event_ids = {e["id"] for e in value["events"]}
            return {
                **value,
                **extra,
                "cursor": revision,
                "incremental": True,
                "tasks": [t for t in value["tasks"] if tasks.get(t["id"]) != t["revision"]],
                "events": [e for e in value["events"] if events.get(e["id"]) != e["revision"]],
                "removed_task_ids": sorted(tasks.keys() - ids),
                # Log history is retained by the client. Team snapshots are
                # replaceable observations and must disappear when removed.
                "removed_event_ids": sorted(
                    event_id for event_id in events
                    if event_id.startswith("team:") and event_id not in event_ids
                ),
            }
