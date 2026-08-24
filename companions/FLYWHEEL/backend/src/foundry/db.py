from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS venues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_key TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            official_name TEXT NOT NULL DEFAULT '',
            category_id TEXT NOT NULL DEFAULT '',
            category_zh TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
            conference_year INTEGER,
            deadline_date TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'AoE',
            round_note TEXT NOT NULL DEFAULT '',
            evidence_status TEXT NOT NULL DEFAULT 'forecast',
            forecast_window_start TEXT,
            forecast_window_end TEXT,
            confidence TEXT,
            source_url TEXT,
            requires_confirmation INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(venue_id, conference_year, deadline_date, round_note)
        );
        CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
            rank INTEGER NOT NULL,
            title_zh TEXT NOT NULL,
            problem_gap TEXT NOT NULL DEFAULT '',
            core_hypothesis TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT '',
            public_data_or_tasks TEXT NOT NULL DEFAULT '',
            strongest_baselines TEXT NOT NULL DEFAULT '',
            decisive_experiments TEXT NOT NULL DEFAULT '',
            compute_fit TEXT NOT NULL DEFAULT '',
            venue_fit_reason TEXT NOT NULL DEFAULT '',
            kill_criterion TEXT NOT NULL DEFAULT '',
            risk_level TEXT NOT NULL DEFAULT '',
            reusable_program TEXT NOT NULL DEFAULT '',
            differentiation TEXT NOT NULL DEFAULT '',
            freshness_state TEXT NOT NULL DEFAULT 'seeded',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(venue_id, rank)
        );
        CREATE TABLE IF NOT EXISTS connections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('local', 'remote')),
            base_url TEXT NOT NULL,
            token_ref TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'unknown',
            last_checked_at TEXT,
            last_error TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS resources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            capacity_json TEXT NOT NULL DEFAULT '{}',
            availability_state TEXT NOT NULL DEFAULT 'available',
            enabled INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY,
            venue_id INTEGER NOT NULL REFERENCES venues(id),
            idea_id INTEGER REFERENCES ideas(id),
            deadline_id INTEGER REFERENCES deadlines(id),
            connection_id TEXT REFERENCES connections(id),
            resource_id TEXT REFERENCES resources(id),
            title TEXT NOT NULL,
            objective TEXT NOT NULL DEFAULT '',
            argus_project_id TEXT,
            schedule_state TEXT NOT NULL DEFAULT 'manual',
            execution_state TEXT NOT NULL DEFAULT 'idle',
            science_state TEXT NOT NULL DEFAULT 'candidate',
            review_state TEXT NOT NULL DEFAULT 'not_requested',
            integrity_state TEXT NOT NULL DEFAULT 'unchecked',
            deadline_state TEXT NOT NULL DEFAULT 'on_track',
            progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 1),
            last_summary TEXT NOT NULL DEFAULT '',
            viewer_score REAL,
            reviewer_scores_json TEXT NOT NULL DEFAULT '[]',
            config_json TEXT NOT NULL DEFAULT '{}',
            scheduled_for TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            venue_id INTEGER REFERENCES venues(id) ON DELETE CASCADE,
            deadline_id INTEGER REFERENCES deadlines(id) ON DELETE CASCADE,
            campaign_id TEXT REFERENCES campaigns(id) ON DELETE CASCADE,
            trigger_at TEXT NOT NULL,
            title TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            reviewer_kind TEXT NOT NULL DEFAULT 'venue_reviewer',
            state TEXT NOT NULL DEFAULT 'queued',
            score REAL,
            recommendation TEXT,
            feedback_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            entity_type TEXT,
            entity_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_deadlines_date ON deadlines(deadline_date);
        CREATE INDEX IF NOT EXISTS idx_ideas_venue ON ideas(venue_id, rank);
        CREATE INDEX IF NOT EXISTS idx_campaigns_states ON campaigns(execution_state, review_state);
        CREATE INDEX IF NOT EXISTS idx_reminders_trigger ON reminders(state, trigger_at);
        CREATE INDEX IF NOT EXISTS idx_events_id_topic ON events(id, topic);
        """,
    ),
    (
        2,
        """
        ALTER TABLE campaigns ADD COLUMN process_alive INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE campaigns ADD COLUMN making_progress INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE campaigns ADD COLUMN snapshot_stale INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE campaigns ADD COLUMN last_progress_at TEXT;
        ALTER TABLE campaigns ADD COLUMN last_snapshot_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE campaigns ADD COLUMN launch_command_id TEXT;
        CREATE TABLE IF NOT EXISTS campaign_event_ingest (
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            external_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id, external_key)
        );
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS submission_records (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            venue_id INTEGER NOT NULL REFERENCES venues(id),
            paper_version TEXT NOT NULL,
            submission_ref TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','submitted','under_review','rebuttal','decided','withdrawn')),
            decision TEXT
                CHECK(decision IS NULL OR decision IN ('accept','reject','withdraw','pending','other')),
            submitted_at TEXT,
            decided_at TEXT,
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            pseudonymized INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(campaign_id, paper_version)
        );
        CREATE TABLE IF NOT EXISTS external_reviews (
            id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL REFERENCES submission_records(id) ON DELETE CASCADE,
            reviewer_alias TEXT NOT NULL,
            score REAL,
            score_label TEXT NOT NULL DEFAULT '',
            confidence REAL,
            recommendation TEXT NOT NULL DEFAULT '',
            feedback_redacted TEXT NOT NULL,
            questions_json TEXT NOT NULL DEFAULT '[]',
            redaction_confirmed INTEGER NOT NULL DEFAULT 0,
            source_kind TEXT NOT NULL DEFAULT 'human_entered',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(submission_id, reviewer_alias)
        );
        CREATE TABLE IF NOT EXISTS rebuttal_versions (
            id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL REFERENCES submission_records(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'objective_ready'
                CHECK(state IN ('objective_ready','campaign_created','drafting','human_review','finalized','superseded')),
            objective_sha256 TEXT NOT NULL,
            objective_path TEXT NOT NULL,
            campaign_id TEXT REFERENCES campaigns(id) ON DELETE SET NULL,
            human_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(submission_id, version),
            UNIQUE(objective_sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_submission_campaign ON submission_records(campaign_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_external_reviews_submission ON external_reviews(submission_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_rebuttal_submission ON rebuttal_versions(submission_id, version);
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS team_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            expertise_json TEXT NOT NULL DEFAULT '[]',
            methods_json TEXT NOT NULL DEFAULT '[]',
            data_access_json TEXT NOT NULL DEFAULT '[]',
            constraints_json TEXT NOT NULL DEFAULT '{}',
            goals_json TEXT NOT NULL DEFAULT '{}',
            policy_json TEXT NOT NULL DEFAULT '{}',
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ideation_runs (
            id TEXT PRIMARY KEY,
            team_profile_id TEXT NOT NULL REFERENCES team_profiles(id),
            venue_id INTEGER NOT NULL REFERENCES venues(id),
            deadline_id INTEGER REFERENCES deadlines(id),
            resource_id TEXT REFERENCES resources(id),
            connection_id TEXT REFERENCES connections(id),
            campaign_id TEXT REFERENCES campaigns(id) ON DELETE SET NULL,
            state TEXT NOT NULL DEFAULT 'objective_ready'
                CHECK(state IN ('objective_ready','campaign_created','running','awaiting_import','awaiting_labels','labeled','closed')),
            condition_schema_version INTEGER NOT NULL DEFAULT 1,
            condition_snapshot_json TEXT NOT NULL,
            objective_sha256 TEXT NOT NULL,
            objective_path TEXT NOT NULL,
            source_snapshot_ref TEXT NOT NULL DEFAULT '',
            source_snapshot_sha256 TEXT NOT NULL DEFAULT '',
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(objective_sha256)
        );
        CREATE TABLE IF NOT EXISTS generated_idea_candidates (
            id TEXT PRIMARY KEY,
            ideation_run_id TEXT NOT NULL REFERENCES ideation_runs(id) ON DELETE CASCADE,
            candidate_key TEXT NOT NULL,
            title TEXT NOT NULL,
            candidate_json TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            imported_from TEXT NOT NULL DEFAULT 'human_or_argus_artifact',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(ideation_run_id, candidate_key)
        );
        CREATE TABLE IF NOT EXISTS idea_labels (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES generated_idea_candidates(id) ON DELETE CASCADE,
            labeler_alias TEXT NOT NULL,
            decision TEXT NOT NULL
                CHECK(decision IN ('shortlist','revise','reject','abstain')),
            dimensions_json TEXT NOT NULL,
            rationale_redacted TEXT NOT NULL,
            redaction_confirmed INTEGER NOT NULL DEFAULT 0,
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(candidate_id, labeler_alias)
        );
        CREATE TABLE IF NOT EXISTS idea_pairwise_preferences (
            id TEXT PRIMARY KEY,
            ideation_run_id TEXT NOT NULL REFERENCES ideation_runs(id) ON DELETE CASCADE,
            left_candidate_id TEXT NOT NULL REFERENCES generated_idea_candidates(id) ON DELETE CASCADE,
            right_candidate_id TEXT NOT NULL REFERENCES generated_idea_candidates(id) ON DELETE CASCADE,
            winner TEXT NOT NULL CHECK(winner IN ('left','right','tie','abstain')),
            labeler_alias TEXT NOT NULL,
            rationale_redacted TEXT NOT NULL,
            redaction_confirmed INTEGER NOT NULL DEFAULT 0,
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            CHECK(left_candidate_id <> right_candidate_id),
            UNIQUE(ideation_run_id, left_candidate_id, right_candidate_id, labeler_alias)
        );
        CREATE INDEX IF NOT EXISTS idx_ideation_team_venue ON ideation_runs(team_profile_id, venue_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_generated_candidates_run ON generated_idea_candidates(ideation_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_idea_labels_candidate ON idea_labels(candidate_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_idea_preferences_run ON idea_pairwise_preferences(ideation_run_id, created_at);
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS research_episodes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'active'
                CHECK(state IN (
                    'active','paused','submitted','rebuttal','decided','closed',
                    'NO_WINNER','NOVELTY_COLLISION','RESOURCE_INFEASIBLE','NEGATIVE_RESULT',
                    'INCONCLUSIVE','KILLED','DEFERRED','POLICY_BLOCKED',
                    'SUBMISSION_READY_FOR_HUMAN_REVIEW','ACCEPTED','REJECTED','WITHDRAWN'
                )),
            team_profile_id TEXT REFERENCES team_profiles(id) ON DELETE SET NULL,
            venue_id INTEGER REFERENCES venues(id) ON DELETE SET NULL,
            deadline_id INTEGER REFERENCES deadlines(id) ON DELETE SET NULL,
            campaign_id TEXT REFERENCES campaigns(id) ON DELETE SET NULL,
            head_revision_id TEXT REFERENCES episode_revisions(id) ON DELETE RESTRICT,
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS episode_entity_links (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES research_episodes(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'related',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(episode_id, entity_type, entity_id, relation)
        );
        CREATE TABLE IF NOT EXISTS content_objects (
            sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
            media_type TEXT NOT NULL,
            byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
            storage_path TEXT NOT NULL UNIQUE,
            secret_scan_state TEXT NOT NULL DEFAULT 'passed'
                CHECK(secret_scan_state = 'passed'),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS episode_revisions (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES research_episodes(id) ON DELETE RESTRICT,
            revision_number INTEGER NOT NULL CHECK(revision_number > 0),
            parent_revision_id TEXT REFERENCES episode_revisions(id) ON DELETE RESTRICT,
            manifest_json TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256) = 64),
            chain_sha256 TEXT NOT NULL CHECK(length(chain_sha256) = 64),
            object_count INTEGER NOT NULL CHECK(object_count >= 0),
            reason TEXT NOT NULL,
            sealed_by TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            UNIQUE(episode_id, revision_number),
            UNIQUE(episode_id, manifest_sha256)
        );
        CREATE TABLE IF NOT EXISTS episode_revision_objects (
            revision_id TEXT NOT NULL REFERENCES episode_revisions(id) ON DELETE RESTRICT,
            object_sha256 TEXT NOT NULL REFERENCES content_objects(sha256) ON DELETE RESTRICT,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(revision_id, object_sha256, role)
        );
        CREATE TABLE IF NOT EXISTS team_intakes (
            id TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','confirmed')),
            raw_text TEXT NOT NULL,
            extracted_json TEXT NOT NULL,
            uncertainties_json TEXT NOT NULL DEFAULT '[]',
            confirmed_profile_json TEXT,
            team_profile_id TEXT REFERENCES team_profiles(id) ON DELETE SET NULL,
            confirmed_by TEXT,
            confirmed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_import_batches (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES research_episodes(id) ON DELETE RESTRICT,
            source_kind TEXT NOT NULL
                CHECK(source_kind IN ('paste','json','pdf','openreview')),
            source_ref TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','confirmed')),
            raw_payload_json TEXT NOT NULL,
            raw_object_sha256 TEXT REFERENCES content_objects(sha256) ON DELETE RESTRICT,
            parsed_json TEXT NOT NULL DEFAULT '{}',
            redaction_confirmed INTEGER NOT NULL DEFAULT 0,
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            confirmed_by TEXT,
            confirmed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dataset_snapshots (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            selection_json TEXT NOT NULL,
            selection_sha256 TEXT NOT NULL CHECK(length(selection_sha256) = 64),
            manifest_json TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256) = 64),
            member_count INTEGER NOT NULL CHECK(member_count > 0),
            license_basis TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(manifest_sha256)
        );
        CREATE TABLE IF NOT EXISTS dataset_snapshot_members (
            snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots(id) ON DELETE RESTRICT,
            revision_id TEXT NOT NULL REFERENCES episode_revisions(id) ON DELETE RESTRICT,
            episode_id TEXT NOT NULL REFERENCES research_episodes(id) ON DELETE RESTRICT,
            manifest_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, revision_id)
        );
        CREATE INDEX IF NOT EXISTS idx_episode_updated ON research_episodes(updated_at);
        CREATE INDEX IF NOT EXISTS idx_episode_links ON episode_entity_links(episode_id, entity_type);
        CREATE INDEX IF NOT EXISTS idx_episode_revisions ON episode_revisions(episode_id, revision_number);
        CREATE INDEX IF NOT EXISTS idx_review_import_episode ON review_import_batches(episode_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_snapshot_created ON dataset_snapshots(created_at);

        CREATE TRIGGER IF NOT EXISTS immutable_content_objects_update
        BEFORE UPDATE ON content_objects BEGIN
            SELECT RAISE(ABORT, 'content_objects are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_content_objects_delete
        BEFORE DELETE ON content_objects BEGIN
            SELECT RAISE(ABORT, 'content_objects are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_episode_revisions_update
        BEFORE UPDATE ON episode_revisions BEGIN
            SELECT RAISE(ABORT, 'episode_revisions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_episode_revisions_delete
        BEFORE DELETE ON episode_revisions BEGIN
            SELECT RAISE(ABORT, 'episode_revisions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_episode_revision_objects_update
        BEFORE UPDATE ON episode_revision_objects BEGIN
            SELECT RAISE(ABORT, 'episode_revision_objects are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_episode_revision_objects_insert_overflow
        BEFORE INSERT ON episode_revision_objects
        WHEN (SELECT COUNT(*) FROM episode_revision_objects WHERE revision_id=NEW.revision_id)
             >= (SELECT object_count FROM episode_revisions WHERE id=NEW.revision_id)
        BEGIN
            SELECT RAISE(ABORT, 'episode revision object set is sealed');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_episode_revision_objects_delete
        BEFORE DELETE ON episode_revision_objects BEGIN
            SELECT RAISE(ABORT, 'episode_revision_objects are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_dataset_snapshots_update
        BEFORE UPDATE ON dataset_snapshots BEGIN
            SELECT RAISE(ABORT, 'dataset_snapshots are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_dataset_snapshots_delete
        BEFORE DELETE ON dataset_snapshots BEGIN
            SELECT RAISE(ABORT, 'dataset_snapshots are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_dataset_snapshot_members_update
        BEFORE UPDATE ON dataset_snapshot_members BEGIN
            SELECT RAISE(ABORT, 'dataset_snapshot_members are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_dataset_snapshot_members_insert_overflow
        BEFORE INSERT ON dataset_snapshot_members
        WHEN (SELECT COUNT(*) FROM dataset_snapshot_members WHERE snapshot_id=NEW.snapshot_id)
             >= (SELECT member_count FROM dataset_snapshots WHERE id=NEW.snapshot_id)
        BEGIN
            SELECT RAISE(ABORT, 'dataset snapshot member set is sealed');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_dataset_snapshot_members_delete
        BEFORE DELETE ON dataset_snapshot_members BEGIN
            SELECT RAISE(ABORT, 'dataset_snapshot_members are immutable');
        END;
        """,
    ),
    (
        6,
        """
        ALTER TABLE ideation_runs ADD COLUMN candidate_artifact_sha256 TEXT NOT NULL DEFAULT ''
            CHECK(candidate_artifact_sha256 = '' OR length(candidate_artifact_sha256) = 64);
        ALTER TABLE generated_idea_candidates ADD COLUMN artifact_sha256 TEXT NOT NULL DEFAULT ''
            CHECK(artifact_sha256 = '' OR length(artifact_sha256) = 64);

        CREATE TRIGGER IF NOT EXISTS immutable_ideation_candidate_artifact_update
        BEFORE UPDATE OF candidate_artifact_sha256 ON ideation_runs
        WHEN OLD.candidate_artifact_sha256 <> ''
             AND NEW.candidate_artifact_sha256 <> OLD.candidate_artifact_sha256
        BEGIN
            SELECT RAISE(ABORT, 'ideation candidate artifact digest is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_generated_candidate_artifact_update
        BEFORE UPDATE OF artifact_sha256 ON generated_idea_candidates
        WHEN NEW.artifact_sha256 <> OLD.artifact_sha256
        BEGIN
            SELECT RAISE(ABORT, 'generated candidate artifact digest is immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS immutable_generated_candidates_update
        BEFORE UPDATE ON generated_idea_candidates BEGIN
            SELECT RAISE(ABORT, 'generated idea candidates are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_generated_candidates_delete
        BEFORE DELETE ON generated_idea_candidates BEGIN
            SELECT RAISE(ABORT, 'generated idea candidates are immutable');
        END;

        PRAGMA foreign_keys=OFF;
        DROP TRIGGER IF EXISTS immutable_content_objects_update;
        DROP TRIGGER IF EXISTS immutable_content_objects_delete;
        CREATE TABLE content_objects_v6 (
            sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
            media_type TEXT NOT NULL,
            byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
            storage_path TEXT NOT NULL UNIQUE,
            secret_scan_state TEXT NOT NULL DEFAULT 'passed'
                CHECK(secret_scan_state IN ('passed','not_scannable_binary')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            redaction_scan_state TEXT NOT NULL DEFAULT 'passed'
                CHECK(redaction_scan_state IN ('passed','not_scannable_binary')),
            manual_redaction_required INTEGER NOT NULL DEFAULT 0
                CHECK(manual_redaction_required IN (0,1))
        );
        INSERT INTO content_objects_v6(
            sha256,media_type,byte_length,storage_path,secret_scan_state,metadata_json,
            created_at,redaction_scan_state,manual_redaction_required
        )
        SELECT sha256,media_type,byte_length,storage_path,
               CASE
                   WHEN lower(media_type) LIKE 'text/%'
                     OR lower(media_type) LIKE 'application/json%'
                     OR lower(media_type) LIKE '%+json%'
                   THEN 'passed' ELSE 'not_scannable_binary'
               END,
               metadata_json,created_at,
               CASE
                   WHEN lower(media_type) LIKE 'text/%'
                     OR lower(media_type) LIKE 'application/json%'
                     OR lower(media_type) LIKE '%+json%'
                   THEN 'passed' ELSE 'not_scannable_binary'
               END,
               CASE
                   WHEN lower(media_type) LIKE 'text/%'
                     OR lower(media_type) LIKE 'application/json%'
                     OR lower(media_type) LIKE '%+json%'
                   THEN 0 ELSE 1
               END
        FROM content_objects;
        DROP TABLE content_objects;
        ALTER TABLE content_objects_v6 RENAME TO content_objects;
        CREATE TRIGGER IF NOT EXISTS immutable_content_objects_update
        BEFORE UPDATE ON content_objects BEGIN
            SELECT RAISE(ABORT, 'content_objects are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_content_objects_delete
        BEFORE DELETE ON content_objects BEGIN
            SELECT RAISE(ABORT, 'content_objects are immutable');
        END;
        PRAGMA foreign_keys=ON;

        ALTER TABLE review_import_batches RENAME TO review_import_batches_v5;
        CREATE TABLE review_import_batches (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES research_episodes(id) ON DELETE RESTRICT,
            source_kind TEXT NOT NULL
                CHECK(source_kind IN ('paste','json','pdf','openreview')),
            source_ref TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'draft'
                CHECK(state IN ('draft','confirmed','discarded')),
            raw_payload_json TEXT NOT NULL,
            raw_object_sha256 TEXT REFERENCES content_objects(sha256) ON DELETE RESTRICT,
            parsed_json TEXT NOT NULL DEFAULT '{}',
            redaction_confirmed INTEGER NOT NULL DEFAULT 0,
            training_consent INTEGER NOT NULL DEFAULT 0,
            license_basis TEXT NOT NULL DEFAULT '',
            confirmed_by TEXT,
            confirmed_at TEXT,
            discarded_by TEXT,
            discarded_at TEXT,
            discard_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO review_import_batches(
            id,episode_id,source_kind,source_ref,state,raw_payload_json,raw_object_sha256,
            parsed_json,redaction_confirmed,training_consent,license_basis,confirmed_by,
            confirmed_at,created_at,updated_at
        )
        SELECT id,episode_id,source_kind,source_ref,state,raw_payload_json,raw_object_sha256,
               parsed_json,redaction_confirmed,training_consent,license_basis,confirmed_by,
               confirmed_at,created_at,updated_at
        FROM review_import_batches_v5;
        DROP TABLE review_import_batches_v5;
        CREATE INDEX IF NOT EXISTS idx_review_import_episode
            ON review_import_batches(episode_id, created_at);
        CREATE TRIGGER IF NOT EXISTS immutable_confirmed_review_import_update
        BEFORE UPDATE ON review_import_batches
        WHEN OLD.state = 'confirmed'
        BEGIN
            SELECT RAISE(ABORT, 'confirmed review imports are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_discarded_review_import_update
        BEFORE UPDATE ON review_import_batches
        WHEN OLD.state = 'discarded'
        BEGIN
            SELECT RAISE(ABORT, 'discarded review imports are immutable');
        END;
        """,
    ),
    (
        7,
        """
        CREATE TABLE IF NOT EXISTS argus_artifact_imports (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES research_episodes(id) ON DELETE RESTRICT,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
            connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE RESTRICT,
            argus_project_id TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN (
                'condition_snapshot','prompt_contract','trajectory','experiment_spec',
                'experiment_result','paper','outcome','review_certificate',
                'integrity_report','reproducibility_manifest'
            )),
            state TEXT NOT NULL DEFAULT 'draft'
                CHECK(state IN ('draft','confirmed','discarded')),
            idempotency_key TEXT NOT NULL,
            source_entry_json TEXT NOT NULL,
            source_entry_sha256 TEXT NOT NULL CHECK(length(source_entry_sha256) = 64),
            source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
            source_byte_length INTEGER NOT NULL CHECK(source_byte_length >= 0),
            media_type TEXT NOT NULL,
            staging_key TEXT NOT NULL UNIQUE,
            scan_state TEXT NOT NULL
                CHECK(scan_state IN ('passed','requires_redaction','not_scannable_binary')),
            manual_redaction_required INTEGER NOT NULL DEFAULT 0
                CHECK(manual_redaction_required IN (0,1)),
            manual_redaction_confirmed INTEGER NOT NULL DEFAULT 0
                CHECK(manual_redaction_confirmed IN (0,1)),
            content_object_sha256 TEXT REFERENCES content_objects(sha256) ON DELETE RESTRICT,
            redaction_mode TEXT NOT NULL DEFAULT ''
                CHECK(redaction_mode IN ('','as_is','replace_text')),
            redaction_confirmed INTEGER NOT NULL DEFAULT 0
                CHECK(redaction_confirmed IN (0,1)),
            training_consent INTEGER NOT NULL DEFAULT 0
                CHECK(training_consent IN (0,1)),
            license_basis TEXT NOT NULL DEFAULT '',
            confirmed_by TEXT,
            confirmed_at TEXT,
            discarded_by TEXT,
            discarded_at TEXT,
            discard_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(episode_id, idempotency_key),
            CHECK(
                (state = 'draft' AND content_object_sha256 IS NULL
                    AND redaction_confirmed = 0 AND confirmed_by IS NULL AND confirmed_at IS NULL
                    AND manual_redaction_confirmed = 0
                    AND discarded_by IS NULL AND discarded_at IS NULL)
                OR
                (state = 'confirmed' AND content_object_sha256 IS NOT NULL
                    AND redaction_confirmed = 1 AND trim(license_basis) <> ''
                    AND (manual_redaction_required = 0 OR manual_redaction_confirmed = 1)
                    AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL
                    AND discarded_by IS NULL AND discarded_at IS NULL)
                OR
                (state = 'discarded' AND content_object_sha256 IS NULL
                    AND discarded_by IS NOT NULL AND discarded_at IS NOT NULL
                    AND trim(discard_reason) <> '')
            )
        );
        CREATE INDEX IF NOT EXISTS idx_argus_artifact_import_episode
            ON argus_artifact_imports(episode_id, state, created_at, id);
        CREATE INDEX IF NOT EXISTS idx_argus_artifact_import_campaign
            ON argus_artifact_imports(campaign_id, argus_project_id, artifact_path);

        CREATE TRIGGER IF NOT EXISTS immutable_argus_artifact_import_identity
        BEFORE UPDATE OF episode_id,campaign_id,connection_id,argus_project_id,artifact_path,
                         role,idempotency_key,source_entry_json,source_entry_sha256,
                         source_sha256,source_byte_length,media_type,staging_key
        ON argus_artifact_imports BEGIN
            SELECT RAISE(ABORT, 'Argus artifact import source identity is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_terminal_argus_artifact_import
        BEFORE UPDATE ON argus_artifact_imports
        WHEN OLD.state IN ('confirmed','discarded')
        BEGIN
            SELECT RAISE(ABORT, 'terminal Argus artifact imports are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_argus_artifact_import_delete
        BEFORE DELETE ON argus_artifact_imports BEGIN
            SELECT RAISE(ABORT, 'Argus artifact import audit rows are immutable');
        END;
        """,
    ),
    (
        8,
        """
        ALTER TABLE ideation_runs ADD COLUMN condition_sha256 TEXT NOT NULL DEFAULT ''
            CHECK(condition_sha256 = '' OR length(condition_sha256) = 64);
        ALTER TABLE ideation_runs ADD COLUMN candidate_manifest_json TEXT NOT NULL DEFAULT '{}';

        CREATE TRIGGER IF NOT EXISTS immutable_ideation_run_condition
        BEFORE UPDATE OF team_profile_id,venue_id,deadline_id,resource_id,connection_id,
                         condition_schema_version,condition_snapshot_json,objective_sha256,
                         objective_path,source_snapshot_ref,source_snapshot_sha256,
                         training_consent,license_basis
        ON ideation_runs
        WHEN NEW.team_profile_id IS NOT OLD.team_profile_id
          OR NEW.venue_id IS NOT OLD.venue_id
          OR NEW.deadline_id IS NOT OLD.deadline_id
          OR NEW.resource_id IS NOT OLD.resource_id
          OR NEW.connection_id IS NOT OLD.connection_id
          OR NEW.condition_schema_version IS NOT OLD.condition_schema_version
          OR NEW.condition_snapshot_json IS NOT OLD.condition_snapshot_json
          OR NEW.objective_sha256 IS NOT OLD.objective_sha256
          OR NEW.objective_path IS NOT OLD.objective_path
          OR NEW.source_snapshot_ref IS NOT OLD.source_snapshot_ref
          OR NEW.source_snapshot_sha256 IS NOT OLD.source_snapshot_sha256
          OR NEW.training_consent IS NOT OLD.training_consent
          OR NEW.license_basis IS NOT OLD.license_basis
        BEGIN
            SELECT RAISE(ABORT, 'ideation run frozen condition is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_ideation_run_condition_sha
        BEFORE UPDATE OF condition_sha256 ON ideation_runs
        WHEN OLD.condition_sha256 <> '' AND NEW.condition_sha256 <> OLD.condition_sha256
        BEGIN
            SELECT RAISE(ABORT, 'ideation run condition digest is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_ideation_candidate_manifest
        BEFORE UPDATE OF candidate_manifest_json ON ideation_runs
        WHEN OLD.candidate_manifest_json <> '{}'
             AND NEW.candidate_manifest_json <> OLD.candidate_manifest_json
        BEGIN
            SELECT RAISE(ABORT, 'ideation candidate manifest is immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS immutable_confirmed_team_intake_update
        BEFORE UPDATE ON team_intakes
        WHEN OLD.state = 'confirmed'
        BEGIN
            SELECT RAISE(ABORT, 'confirmed team intake is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_confirmed_team_intake_delete
        BEFORE DELETE ON team_intakes
        WHEN OLD.state = 'confirmed'
        BEGIN
            SELECT RAISE(ABORT, 'confirmed team intake is immutable');
        END;
        """,
    ),
    (
        9,
        """
        CREATE TABLE IF NOT EXISTS conditioned_campaign_bindings (
            campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id) ON DELETE RESTRICT,
            schema_version TEXT NOT NULL,
            ideation_run_id TEXT NOT NULL REFERENCES ideation_runs(id) ON DELETE RESTRICT,
            candidate_id TEXT NOT NULL REFERENCES generated_idea_candidates(id) ON DELETE RESTRICT,
            condition_sha256 TEXT NOT NULL CHECK(length(condition_sha256) = 64),
            parent_objective_sha256 TEXT NOT NULL CHECK(length(parent_objective_sha256) = 64),
            candidate_artifact_sha256 TEXT NOT NULL CHECK(length(candidate_artifact_sha256) = 64),
            candidate_record_sha256 TEXT NOT NULL CHECK(length(candidate_record_sha256) = 64),
            candidate_input_sha256 TEXT NOT NULL CHECK(length(candidate_input_sha256) = 64),
            candidate_prompt_sha256 TEXT NOT NULL CHECK(length(candidate_prompt_sha256) = 64),
            objective_path TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL UNIQUE CHECK(length(receipt_sha256) = 64),
            created_at TEXT NOT NULL,
            UNIQUE(candidate_id, candidate_prompt_sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_conditioned_campaign_run
            ON conditioned_campaign_bindings(ideation_run_id, candidate_id);
        CREATE TRIGGER IF NOT EXISTS immutable_conditioned_campaign_binding_update
        BEFORE UPDATE ON conditioned_campaign_bindings
        BEGIN
            SELECT RAISE(ABORT, 'conditioned campaign binding is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_conditioned_campaign_binding_delete
        BEFORE DELETE ON conditioned_campaign_bindings
        BEGIN
            SELECT RAISE(ABORT, 'conditioned campaign binding is immutable');
        END;
        """,
    ),
)


def _iter_sql_statements(script: str) -> Iterator[str]:
    """Yield complete SQLite statements without using ``executescript``.

    ``sqlite3.Connection.executescript`` commits an existing transaction before
    it runs. Executing complete statements individually lets one migration and
    its schema-version receipt share the same rollback boundary, including
    trigger bodies containing internal semicolons.
    """

    buffer: list[str] = []
    for character in script:
        buffer.append(character)
        if character == ";" and sqlite3.complete_statement("".join(buffer)):
            statement = "".join(buffer).strip()
            if statement:
                yield statement
            buffer = []
    trailing = "".join(buffer).strip()
    if trailing:
        raise RuntimeError("migration SQL ends with an incomplete statement")


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _backfill_ideation_condition_hashes(connection: sqlite3.Connection) -> None:
    """Bind legacy frozen snapshots to the digest required by schema v8.

    This runs inside the migration transaction.  Invalid legacy JSON aborts the
    migration instead of blessing an unverifiable condition snapshot.
    """

    rows = connection.execute(
        "SELECT id,condition_snapshot_json FROM ideation_runs WHERE condition_sha256=''"
    ).fetchall()
    for row in rows:
        try:
            snapshot = json.loads(row["condition_snapshot_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise sqlite3.IntegrityError(
                f"ideation run {row['id']} has invalid condition snapshot JSON"
            ) from exc
        digest = hashlib.sha256(_canonical_json_bytes(snapshot)).hexdigest()
        connection.execute(
            "UPDATE ideation_runs SET condition_sha256=? WHERE id=? AND condition_sha256=''",
            (digest, row["id"]),
        )


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.commit()
            for version, sql in MIGRATIONS:
                # v6 rebuilds referenced tables, so FK enforcement must be
                # disabled before BEGIN. Do this uniformly per migration and
                # prove consistency with foreign_key_check before commit.
                connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    already_applied = connection.execute(
                        "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
                    ).fetchone()
                    if not already_applied:
                        for statement in _iter_sql_statements(sql):
                            # PRAGMA foreign_keys inside a transaction is a
                            # documented no-op; the executor owns restoration.
                            if statement.strip().lower().startswith("pragma foreign_keys"):
                                continue
                            connection.execute(statement)
                        if version == 8:
                            _backfill_ideation_condition_hashes(connection)
                        violations = list(connection.execute("PRAGMA foreign_key_check"))
                        if violations:
                            raise sqlite3.IntegrityError(
                                f"migration {version} introduced foreign-key violations: "
                                f"{violations[:5]}"
                            )
                        connection.execute(
                            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                            (version, utc_now()),
                        )
                    connection.commit()
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
                finally:
                    if connection.in_transaction:
                        connection.rollback()
                    connection.execute("PRAGMA foreign_keys = ON")
                    restored = connection.execute("PRAGMA foreign_keys").fetchone()
                    if restored is None or int(restored[0]) != 1:
                        raise RuntimeError("failed to restore SQLite foreign-key enforcement")

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(sql, params)
            connection.commit()
            return int(cursor.lastrowid)

    def append_event(
        self,
        topic: str,
        event_type: str,
        *,
        severity: str = "info",
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        return self.execute(
            "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                topic,
                event_type,
                severity,
                entity_type,
                entity_id,
                json.dumps(payload or {}, ensure_ascii=False),
                utc_now(),
            ),
        )


JSON_COLUMNS = {
    "metadata_json": "metadata",
    "capacity_json": "capacity",
    "config_json": "config",
    "payload_json": "payload",
    "reviewer_scores_json": "reviewer_scores",
    "feedback_json": "feedback",
    "questions_json": "questions",
    "expertise_json": "expertise",
    "methods_json": "methods",
    "data_access_json": "data_access",
    "constraints_json": "constraints",
    "goals_json": "goals",
    "policy_json": "policy",
    "condition_snapshot_json": "condition_snapshot",
    "candidate_manifest_json": "candidate_manifest",
    "candidate_json": "candidate",
    "evidence_refs_json": "evidence_refs",
    "dimensions_json": "dimensions",
    "value_json": "value",
    "last_snapshot_json": "last_snapshot",
    "uncertainties_json": "uncertainties",
    "confirmed_profile_json": "confirmed_profile",
    "parsed_json": "parsed",
    "manifest_json": "manifest",
    "selection_json": "selection",
    "raw_payload_json": "raw_payload",
    "source_entry_json": "source_entry",
}


def decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    output = dict(row)
    for source, target in JSON_COLUMNS.items():
        if source in output:
            raw = output.pop(source)
            try:
                output[target] = json.loads(raw or "null")
            except (TypeError, json.JSONDecodeError):
                output[target] = None
    for key in (
        "enabled", "requires_confirmation", "process_alive", "making_progress", "snapshot_stale",
        "training_consent", "pseudonymized", "redaction_confirmed",
        "manual_redaction_required", "manual_redaction_confirmed"
    ):
        if key in output:
            output[key] = bool(output[key])
    return output


def decode_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [decode_row(row) or {} for row in rows]
