"""Purpose-authorized views and streaming exports of received agent observations.

Raw observations are not SFT approval. V2 reads the append-only event table;
legacy rows remain readable without rewriting their source records or reviews.
"""
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

from .analytics import AnalyticsError
from .collaboration_data import ROLES, _code, _role, _task_id
from .training_capture import OBSERVED_POLICY
from .training_data import PURPOSES, REVIEWER_KINDS, _hash, _json

MAX_PAGE_BYTES = 20 * 1024 * 1024
MAX_EPISODE_METADATA = 128
_CURSOR = re.compile(r"([1-9][0-9]{0,18}):(-1|[0-9]{1,18})\Z")
_COLUMNS = "id,tenant_id,sid,session_id,started_at,updated_at,state,reason,runtime_profile,runtime_metadata,grants"


class _ExportChunks:
    """An explicitly closeable iterator also cleans up an abandoned download."""

    def __init__(self, path):
        self.path, self.source = path, None

    def __iter__(self):
        return self

    def __next__(self):
        if self.path is None:
            raise StopIteration
        if self.source is None:
            self.source = self.path.open("rb")
        chunk = self.source.read(128 * 1024)
        if chunk:
            return chunk
        self.close()
        raise StopIteration

    def close(self):
        if self.source is not None:
            self.source.close()
            self.source = None
        if self.path is not None:
            self.path.unlink(missing_ok=True)
            self.path = None

    def __del__(self):
        self.close()


def observed_summary(db, episode_id, *, pair_limit=100):
    """Small SQL projections; never load context, tool arguments, or outputs."""
    counts = {row["kind"]: row["n"] for row in db.execute(
        "SELECT kind,count(*) AS n FROM training_observed_events WHERE episode_id=? GROUP BY kind", (episode_id,),
    )}
    common = """
        WITH calls AS (
            SELECT json_extract(payload,'$.toolCallId') AS call_id,
                   json_extract(payload,'$.toolName') AS name,min(sequence) AS seq,
                   min(observed_at) AS stamp
            FROM training_observed_events WHERE episode_id=? AND kind='tool_call'
              AND json_type(payload,'$.toolCallId')='text' AND json_type(payload,'$.toolName')='text'
            GROUP BY call_id,name HAVING count(*)=1
        ), results AS (
            SELECT json_extract(payload,'$.toolCallId') AS call_id,
                   json_extract(payload,'$.toolName') AS name,min(sequence) AS seq,
                   min(observed_at) AS stamp,json_type(payload,'$.isError') AS error_type
            FROM training_observed_events WHERE episode_id=? AND kind='tool_result'
              AND json_type(payload,'$.toolCallId')='text' AND json_type(payload,'$.toolName')='text'
            GROUP BY call_id,name HAVING count(*)=1
        )
    """
    join = " FROM calls c JOIN results r ON c.call_id=r.call_id AND c.name=r.name AND r.seq>c.seq WHERE c.call_id IS NOT NULL"
    total = db.execute(common + "SELECT count(*)" + join, (episode_id, episode_id)).fetchone()[0]
    pairs = [{"call_id": row["call_id"], "name": row["name"],
              "status": {"true": "error", "false": "success"}.get(row["error_type"], "unknown"),
              "call_timestamp": row["called_at"], "result_timestamp": row["returned_at"], "result_characters": None}
             for row in db.execute(
                 common + "SELECT c.call_id,c.name,c.stamp AS called_at,r.stamp AS returned_at,r.error_type"
                 + join + " ORDER BY c.seq LIMIT ?", (episode_id, episode_id, pair_limit),
             )]
    issues = [{"reason": _code(row["reason"], "capture_warning"), "count": row["n"]}
              for row in db.execute(
                  "SELECT json_extract(payload,'$.reason') AS reason,count(*) AS n FROM training_observed_events "
                  "WHERE episode_id=? AND kind IN ('capture_warning','quarantine') GROUP BY reason LIMIT 100", (episode_id,),
              )]
    return {"event_count": sum(counts.values()), "event_counts": counts, "tool_pairs": pairs,
            "tool_pairs_total": total, "tool_pairs_truncated": total > len(pairs), "issues": issues}


