"""Explicit human judgements and content-free operator access auditing."""
from __future__ import annotations

import re
import threading

from .analytics import AnalyticsError, _sanitize

SID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


class ResearchControls:
    def __init__(self, analytics):
        self.analytics = analytics
        self.capture_lock = threading.RLock()
        with analytics._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, tenant_id TEXT, sid TEXT,
                    outcome TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_annotations (
                    tenant_id TEXT NOT NULL, sid TEXT NOT NULL,
                    updated_at REAL NOT NULL, user_goal TEXT NOT NULL,
                    first_deviation_event_id TEXT, satisfaction TEXT NOT NULL,
                    note TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,sid)
                );
                CREATE TABLE IF NOT EXISTS research_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL, sid TEXT NOT NULL, task_id TEXT,
                    created_at REAL NOT NULL, verdict TEXT NOT NULL, note TEXT NOT NULL
                );
            """)

    def audit(self, action: str, *, tenant_id: str | None = None,
              sid: str | None = None, outcome: str = "requested", actor: str = "operator") -> int:
        if not re.fullmatch(r"[a-z_.:-]{1,80}", action):
            raise ValueError("Invalid audit action")
        if outcome not in {"requested", "allowed", "denied", "failed", "completed"}:
            raise ValueError("Invalid audit outcome")
        if tenant_id is not None and tenant_id not in self.analytics.tenants:
            raise AnalyticsError(404, "tenant_not_found")
        if sid is not None and not SID.fullmatch(sid):
            raise AnalyticsError(400, "invalid_session_id")
        if actor not in {"operator", "system", "tester"}:
            raise ValueError("Invalid audit actor")
        with self.analytics._db() as db:
            return db.execute("""
                INSERT INTO research_audit(created_at,actor,action,tenant_id,sid,outcome)
                VALUES (?,?,?,?,?,?)
            """, (self.analytics.clock(), actor, action, tenant_id, sid, outcome)).lastrowid

    def audit_log(self, limit: int = 100) -> dict:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Audit limit must be between 1 and 500")
        with self.analytics._db() as db:
            rows = db.execute("SELECT * FROM research_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"events": [dict(row) for row in rows]}

    def _project(self, tenant_id: str, sid: str):
        if not SID.fullmatch(sid):
            raise AnalyticsError(400, "invalid_session_id")
        self.analytics._require_consent(tenant_id)
        with self.analytics._db() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "journey_tombstones" in tables and db.execute(
                "SELECT 1 FROM journey_tombstones WHERE tenant_id=? AND sid=?", (tenant_id, sid),
            ).fetchone():
                raise AnalyticsError(410, "journal_deleted")
            if "journey_projects" in tables and db.execute(
                "SELECT 1 FROM journey_projects WHERE tenant_id=? AND sid=? AND notice_version=?",
                (tenant_id, sid, self.analytics.notice_version),
            ).fetchone():
                return
        with self.analytics._project(tenant_id, sid) as directory:
            meta, _ = self.analytics._meta(directory, sid)
            if meta is None:
                raise AnalyticsError(404, "project_not_found")

    def capture(self, tenant_id, sid, path, data):
        from .interaction_capture import Capture

        with self.capture_lock:
            with self.analytics._db() as db:
                if db.execute(
                    "SELECT 1 FROM journey_tombstones WHERE tenant_id=? AND sid=?", (tenant_id, sid),
                ).fetchone():
                    return None
            # Attachments and arbitrary command/resource objects are not research inputs.
            return Capture(self.analytics, tenant_id, sid, path, {
                key: value for key, value in data.items()
                if key in {"text", "name", "route_override"} and isinstance(value, str)
            })

    def delete_copies(self, journal, tenant_id, sid):
        with self.capture_lock:
            result = journal.delete_project(tenant_id, sid, research_copies=True)
        self.audit("research.delete", tenant_id=tenant_id, sid=sid, outcome="completed", actor="tester")
        return {
            "deleted": result, "runtime_files_deleted": False,
            "audit_metadata_retained": True, "future_project_capture_disabled": True,
        }

    @staticmethod
    def _text(value, maximum: int) -> str:
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"Text must be a string of at most {maximum} characters")
        return _sanitize(value)

    def annotate(self, tenant_id: str, sid: str, data: dict) -> dict:
        self._project(tenant_id, sid)
        if not isinstance(data, dict) or data.keys() - {
            "user_goal", "first_deviation_event_id", "satisfaction", "note",
        }:
            raise ValueError("Invalid annotation fields")
        satisfaction = data.get("satisfaction", "unknown")
        if satisfaction not in {"yes", "no", "unknown"}:
            raise ValueError("Satisfaction must be yes, no or unknown")
        deviation = data.get("first_deviation_event_id")
        if deviation is not None and (
            not isinstance(deviation, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", deviation)
        ):
            raise ValueError("Invalid deviation event identifier")
        values = (
            tenant_id, sid, self.analytics.clock(), self._text(data.get("user_goal", ""), 4000),
            deviation, satisfaction, self._text(data.get("note", ""), 4000),
        )
        with self.capture_lock, self.analytics._db() as db:
            self._project(tenant_id, sid)
            if deviation is not None and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='journey_events'",
            ).fetchone() and not db.execute(
                "SELECT 1 FROM journey_events WHERE tenant_id=? AND sid=? AND id=? AND notice_version=?",
                (tenant_id, sid, deviation, self.analytics.notice_version),
            ).fetchone():
                raise ValueError("Deviation must identify a retained event in this project")
            db.execute("""
                INSERT INTO research_annotations VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id,sid) DO UPDATE SET
                updated_at=excluded.updated_at,user_goal=excluded.user_goal,
                first_deviation_event_id=excluded.first_deviation_event_id,
                satisfaction=excluded.satisfaction,note=excluded.note
            """, values)
        return self.annotation(tenant_id, sid)

    def annotation(self, tenant_id: str, sid: str) -> dict:
        self._project(tenant_id, sid)
        self.prune()
        with self.analytics._db() as db:
            row = db.execute(
                "SELECT * FROM research_annotations WHERE tenant_id=? AND sid=?", (tenant_id, sid),
            ).fetchone()
        return dict(row) if row else {
            "tenant_id": tenant_id, "sid": sid, "user_goal": "",
            "first_deviation_event_id": None, "satisfaction": "unknown", "note": "",
        }

    def feedback_list(self, tenant_id, sid):
        self._project(tenant_id, sid)
        self.prune()
        with self.analytics._db() as db:
            rows = db.execute(
                "SELECT * FROM research_feedback WHERE tenant_id=? AND sid=? ORDER BY id DESC LIMIT 500",
                (tenant_id, sid),
            ).fetchall()
        return {"feedback": [dict(row) for row in rows], "inferred_success": False}

    def feedback(self, tenant_id: str, sid: str, data: dict) -> dict:
        self._project(tenant_id, sid)
        if not isinstance(data, dict) or data.keys() - {"task_id", "verdict", "note"}:
            raise ValueError("Invalid feedback fields")
        verdict = data.get("verdict")
        if verdict not in {"met_need", "needs_changes", "not_evaluated"}:
            raise ValueError("Feedback must be an explicit tester judgement")
        task_id = data.get("task_id")
        if task_id is not None and (not isinstance(task_id, str) or not SID.fullmatch(task_id)):
            raise ValueError("Invalid task identifier")
        note = self._text(data.get("note", ""), 4000)
        with self.capture_lock, self.analytics._db() as db:
            self._project(tenant_id, sid)
            key = db.execute("""
                INSERT INTO research_feedback(tenant_id,sid,task_id,created_at,verdict,note)
                VALUES (?,?,?,?,?,?)
            """, (tenant_id, sid, task_id, self.analytics.clock(), verdict, note)).lastrowid
        return {"id": key, "verdict": verdict, "inferred_success": False}

    def delete_project(self, tenant_id: str, sid: str) -> dict:
        self._project(tenant_id, sid)
        counts = {}
        with self.analytics._db() as db:
            for table in ("research_annotations", "research_feedback"):
                counts[table] = db.execute(
                    f"DELETE FROM {table} WHERE tenant_id=? AND sid=?", (tenant_id, sid),
                ).rowcount
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "interactions" in tables:
                counts["interactions"] = db.execute(
                    "DELETE FROM interactions WHERE tenant_id=? AND sid=?", (tenant_id, sid),
                ).rowcount
        self.audit("research.delete", tenant_id=tenant_id, sid=sid, outcome="completed", actor="tester")
        return {"deleted": counts, "runtime_files_deleted": False, "audit_metadata_retained": True}

    def prune(self) -> dict:
        cutoff = self.analytics.clock() - self.analytics.retention_days * 86400
        counts = {}
        with self.analytics._db() as db:
            for table, timestamp in (
                ("research_annotations", "updated_at"),
                ("research_feedback", "created_at"),
                ("research_audit", "created_at"),
            ):
                counts[table] = db.execute(
                    f"DELETE FROM {table} WHERE {timestamp}<?", (cutoff,),
                ).rowcount
        return counts
