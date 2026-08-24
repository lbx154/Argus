from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import Database, utc_now

CALENDAR_FILE = "conference_calendar_2026-08-22_2027-08-22.json"
IDEAS_FILE = "topics_all_58x5.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Required seed file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def seed_database(db: Database, data_dir: Path) -> dict[str, int]:
    calendar = _load(data_dir / CALENDAR_FILE)
    ideas_document = _load(data_dir / IDEAS_FILE)
    now = utc_now()
    venue_count = deadline_count = idea_count = reminder_count = 0
    with db.transaction() as connection:
        for venue in calendar.get("venues", []):
            metadata = {
                key: value
                for key, value in venue.items()
                if key not in {
                    "key", "display_name", "official_name", "category_id", "category_zh",
                    "venue_status", "targets_in_window",
                }
            }
            connection.execute(
                """
                INSERT INTO venues(venue_key,display_name,official_name,category_id,category_zh,status,
                                   metadata_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(venue_key) DO UPDATE SET
                    display_name=excluded.display_name, official_name=excluded.official_name,
                    category_id=excluded.category_id, category_zh=excluded.category_zh,
                    status=excluded.status, metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    venue["key"], venue.get("display_name", venue["key"]),
                    venue.get("official_name", ""), venue.get("category_id", ""),
                    venue.get("category_zh", ""), venue.get("venue_status", "active"),
                    json.dumps(metadata, ensure_ascii=False), now, now,
                ),
            )
            venue_count += 1
            venue_id = connection.execute(
                "SELECT id FROM venues WHERE venue_key=?", (venue["key"],)
            ).fetchone()[0]
            for target in venue.get("targets_in_window", []):
                source_url = target.get("source_url") or (target.get("forecast_basis") or {}).get("source_url")
                round_note = target.get("round_note") or ""
                metadata = {
                    key: value for key, value in target.items()
                    if key not in {
                        "conference_year", "deadline_date", "timezone", "round_note",
                        "evidence_status", "forecast_window_start", "forecast_window_end",
                        "confidence", "requires_official_confirmation",
                    }
                }
                connection.execute(
                    """
                    INSERT INTO deadlines(venue_id,conference_year,deadline_date,timezone,round_note,
                      evidence_status,forecast_window_start,forecast_window_end,confidence,source_url,
                      requires_confirmation,metadata_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(venue_id,conference_year,deadline_date,round_note) DO UPDATE SET
                      timezone=excluded.timezone,evidence_status=excluded.evidence_status,
                      forecast_window_start=excluded.forecast_window_start,
                      forecast_window_end=excluded.forecast_window_end,
                      confidence=excluded.confidence,source_url=excluded.source_url,
                      requires_confirmation=excluded.requires_confirmation,
                      metadata_json=excluded.metadata_json,updated_at=excluded.updated_at
                    """,
                    (
                        venue_id, target.get("conference_year"), target["deadline_date"],
                        target.get("timezone", "AoE"), round_note,
                        target.get("evidence_status", "forecast"),
                        target.get("forecast_window_start"), target.get("forecast_window_end"),
                        target.get("confidence"), source_url,
                        int(bool(target.get("requires_official_confirmation", True))),
                        json.dumps(metadata, ensure_ascii=False), now, now,
                    ),
                )
                deadline_count += 1

        venue_ids = {
            row["venue_key"]: row["id"]
            for row in connection.execute("SELECT id,venue_key FROM venues").fetchall()
        }
        for idea in ideas_document.get("topics", []):
            venue_id = venue_ids.get(idea.get("venue_key"))
            if venue_id is None:
                continue
            known = {
                "topic_rank_within_venue", "venue_key", "title_zh", "problem_gap",
                "core_hypothesis", "method", "public_data_or_tasks", "strongest_baselines",
                "decisive_experiments", "compute_fit", "venue_fit_reason", "kill_criterion",
                "risk_level", "reusable_program",
            }
            metadata = {key: value for key, value in idea.items() if key not in known}
            connection.execute(
                """
                INSERT INTO ideas(venue_id,rank,title_zh,problem_gap,core_hypothesis,method,
                  public_data_or_tasks,strongest_baselines,decisive_experiments,compute_fit,
                  venue_fit_reason,kill_criterion,risk_level,reusable_program,metadata_json,
                  created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(venue_id,rank) DO UPDATE SET
                  title_zh=excluded.title_zh,problem_gap=excluded.problem_gap,
                  core_hypothesis=excluded.core_hypothesis,method=excluded.method,
                  public_data_or_tasks=excluded.public_data_or_tasks,
                  strongest_baselines=excluded.strongest_baselines,
                  decisive_experiments=excluded.decisive_experiments,compute_fit=excluded.compute_fit,
                  venue_fit_reason=excluded.venue_fit_reason,kill_criterion=excluded.kill_criterion,
                  risk_level=excluded.risk_level,reusable_program=excluded.reusable_program,
                  metadata_json=excluded.metadata_json,updated_at=excluded.updated_at
                """,
                (
                    venue_id, int(idea["topic_rank_within_venue"]), idea["title_zh"],
                    idea.get("problem_gap", ""), idea.get("core_hypothesis", ""),
                    idea.get("method", ""), idea.get("public_data_or_tasks", ""),
                    idea.get("strongest_baselines", ""), idea.get("decisive_experiments", ""),
                    idea.get("compute_fit", ""), idea.get("venue_fit_reason", ""),
                    idea.get("kill_criterion", ""), idea.get("risk_level", ""),
                    idea.get("reusable_program", ""), json.dumps(metadata, ensure_ascii=False), now, now,
                ),
            )
            idea_count += 1

        default_settings = {
            "timezone": "Asia/Shanghai",
            "reminder_offsets_days": [180, 90, 30, 14, 7, 2],
            "max_concurrent_campaigns": 2,
            "require_human_start_approval": True,
            "require_human_submission_approval": True,
            "positive_results_required": False,
        }
        for key, value in default_settings.items():
            connection.execute(
                "INSERT OR IGNORE INTO app_settings(key,value_json,updated_at) VALUES(?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), now),
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO resources(id,name,resource_type,capacity_json,availability_state,
              enabled,metadata_json,created_at,updated_at)
            VALUES('resource-unconfigured','Configure compute resources','unconfigured',?,
              'unconfigured',1,'{}',?,?)
            """,
            (json.dumps({"configured": False}), now, now),
        )
        offsets = (180, 90, 30, 14, 7, 2)
        deadlines = connection.execute(
            "SELECT d.id,d.deadline_date,d.evidence_status,d.forecast_window_start,"
            "v.id AS venue_id,v.display_name "
            "FROM deadlines d JOIN venues v ON v.id=d.venue_id"
        ).fetchall()
        existing_reminders = {
            row[0] for row in connection.execute("SELECT id FROM reminders").fetchall()
        }
        for deadline in deadlines:
            evidence_status = str(deadline["evidence_status"])
            if evidence_status == "official_confirmed":
                planning_cutoff = deadline["deadline_date"]
                planning_basis = "official_deadline_date"
            else:
                planning_cutoff = deadline["forecast_window_start"]
                planning_basis = "forecast_window_start"
                if not planning_cutoff:
                    raise ValueError(
                        f"Forecast deadline {deadline['id']} has no conservative lower bound"
                    )
            planning_date = datetime.fromisoformat(planning_cutoff)
            for offset in offsets:
                reminder_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"foundry:{deadline['id']}:{offset}")
                )
                local_day = (planning_date - timedelta(days=offset)).date()
                trigger_at = datetime.combine(
                    local_day, time(hour=9), tzinfo=timezone(timedelta(hours=8))
                ).astimezone(UTC).isoformat()
                connection.execute(
                    """
                    INSERT INTO reminders(id,venue_id,deadline_id,trigger_at,title,state,
                      payload_json,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?,?)
                    ON CONFLICT(id) DO UPDATE SET trigger_at=excluded.trigger_at,
                      title=excluded.title,payload_json=excluded.payload_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        reminder_id, deadline["venue_id"], deadline["id"], trigger_at,
                        (
                            f"{deadline['display_name']} planning reminder: {offset} days "
                            f"before {planning_basis}"
                        ),
                        json.dumps(
                            {
                                "offset_days": offset,
                                "evidence_status": evidence_status,
                                "planning_basis": planning_basis,
                                "planning_cutoff_date": planning_cutoff,
                                "point_estimate_deadline_date": deadline["deadline_date"],
                                "requires_official_confirmation": (
                                    evidence_status != "official_confirmed"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        now,
                        now,
                    ),
                )
                if reminder_id not in existing_reminders:
                    reminder_count += 1
                    existing_reminders.add(reminder_id)
    return {
        "venues": venue_count,
        "deadlines": deadline_count,
        "ideas": idea_count,
        "reminders_created": reminder_count,
    }