class ObservedData:
    def __init__(self, training):
        self.training = training
        self.analytics = training.analytics

    def _validate(self, purpose, projects):
        if purpose not in PURPOSES:
            raise ValueError("Unknown training purpose")
        return self.training._selection(projects)

    def _grant(self, db, purpose, tenant, sid):
        grant, reason = self.training._grant(db, tenant, sid, purpose)
        if reason:
            raise AnalyticsError(403, reason)
        return grant

    def _rows(self, db, purpose, tenant, sid, grant, task_id=None, after_episode=0, limit=None):
        cutoff = max(grant, self.analytics.clock() - min(30, self.analytics.retention_days) * 86400)
        matched = 0
        for row in db.execute(
            f"SELECT {_COLUMNS} FROM training_tool_episodes WHERE tenant_id=? AND sid=? AND started_at>=? AND id>=? ORDER BY id",
            (tenant, sid, cutoff, after_episode),
        ):
            runtime = json.loads(row["runtime_metadata"])
            if json.loads(row["grants"]).get(purpose) != grant:
                continue
            if task_id is not None and _task_id(runtime.get("mission_id")) != task_id:
                continue
            yield row
            matched += 1
            if limit is not None and matched >= limit:
                break

    def _quality(self, db, row, purpose, grant, runtime):
        result = {"state": "not_evaluated", "approved": False}
        if runtime.get("capture_policy") == OBSERVED_POLICY or row["state"] != "complete":
            return result
        key = _hash(_json(["pi_episode", row["tenant_id"], row["sid"], row["id"]]).encode())
        receipt = db.execute(
            "SELECT * FROM training_sample_reviews WHERE tenant_id=? AND sid=? AND purpose=? AND event_id=?",
            (row["tenant_id"], row["sid"], purpose, key),
        ).fetchone()
        cutoff = self.analytics.clock() - min(30, self.analytics.retention_days) * 86400
        if not receipt or not (receipt["grant_at"] == grant and receipt["notice_version"] == self.analytics.notice_version
                               and receipt["context_approved"] and receipt["reviewer_kind"] in REVIEWER_KINDS
                               and cutoff <= receipt["reviewed_at"] <= self.analytics.clock()):
            return result
        try:
            from .training_capture import HOSTED_PROFILE

            sample = self.training.capture._sample(self.training.capture.events(db, row["id"]), grant,
                                                    hosted=row["runtime_profile"] == HOSTED_PROFILE)
        except (ValueError, TypeError, KeyError, IndexError):
            return result
        if _hash(_json(sample).encode()) == receipt["sample_sha256"]:
            result.update(state="approved", approved=True, reviewer_kind=receipt["reviewer_kind"],
                          reviewed_at=receipt["reviewed_at"])
        return result

    def _descriptor(self, db, row, purpose, grant, *, quality=True):
        runtime = json.loads(row["runtime_metadata"])
        role = _role(runtime.get("run_label"))
        observed = runtime.get("capture_policy") == OBSERVED_POLICY
        summary = observed_summary(db, row["id"]) if observed else None
        if summary is None:
            try:
                records = self.training.capture.events(db, row["id"])
            except (ValueError, TypeError):
                records = []
            from .collaboration_data import CollaborationData

            pairs = CollaborationData._pairs(records) if row["state"] == "complete" else []
            summary = {"event_count": len(records), "event_counts": dict(Counter(event["kind"] for event in records)),
                       "tool_pairs": pairs, "tool_pairs_total": len(pairs), "tool_pairs_truncated": False,
                       "issues": [{"reason": _code(row["reason"], "capture_interrupted"), "count": 1}]
                       if row["reason"] else []}
        return {
            "episode_id": row["id"], "tenant_id": row["tenant_id"], "sid": row["sid"],
            "task_id": _task_id(runtime.get("mission_id")), "role": role, "label": ROLES[role],
            "session_id": row["session_id"], "runtime": runtime, "capture_policy": runtime.get("capture_policy", "legacy"),
            "state": row["state"], "started_at": row["started_at"], "updated_at": row["updated_at"],
            "collection": {"event_count": summary["event_count"], "event_counts": summary["event_counts"],
                           "complete": row["state"] == "complete", "issues": summary["issues"],
                           "historical_data_unavailable": not observed and not summary["event_count"]},
            "quality": self._quality(db, row, purpose, grant, runtime) if quality else {"state": "not_evaluated", "approved": False},
            "tool_pairs": summary["tool_pairs"], "tool_pairs_total": summary["tool_pairs_total"],
            "tool_pairs_truncated": summary["tool_pairs_truncated"], "events": [],
        }

    def _recheck(self, purpose, grants):
        with self.training.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN")
            for (tenant, sid), original in grants.items():
                if self._grant(db, purpose, tenant, sid) != original:
                    raise AnalyticsError(403, "training_capture_consent_changed")

    def observations(self, purpose, tenant, sid, task_id=None, *, cursor=None, limit=200):
        self.training.audit("observations", purpose)
        try:
            self._validate(purpose, [{"tenant_id": tenant, "sid": sid}])
            if task_id is not None and _task_id(task_id) is None:
                raise ValueError("Invalid task identity")
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ValueError("Observation limit must be 1..500")
            after_episode, after_sequence = 0, -1
            if cursor is not None:
                match = _CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
                if not match:
                    raise ValueError("Invalid observation cursor")
                after_episode, after_sequence = map(int, match.groups())
                if after_episode >= 2**63 or after_sequence >= 2**63:
                    raise ValueError("Invalid observation cursor")
            result, returned, used, has_more = [], 0, 0, False
            next_cursor = None
            with self.analytics._db() as db:
                db.execute("BEGIN")
                grant = self._grant(db, purpose, tenant, sid)
                rows = list(self._rows(db, purpose, tenant, sid, grant, task_id, after_episode, MAX_EPISODE_METADATA + 1))
                for row in rows[:MAX_EPISODE_METADATA]:
                    descriptor = self._descriptor(db, row, purpose, grant)
                    sequence = after_sequence if row["id"] == after_episode else -1
                    for event in self.training.capture.iter_events(db, row["id"], after_sequence=sequence):
                        size = len(_json(event).encode())
                        if returned >= limit or (returned and used + size > MAX_PAGE_BYTES):
                            has_more = True
                            break
                        descriptor["events"].append(event)
                        returned += 1
                        used += size
                        sequence = event["sequence"]
                    result.append(descriptor)
                    next_cursor = f"{row['id']}:{sequence}"
                    if has_more:
                        break
                has_more |= len(rows) > MAX_EPISODE_METADATA
            self._recheck(purpose, {(tenant, sid): grant})
            payload = {"tenant_id": tenant, "sid": sid, "task_id": task_id, "purpose": purpose,
                       "episodes": result, "global_complete": False,
                       "pagination": {"has_more": has_more, "next_cursor": next_cursor if has_more else None,
                                      "returned_events": returned, "limit": limit},
                       "scope": "received_public_observations", "quality_status": "not_automatically_approved"}
            self.training.audit("observations", purpose, outcome="completed")
            return payload
        except Exception:
            self.training.audit("observations", purpose, outcome="failed")
            raise

    def export_observations(self, purpose, projects):
        """Write each source observation separately, then stream the private ZIP."""
        self.training.audit("export_observations", purpose)
        path = None
        try:
            selected = self._validate(purpose, projects)
            handle = tempfile.NamedTemporaryFile(prefix="argus-observations-", suffix=".zip", delete=False)
            path = Path(handle.name)
            handle.close()
            grants, episode_count, event_count = {}, 0, 0
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                with archive.open("observations.jsonl", "w", force_zip64=True) as output:
                    for tenant, sid in selected:
                        with self.analytics._db() as db:
                            db.execute("BEGIN")
                            grant = self._grant(db, purpose, tenant, sid)
                            grants[(tenant, sid)] = grant
                            for row in self._rows(db, purpose, tenant, sid, grant):
                                descriptor = self._descriptor(db, row, purpose, grant)
                                descriptor.pop("events")
                                output.write((_json({"kind": "episode", **descriptor}) + "\n").encode())
                                episode_count += 1
                                for event in self.training.capture.iter_events(db, row["id"]):
                                    output.write((_json({"kind": "observation", "tenant_id": tenant, "sid": sid,
                                                        "episode_id": row["id"], "event": event}) + "\n").encode())
                                    event_count += 1
                archive.writestr("manifest.json", _json({
                    "format": "argus-observations-v2", "purpose": purpose,
                    "created_at": self.analytics.clock(), "selection_scope": "retained_export_snapshot",
                    "projects": [{"tenant_id": tenant, "sid": sid} for tenant, sid in selected],
                    "episodes": episode_count, "observations": event_count,
                    "quality_status": "not_automatically_approved", "global_complete": False,
                }))
                archive.writestr("README.txt", "Received agent observations. Each JSONL episode header is followed by its stored observation rows.\n"
                                 "Collection includes text-only, interrupted and not-yet-settled runs. Collection is not training-quality approval.\n"
                                 "Application system/developer instructions are retained; private structured thinking/signature blocks are excluded.\n"
                                 "Missing observer payloads are not fabricated. Available session-log message recoveries explicitly identify their source; "
                                 "they do not imply recorded provider requests, tool schemas, or complete episodes.\n")
            self._recheck(purpose, grants)
            self.training.audit("export_observations", purpose, outcome="completed", counts={"projects": len(selected)})
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            self.training.audit("export_observations", purpose, outcome="failed")
            raise

        return _ExportChunks(path), f"argus-{purpose}-observations.zip"
