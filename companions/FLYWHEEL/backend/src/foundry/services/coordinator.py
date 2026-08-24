"""Connection, campaign, reminder and Viewer state projection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..db import Database, decode_row, utc_now
from ..integrations.argus_webapi import (
    ArgusWebApiClient,
    ArgusWebApiError,
    EventBatch,
    EventCursor,
    argus_connection_metadata,
    assess_argus_connection,
)
from ..secrets import SecretVault
from .candidate_import import CandidateImportError, import_argus_candidate_artifacts

_EVENT_WINDOW = 1_000


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _terminal_evidence(
    snapshot: dict[str, Any], events: tuple[dict[str, Any], ...]
) -> dict[str, Any] | None:
    """Return positive Project-terminal evidence; daemon death is never evidence."""

    mission_view = snapshot.get("mission_view")
    mission_view = mission_view if isinstance(mission_view, dict) else {}
    mission = mission_view.get("mission")
    mission = mission if isinstance(mission, dict) else {}
    status = str(mission.get("status") or "").strip().lower()
    completed_at = mission.get("completed_at")
    if status in {"complete", "completed", "done"} and completed_at not in {None, "", 0, 0.0}:
        return {
            "source": "mission_view.completed_at",
            "status": status,
            "completed_at": completed_at,
        }
    for event in reversed(events):
        event_type = str(event.get("type") or event.get("event_type") or "")
        if event_type == "project.completed":
            return {
                "source": "event.project.completed",
                "event_sha256": hashlib.sha256(
                    json.dumps(
                        event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
                    ).encode("utf-8")
                ).hexdigest(),
                "ts": event.get("ts") or event.get("created_at"),
            }
        if (
            event_type == "life.planner.verdict"
            and event.get("project_done") is True
            and isinstance(event.get("delivery"), dict)
        ):
            return {
                "source": "event.life.planner.verdict",
                "event_sha256": hashlib.sha256(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest(),
                "ts": event.get("ts") or event.get("created_at"),
            }
    return None


class BackgroundCoordinator:
    def __init__(self, db: Database, vault: SecretVault, interval_seconds: float, data_dir: Path) -> None:
        self.db, self.vault, self.interval_seconds, self.data_dir = db, vault, interval_seconds, data_dir
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def _client(self, row: dict[str, Any]) -> ArgusWebApiClient:
        token = self.vault.resolve(
            row["id"], row.get("token_ref"), endpoint=row.get("base_url")
        )
        return ArgusWebApiClient(row["base_url"], token=token, timeout=5)

    async def start(self) -> None:
        if self.interval_seconds > 0 and self._task is None:
            self._task = asyncio.create_task(self._run(), name="foundry-coordinator")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.poll_connections()
            await self.poll_campaigns()
            await self.promote_scheduled_campaigns()
            await self.emit_due_reminders()
            await self.ingest_viewer_outbox()
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval_seconds)
            except TimeoutError:
                pass

    async def poll_connections(self) -> None:
        for row in self.db.fetch_all("SELECT * FROM connections WHERE enabled=1"):
            status, error = "online", None
            persisted_metadata: dict[str, Any] | None = None
            try:
                result = await asyncio.to_thread(self._client(row).test_connection)
                assessment = assess_argus_connection(result)
                status, error = assessment.status, assessment.error
                decoded = decode_row(row) or {}
                persisted_metadata = dict(decoded.get("metadata") or {})
                persisted_metadata.update(argus_connection_metadata(result, assessment))
            except (ArgusWebApiError, ValueError) as exc:
                status, error = "offline", str(exc)
            self.db.execute(
                "UPDATE connections SET status=?,last_checked_at=?,last_error=?,metadata_json="
                "COALESCE(?,metadata_json),updated_at=? WHERE id=?",
                (
                    status,
                    utc_now(),
                    error,
                    json.dumps(persisted_metadata, ensure_ascii=False)
                    if persisted_metadata is not None
                    else None,
                    utc_now(),
                    row["id"],
                ),
            )
            if row["status"] != status:
                self.db.append_event("connections", f"connection.{status}",
                    severity="warning" if error else "info", entity_type="connection",
                    entity_id=row["id"], payload={"name": row["name"], "error": error})

    async def poll_campaigns(self) -> None:
        rows = self.db.fetch_all(
            """SELECT c.*,cn.base_url,cn.token_ref,cn.enabled AS connection_enabled
               FROM campaigns c JOIN connections cn ON cn.id=c.connection_id
               WHERE c.argus_project_id IS NOT NULL
               AND c.execution_state IN ('starting','running','draining','needs_attention')""")
        for campaign in rows:
            if not campaign["connection_enabled"]:
                continue
            remote = {"id": campaign["connection_id"], "base_url": campaign["base_url"],
                      "token_ref": campaign.get("token_ref")}
            config = _json_object(campaign.get("config_json"))
            try:
                client = self._client(remote)
                snapshot, event_batch = await asyncio.gather(
                    asyncio.to_thread(
                        client.snapshot,
                        campaign["argus_project_id"],
                        events_limit=min(_EVENT_WINDOW, 500),
                    ),
                    asyncio.to_thread(
                        self._poll_event_batch,
                        client,
                        str(campaign["argus_project_id"]),
                        config,
                    ),
                )
            except (ArgusWebApiError, ValueError) as exc:
                self.db.execute(
                    "UPDATE campaigns SET snapshot_stale=1,last_summary=?,updated_at=? WHERE id=?",
                    (f"Last snapshot retained; refresh failed: {exc}", utc_now(), campaign["id"]))
                continue
            try:
                snapshot["foundry_artifacts"] = await asyncio.to_thread(
                    client.artifacts, campaign["argus_project_id"]
                )
                snapshot["foundry_artifacts_status"] = "available"
            except (ArgusWebApiError, ValueError) as exc:
                # Artifact indexing is optional for older Argus servers.  Keep the
                # usable snapshot and make the partial state explicit instead of
                # marking the entire Campaign stale.
                snapshot["foundry_artifacts"] = []
                snapshot["foundry_artifacts_status"] = "unavailable"
                snapshot["foundry_artifacts_error"] = str(exc)
            health, daemon = snapshot.get("health") or {}, snapshot.get("daemon") or {}
            alive = bool(health.get("alive", daemon.get("alive", False)))
            remote_state = str(health.get("state") or health.get("phase") or daemon.get("state") or "unknown")
            new_events = 0
            events = tuple(dict(event) for event in event_batch.events)
            for event in events:
                key = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
                with self.db.transaction() as transaction:
                    inserted = transaction.execute(
                        "INSERT OR IGNORE INTO campaign_event_ingest VALUES(?,?,?)",
                        (campaign["id"], key, utc_now())).rowcount > 0
                if inserted:
                    new_events += 1
                    self.db.append_event("argus", "argus.event", entity_type="campaign",
                        entity_id=campaign["id"], payload={"event": event})
            config["argus_event_cursor"] = {
                "project_id": event_batch.cursor.project_id,
                "view": event_batch.cursor.view,
                "fingerprints": list(event_batch.cursor.fingerprints),
                "window_limit": _EVENT_WINDOW,
            }
            if event_batch.gap_detected:
                first_gap = not bool(config.get("argus_event_gap_detected"))
                config["argus_event_gap_detected"] = True
                config.setdefault("argus_event_gap_detected_at", utc_now())
                if first_gap:
                    self.db.append_event(
                        "argus",
                        "argus.event_gap_detected",
                        severity="attention",
                        entity_type="campaign",
                        entity_id=campaign["id"],
                        payload={
                            "project_id": campaign["argus_project_id"],
                            "window_limit": _EVENT_WINDOW,
                            "action": "review_remote_event_history_before integrity promotion",
                        },
                    )
            progressing = bool(health.get("making_progress", False)) or new_events > 0
            terminal = _json_object(config.get("argus_terminal_evidence")) or _terminal_evidence(
                snapshot, events
            )
            if terminal:
                config["argus_terminal_evidence"] = terminal
            ideation_run = self.db.fetch_one(
                "SELECT * FROM ideation_runs WHERE campaign_id=?", (campaign["id"],)
            )
            import_ready = ideation_run is None
            import_error: CandidateImportError | ArgusWebApiError | sqlite3.Error | None = None
            if ideation_run is not None:
                run_id = str(ideation_run["id"])
                if alive and str(ideation_run["state"]) == "campaign_created":
                    self.db.execute(
                        "UPDATE ideation_runs SET state='running',updated_at=? "
                        "WHERE id=? AND state='campaign_created'",
                        (utc_now(), run_id),
                    )
                if str(ideation_run["state"]) in {"awaiting_labels", "labeled", "closed"}:
                    import_ready = True
                elif terminal:
                    self.db.execute(
                        "UPDATE ideation_runs SET state='awaiting_import',updated_at=? "
                        "WHERE id=? AND state IN ('campaign_created','running')",
                        (utc_now(), run_id),
                    )
                    try:
                        result = await asyncio.to_thread(
                            import_argus_candidate_artifacts,
                            self.db,
                            run_id=run_id,
                            client=client,
                            artifact_index=snapshot.get("foundry_artifacts") or [],
                        )
                        import_ready = True
                        config["ideation_artifact_import"] = {
                            "status": "imported" if result.imported else "already_imported",
                            "candidate_count": result.candidate_count,
                            "candidates_sha256": result.candidates_sha256,
                            "manifest_sha256": result.manifest_sha256,
                            "verified_at": utc_now(),
                        }
                    except (CandidateImportError, ArgusWebApiError, sqlite3.Error) as exc:
                        import_error = exc
                        self._record_candidate_quarantine(
                            campaign=campaign,
                            run_id=run_id,
                            config=config,
                            error=exc,
                            artifact_index=snapshot.get("foundry_artifacts") or [],
                        )
            execution = str(campaign["execution_state"])
            if terminal:
                if alive:
                    # Terminal evidence may arrive one poll before the daemon
                    # exits. Keep it capacity-counted until liveness agrees.
                    execution = "draining"
                elif import_ready and not bool(config.get("argus_event_gap_detected")):
                    execution = "completed"
                else:
                    execution = "needs_attention"
            elif alive and execution in {"starting", "needs_attention"}:
                execution = "running"
            elif not alive and execution == "draining":
                execution = "paused"
            elif not alive and execution in {"starting", "running"}:
                execution = "needs_attention"
            mission_view = snapshot.get("mission_view") or {}
            mission = mission_view.get("mission") if isinstance(mission_view, dict) else {}
            summary = (
                (mission or {}).get("summary")
                or (mission or {}).get("objective")
                or snapshot.get("summary")
                or snapshot.get("status")
                or ""
            )
            if not isinstance(summary, str):
                summary = json.dumps(summary, ensure_ascii=False)[:20_000]
            if import_error is not None and not alive:
                code = import_error.code if isinstance(import_error, CandidateImportError) else "transport_error"
                summary = f"Argus ended, but verified candidate import needs attention ({code}): {import_error}"
            elif terminal and config.get("argus_event_gap_detected") and not alive:
                summary = (
                    "Argus returned terminal evidence, but the persisted event cursor detected "
                    "a history gap; completion remains blocked for integrity review."
                )
            config["argus_remote_state"] = remote_state
            snapshot["foundry_terminal_evidence"] = terminal
            snapshot["foundry_event_gap_detected"] = bool(
                config.get("argus_event_gap_detected")
            )
            snapshot["foundry_ideation_artifact_import"] = config.get(
                "ideation_artifact_import"
            )
            now = utc_now()
            self.db.execute(
                """UPDATE campaigns SET process_alive=?,making_progress=?,snapshot_stale=0,
                   last_snapshot_json=?,last_summary=?,execution_state=?,config_json=?,updated_at=?,
                   last_progress_at=?,completed_at=CASE WHEN ?='completed'
                       THEN COALESCE(completed_at,?) ELSE completed_at END,
                   progress=CASE WHEN ?='completed' THEN 1 ELSE progress END WHERE id=?""",
                (int(alive), int(progressing), json.dumps(snapshot, ensure_ascii=False), summary,
                 execution, json.dumps(config, ensure_ascii=False), now,
                 now if progressing else campaign.get("last_progress_at"), execution, now, execution,
                 campaign["id"]))

    def _poll_event_batch(
        self, client: ArgusWebApiClient, project_id: str, config: dict[str, Any]
    ) -> EventBatch:
        raw_cursor = _json_object(config.get("argus_event_cursor"))
        cursor: EventCursor | None = None
        fingerprints = raw_cursor.get("fingerprints")
        if (
            raw_cursor.get("project_id") == project_id
            and raw_cursor.get("view") == "full"
            and isinstance(fingerprints, list)
            and all(isinstance(value, str) and len(value) == 64 for value in fingerprints)
        ):
            cursor = EventCursor(project_id, "full", tuple(fingerprints[-_EVENT_WINDOW:]))
        poll = getattr(client, "poll_events", None)
        if callable(poll):
            return poll(project_id, cursor=cursor, limit=_EVENT_WINDOW, view="full")
        # Test doubles and legacy wrappers may expose only ``events``. Preserve
        # the exact same overlap contract instead of falling back to last-80.
        try:
            rows = client.events(project_id, limit=_EVENT_WINDOW, view="full")
        except TypeError:
            rows = client.events(project_id, limit=_EVENT_WINDOW)
        current = tuple(ArgusWebApiClient._event_fingerprint(event) for event in rows)
        previous = cursor.fingerprints if cursor is not None else ()
        overlap = ArgusWebApiClient._tail_overlap(previous, current)
        return EventBatch(
            events=tuple(rows[overlap:]),
            cursor=EventCursor(project_id, "full", current if current else previous),
            overlap_count=overlap,
            gap_detected=bool(previous and current and overlap == 0),
        )

    def _record_candidate_quarantine(
        self,
        *,
        campaign: dict[str, Any],
        run_id: str,
        config: dict[str, Any],
        error: CandidateImportError | ArgusWebApiError | sqlite3.Error,
        artifact_index: list[dict[str, Any]],
    ) -> None:
        code = error.code if isinstance(error, CandidateImportError) else "candidate_import_error"
        safe_index = [
            {
                "path": str(item.get("path") or "")[:4_096],
                "size": item.get("size") if isinstance(item.get("size"), int) else None,
                "sha256": str(item.get("sha256") or "")[:64],
                "exists": item.get("exists") is True,
            }
            for item in artifact_index[:2_048]
            if isinstance(item, dict)
        ]
        signature = hashlib.sha256(
            json.dumps(
                {"code": code, "message": str(error), "index": safe_index},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        prior = _json_object(config.get("ideation_artifact_import"))
        config["ideation_artifact_import"] = {
            "status": "quarantined",
            "code": code,
            "error": str(error)[:4_000],
            "signature": signature,
            "observed_at": utc_now(),
            "raw_artifact_persisted": False,
        }
        if prior.get("signature") == signature:
            return
        self.db.append_event(
            "ideation",
            "ideation.candidate_artifacts_quarantined",
            severity="attention",
            entity_type="ideation_run",
            entity_id=run_id,
            payload={
                "campaign_id": campaign["id"],
                "code": code,
                "error": str(error)[:2_000],
                "signature": signature,
                "raw_artifact_persisted": False,
                "action": "repair and re-register both bound candidate artifacts in Argus",
            },
        )

    async def emit_due_reminders(self) -> None:
        for row in self.db.fetch_all(
            "SELECT * FROM reminders WHERE state='pending' AND trigger_at<=? LIMIT 100", (utc_now(),)):
            self.db.execute("UPDATE reminders SET state='fired',updated_at=? WHERE id=?",
                            (utc_now(), row["id"]))
            self.db.append_event("reminders", "reminder.fired", severity="attention",
                                 entity_type="reminder", entity_id=row["id"], payload={"title": row["title"]})

    async def promote_scheduled_campaigns(self) -> None:
        rows = self.db.fetch_all(
            "SELECT id,title FROM campaigns WHERE schedule_state='scheduled' "
            "AND execution_state='idle' AND scheduled_for IS NOT NULL AND scheduled_for<=?",
            (utc_now(),),
        )
        for row in rows:
            self.db.execute(
                "UPDATE campaigns SET schedule_state='awaiting_approval',updated_at=? WHERE id=?",
                (utc_now(), row["id"]),
            )
            self.db.append_event(
                "campaigns", "campaign.start_due", severity="attention",
                entity_type="campaign", entity_id=row["id"],
                payload={
                    "title": row["title"],
                    "action": "human_start_approval_required",
                    "automatic_submission": False,
                },
            )

    async def ingest_viewer_outbox(self) -> None:
        outbox, consumed = self.data_dir / "viewer" / "outbox", self.data_dir / "viewer" / "consumed"
        consumed.mkdir(parents=True, exist_ok=True)
        for path in sorted(outbox.glob("*.json")) if outbox.is_dir() else ():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            review = self.db.fetch_one("SELECT * FROM reviews WHERE id=?", (path.stem,))
            if not review:
                path.replace(consumed / path.name)
                continue
            state = str(result.get("state") or "invalid_input")
            score = result.get("overall") if isinstance(result.get("overall"), (int, float)) else None
            self.db.execute("UPDATE reviews SET state=?,score=?,recommendation=?,feedback_json=?,updated_at=? WHERE id=?",
                (state, score, result.get("oral_readiness"), json.dumps(result, ensure_ascii=False), utc_now(), review["id"]))
            self.db.execute("UPDATE campaigns SET review_state=?,viewer_score=?,reviewer_scores_json=?,updated_at=? WHERE id=?",
                (state, score, json.dumps(result.get("dimension_scores") or {}), utc_now(), review["campaign_id"]))
            self.db.append_event("reviews", f"review.{state}", entity_type="campaign",
                                 entity_id=review["campaign_id"], payload={"review_id": review["id"], "score": score})
            path.replace(consumed / path.name)
