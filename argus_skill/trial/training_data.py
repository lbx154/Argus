"""Bounded, purpose-consented dataset packaging; no training or third-party I/O.

Only retained journal observations are inputs. HTTP request/response IDs establish
one observed chat turn, not the model's full context or the entire task history.
The current journal deliberately discards tool schemas/results: these observations
are diagnostics. Tool SFT can only come from consent-authorized forward
Pi collector, with exact schema/call/result/context verification and attributed review.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone

from argus_skill.core.secret_guard import redact_secrets_record

from .analytics import AnalyticsError, _private, _sanitize
from .research_controls import SID

NOTICE_VERSION = "training-data-v3-workspace"
COMBINED_NOTICE_VERSION = "operator-analytics-v3-workspace"
PURPOSES = ("internal_training", "external_sharing")
REVIEWER_KINDS = ("unspecified", "human_operator", "automated_acceptance")
MAX_PROJECTS = 20
MAX_EVENTS = 2000
MAX_PROJECT_EVENTS = 500
MAX_EVENT_BYTES = 20 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_EXPORT_BYTES = 32 * 1024 * 1024
LIMITATIONS = [
    "Global journey completeness is always unverified; gaps and truncation are explicit.",
    "Sequence is ingestion order, not causal order; runtime timestamps are untrusted.",
    "Chat samples are isolated HTTP turns; tool samples require a fresh verified Pi public episode.",
    "No private reasoning, invented successes, preferences, rollouts or tool schemas.",
    "Journal projections cannot supply tool SFT; only consent-authorized forward Pi capture can.",
    "Tool episodes omit system prompts; explicit review must confirm self-contained public context, with automated acceptance identified separately.",
    "Hosted workspace capture includes authorized read/write/edit/bash schemas, code, arguments and public results; other profiles retain their explicit allowlists.",
    "Runtime activities are observable labels, not reasoning or tool actions/results, and never SFT targets.",
    "Automatic filtering is imperfect; content, rights and provider licenses need human review.",
    "Packaging is not legal certification, anonymity assurance or a marketability guarantee.",
    "Revocation prevents future exports, but cannot recall copies already downloaded.",
]
_SENSITIVE = re.compile(
    r"(?ix)"
    r"\[REDACTED[^]]*\]|<redacted>|<\s*/?\s*(?:think|thinking|analysis)\b|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\b\d{3}-\d{2}-\d{4}\b|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"\b\d{1,6}\s+(?:[A-Z]+\s+){1,4}(?:street|avenue|road|lane|drive|boulevard)\b|"
    # Telephone indicators must be specific: decimal metrics and ordinary CSV
    # numbers are not evidence of a phone number. Plain unlabelled digit strings
    # remain ambiguous; do not quarantine scientific counts based on length alone.
    r"(?<![\w.])\+[1-9]\d{7,14}(?![\d.])|"
    r"(?<![\w.])\+[1-9]\d{0,2}[ -](?:\(?\d{1,4}\)?[ -]){0,3}\d{7,12}(?![\w.])|"
    r"(?<![\w.])\+[1-9]\d{0,2}[ .-](?:\(?\d{2,4}\)?[ .-]){1,3}\d{3,4}(?![\w.])|"
    r"(?<![\w.])(?:\(\d{3}\)[ -]?\d{3}[ -]\d{4}|\d{3}-\d{3}-\d{4}|"
    r"\d{3}\.\d{3}\.\d{4}|\d{3}[ ]\d{3}[ ]\d{4})(?![\w.])|"
    r"(?:\b(?:phone|mobile|telephone|tel|call[ ]me[ ]at)\b|手机号|手机|电话)"
    r"[\"']?[ \t]*[:：=,]?[ \t]*[\"']?(?:\+?\d[ -]*){7,15}(?!\d)|"
    r"(?:/home/|/Users/|/data/|/root/|/mnt/|[A-Z]:\\|\\\\)[^\s\"']+|"
    r"\b(?:sk-[A-Z0-9_-]{8,}|gh[pousr]_[A-Z0-9_]+|github_pat_[A-Z0-9_]+|"
    r"argus_trial_[A-Z0-9]+|AKIA[A-Z0-9]{16})\b|"
    r"\beyJ[A-Z0-9_-]+\.[A-Z0-9_-]+\.[A-Z0-9_-]+\b|"
    r"\b(?:bearer|basic)\s+[A-Z0-9+/=_-]+|"
    r"\b(?:api[_ -]?key|password|secret|access[_ -]?token|refresh[_ -]?token|"
    r"cookie|authorization|invitation[_ -]?code)\s*[:=]\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"https?://[^\s\"']+(?:\?[^\s\"']+|@[^\s\"']+)"
)



def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _sensitive(value):
    """Quarantine instead of silently changing training text or tool arguments."""
    def detect(item):
        if isinstance(item, str):
            return _SENSITIVE.search(item) is not None
        if isinstance(item, dict):
            return any(
                key.lower() in {"analysis", "thinking", "thoughts", "private", "internal",
                                "system", "systemprompt", "system_prompt"}
                or (key in {"role", "channel", "type", "kind"} and isinstance(val, str)
                    and val.lower() in {"analysis", "thinking", "private", "internal", "system", "developer"})
                or detect(key) or detect(val) for key, val in item.items()
            )
        if isinstance(item, list):
            return any(detect(val) for val in item)
        return False

    return (_private(value) or detect(value)
            or _sanitize(redact_secrets_record(value)) != value)


def _timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo else None
        except ValueError:
            return None
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _tool_observation(value):
    if isinstance(value, dict):
        return (bool(value.keys() & {"tool_calls", "tools", "tool_call_id", "tool_name", "call_id", "tool"})
                or (isinstance(value.get("kind"), str) and value["kind"] in {
                    "tool_call", "tool_result", "tool_use", "command_execution", "file_change",
                })
                or any(_tool_observation(item) for item in value.values()))
    if isinstance(value, list):
        return any(_tool_observation(item) for item in value)
    return False


def _step_issue(value):
    if isinstance(value, dict):
        if "steps_incomplete" in value and value["steps_incomplete"] is not False:
            return "runtime_steps_incomplete"
        if "steps" in value:
            steps = value["steps"]
            if not isinstance(steps, list) or len(steps) > 80 or any(
                not isinstance(step, dict) for step in steps
            ):
                return "malformed_runtime_steps"
            if any(step.get("status") not in ("completed", "done") for step in steps):
                return "runtime_steps_incomplete"
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return None
    for item in values:
        issue = _step_issue(item)
        if issue:
            return issue
    return None


class TrainingData:
    def __init__(self, analytics, journal, controls):
        self.analytics, self.journal, self.controls = analytics, journal, controls
        self._offline_team_tenants = set()
        with analytics._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS training_permissions (
                    tenant_id TEXT NOT NULL, purpose TEXT NOT NULL, notice_version TEXT NOT NULL,
                    granted INTEGER NOT NULL, granted_at REAL, updated_at REAL NOT NULL,
                    PRIMARY KEY(tenant_id,purpose)
                );
                CREATE TABLE IF NOT EXISTS training_permission_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
                    purpose TEXT NOT NULL, notice_version TEXT NOT NULL,
                    granted INTEGER NOT NULL, changed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS training_audit_counts (
                    audit_id INTEGER PRIMARY KEY, purpose TEXT,
                    projects INTEGER NOT NULL, sft INTEGER NOT NULL,
                    quarantined INTEGER NOT NULL, content_approved INTEGER NOT NULL,
                    rights_reviewed INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS training_review_evidence (
                    audit_id INTEGER PRIMARY KEY, reviewer_kind TEXT NOT NULL,
                    evidence_sha256 TEXT
                );
                CREATE TRIGGER IF NOT EXISTS training_review_evidence_prune
                AFTER DELETE ON research_audit BEGIN
                    DELETE FROM training_review_evidence WHERE audit_id=OLD.id;
                END;
                CREATE TABLE IF NOT EXISTS training_onboarding_acceptances (
                    tenant_id TEXT NOT NULL, research_notice_version TEXT NOT NULL,
                    training_notice_version TEXT NOT NULL, accepted_at REAL NOT NULL,
                    last_accepted_at REAL NOT NULL, reauthorized_at REAL,
                    PRIMARY KEY(tenant_id,research_notice_version,training_notice_version)
                );
                CREATE TABLE IF NOT EXISTS training_offline_authorizations (
                    tenant_id TEXT NOT NULL, research_notice_version TEXT NOT NULL,
                    training_notice_version TEXT NOT NULL, source TEXT NOT NULL,
                    recorded_at REAL NOT NULL, effective_at REAL, evidence_note TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,research_notice_version,training_notice_version)
                );
            """)
        from .training_capture import TrainingCapture

        self.capture = TrainingCapture(self)
        self._initialize_review_receipts()

    def _initialize_review_receipts(self):
        """Persist only provenance and hashes; source bodies keep their own retention."""
        with self.analytics._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS training_sample_reviews (
                    tenant_id TEXT NOT NULL, sid TEXT NOT NULL, purpose TEXT NOT NULL,
                    event_id TEXT NOT NULL, sample_sha256 TEXT NOT NULL,
                    reviewer_kind TEXT NOT NULL, evidence_sha256 TEXT, reviewed_at REAL NOT NULL,
                    grant_at REAL NOT NULL, notice_version TEXT NOT NULL,
                    context_approved INTEGER NOT NULL, source_event_ids TEXT NOT NULL,
                    source_episode_id INTEGER,
                    PRIMARY KEY(tenant_id,sid,purpose,event_id)
                );
                CREATE TRIGGER IF NOT EXISTS training_review_permission_change
                AFTER UPDATE ON training_permissions WHEN OLD.granted=1 AND (
                    NEW.granted!=1 OR NEW.notice_version!=OLD.notice_version OR NEW.granted_at IS NOT OLD.granted_at
                ) BEGIN
                    DELETE FROM training_sample_reviews WHERE tenant_id=OLD.tenant_id AND purpose=OLD.purpose;
                END;
                CREATE TRIGGER IF NOT EXISTS training_review_permission_delete
                AFTER DELETE ON training_permissions BEGIN
                    DELETE FROM training_sample_reviews WHERE tenant_id=OLD.tenant_id AND purpose=OLD.purpose;
                END;
                CREATE TRIGGER IF NOT EXISTS training_review_consent_delete
                AFTER DELETE ON consents BEGIN
                    DELETE FROM training_sample_reviews WHERE tenant_id=OLD.tenant_id AND notice_version=OLD.version;
                END;
                CREATE TRIGGER IF NOT EXISTS training_review_episode_delete
                AFTER DELETE ON training_tool_episodes BEGIN
                    DELETE FROM training_sample_reviews WHERE source_episode_id=OLD.id;
                END;
            """)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "journey_events" in tables:
                db.executescript("""
                    CREATE TRIGGER IF NOT EXISTS training_review_event_delete
                    AFTER DELETE ON journey_events BEGIN
                        DELETE FROM training_sample_reviews WHERE event_id=OLD.id OR EXISTS (
                            SELECT 1 FROM json_each(source_event_ids) WHERE value=OLD.id
                        );
                    END;
                """)
            if "journey_tombstones" in tables:
                db.executescript("""
                    CREATE TRIGGER IF NOT EXISTS training_review_project_delete
                    AFTER INSERT ON journey_tombstones BEGIN
                        DELETE FROM training_sample_reviews WHERE tenant_id=NEW.tenant_id AND sid=NEW.sid;
                    END;
                    CREATE TRIGGER IF NOT EXISTS training_review_project_delete_again
                    AFTER UPDATE ON journey_tombstones BEGIN
                        DELETE FROM training_sample_reviews WHERE tenant_id=NEW.tenant_id AND sid=NEW.sid;
                    END;
                """)

    def _restore_reviews(self, purpose, candidates, projects, explicitly_approved):
        restored = Counter()
        grants = {(project["tenant_id"], project["sid"]): project.get("granted_at")
                  for project in projects if project["eligible"]}
        cutoff = self.analytics.clock() - min(30, self.analytics.retention_days) * 86400
        with self.analytics._db() as db:
            db.execute("DELETE FROM training_sample_reviews WHERE reviewed_at<?", (cutoff,))
            for candidate in candidates:
                if candidate["event_id"] in explicitly_approved:
                    continue
                identity = (candidate["tenant_id"], candidate["sid"], purpose, candidate["event_id"])
                row = db.execute("""SELECT * FROM training_sample_reviews
                    WHERE tenant_id=? AND sid=? AND purpose=? AND event_id=?""", identity).fetchone()
                if row is None:
                    continue
                if (row["sample_sha256"] != _hash(_json(candidate["sample"]).encode())
                        or row["grant_at"] != grants.get(identity[:2])
                        or row["notice_version"] != self.analytics.notice_version
                        or row["reviewer_kind"] not in REVIEWER_KINDS
                        or ("tools" in candidate["sample"] and not row["context_approved"])):
                    db.execute("""DELETE FROM training_sample_reviews
                        WHERE tenant_id=? AND sid=? AND purpose=? AND event_id=?""", identity)
                    continue
                if not candidate["quality_approved"]:
                    restored["explicit_tool_context_review_required" if "tools" in candidate["sample"]
                             else "explicit_quality_review_required"] += 1
                candidate["quality_approved"] = True
                candidate["quality_evidence"] = {
                    "kind": "operator_tool_context_review" if "tools" in candidate["sample"] else "operator_event_review",
                    "event_id": candidate["event_id"], "persisted": True,
                    "reviewer_kind": row["reviewer_kind"], "human_reviewed": row["reviewer_kind"] == "human_operator",
                    "evidence_sha256": row["evidence_sha256"], "reviewed_at": row["reviewed_at"],
                    "sample_sha256": row["sample_sha256"],
                }
        return restored

    def _save_reviews(self, purpose, result, review):
        grants = {(project["tenant_id"], project["sid"]): project["granted_at"]
                  for project in result["projects"] if project["eligible"]}
        episodes = {row["event_id"]: row.get("episode_id") for row in result["provenance"]}
        approved = set(review.get("approved_event_ids", []))
        with self.analytics._db() as db:
            for candidate in result["candidates"]:
                if not candidate["quality_approved"] or candidate["event_id"] not in approved:
                    continue
                db.execute("""INSERT OR REPLACE INTO training_sample_reviews
                    (tenant_id,sid,purpose,event_id,sample_sha256,reviewer_kind,evidence_sha256,
                     reviewed_at,grant_at,notice_version,context_approved,source_event_ids,source_episode_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    candidate["tenant_id"], candidate["sid"], purpose, candidate["event_id"],
                    _hash(_json(candidate["sample"]).encode()), review.get("reviewer_kind", "unspecified"),
                    review.get("evidence_sha256"), self.analytics.clock(),
                    grants[candidate["tenant_id"], candidate["sid"]], self.analytics.notice_version,
                    int(review.get("tool_context_approved") is True), _json(candidate["event_ids"]),
                    episodes.get(candidate["event_id"]),
                ))

    def audit(self, action, purpose=None, *, outcome="requested", counts=None,
              review=None, actor="operator"):
        audit_id = self.controls.audit("training." + action, outcome=outcome, actor=actor)
        counts, review = counts or {}, review or {}
        with self.analytics._db() as db:
            db.execute("INSERT INTO training_audit_counts VALUES (?,?,?,?,?,?,?)", (
                audit_id, purpose if purpose in PURPOSES else None,
                counts.get("projects", 0), counts.get("sft", 0), counts.get("quarantined", 0),
                int(review.get("content_approved") is True),
                int(review.get("rights_reviewed") is True),
            ))
            if action.startswith("export"):
                kind = review.get("reviewer_kind", "unspecified")
                if kind not in REVIEWER_KINDS:
                    kind = "unspecified"
                digest = review.get("evidence_sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    digest = None
                db.execute("INSERT INTO training_review_evidence VALUES (?,?,?)", (audit_id, kind, digest))
        return audit_id

    def review_audit(self, limit=40):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Audit limit must be 1..100")
        with self.analytics._db() as db:
            return {"events": [dict(row) for row in db.execute("""
                SELECT a.id,a.created_at,a.actor,a.action,a.outcome,e.reviewer_kind,e.evidence_sha256,
                    c.purpose,c.projects,c.sft,c.quarantined
                FROM research_audit a JOIN training_review_evidence e ON e.audit_id=a.id
                LEFT JOIN training_audit_counts c ON c.audit_id=a.id
                ORDER BY a.id DESC LIMIT ?
            """, (limit,))]}

    def permissions(self, tenant_id):
        self.analytics._tenant(tenant_id)
        with self.analytics._db() as db:
            rows = {row["purpose"]: dict(row) for row in db.execute(
                "SELECT * FROM training_permissions WHERE tenant_id=?", (tenant_id,),
            )}
            onboarding = db.execute("""
                SELECT research_notice_version,training_notice_version,accepted_at,
                    last_accepted_at,reauthorized_at FROM training_onboarding_acceptances
                WHERE tenant_id=? AND research_notice_version=? AND training_notice_version=?
            """, (tenant_id, COMBINED_NOTICE_VERSION, NOTICE_VERSION)).fetchone()
            offline = self._offline_authority(db, tenant_id)
        current = {purpose: rows.get(purpose, {}) for purpose in PURPOSES}
        offline_enabled = tenant_id in self._offline_team_tenants
        usable = offline is None or offline_enabled
        result = {
            "notice_version": NOTICE_VERSION,
            **{purpose: bool(usable and row.get("granted") and row.get("notice_version") == NOTICE_VERSION
                            and not (offline_enabled and purpose == "external_sharing"))
               for purpose, row in current.items()},
            "granted_at": {purpose: row.get("granted_at") if row.get("granted")
                           and row.get("notice_version") == NOTICE_VERSION else None
                           for purpose, row in current.items()},
            "updated_at": {purpose: row.get("updated_at") for purpose, row in current.items()},
            "optional": False, "retroactive": False, "revocable": True,
            "consent_mode": "combined_onboarding",
            "requires_affirmative_acceptance": True, "preserve_existing_revocations": True,
            "defaults_after_acceptance": {"internal_training": True, "external_sharing": False},
            "optional_purposes": ["external_sharing"],
            "combined_notice_version": COMBINED_NOTICE_VERSION,
            "onboarding": dict(onboarding) if onboarding else None,
            "research_consent_current": self.analytics.consented(tenant_id, self.analytics.notice_version),
            "notice": {
                "internal_training": (
                    "Accepting the combined invitation-trial notice enables forward collection and use of "
                    "conversation text, hosted workspace code, exact tool schemas and arguments, "
                    "and bounded public results of read/write/edit/bash tools for internal model "
                    "training datasets. This purpose remains revocable in data permissions. "
                    "No system prompts or private reasoning; sensitive content is filtered or quarantined."
                ),
                "external_sharing": (
                    "Only a separate explicit choice enables forward collection and use of "
                    "conversation text, hosted workspace code, exact tool schemas and "
                    "arguments, and bounded public read/write/edit/bash results for "
                    "third-party datasets, including commercial supply. No system prompts or private reasoning; "
                    "sensitive content is filtered or quarantined. This purpose remains independently revocable. "
                    "Human content and rights review is required."
                ),
                "revocation": LIMITATIONS[-1],
                "review": LIMITATIONS[-3],
            },
        }
        if offline:
            result["authorization"] = offline
            result["authorization_active"] = offline_enabled
        if offline_enabled:
            result.update(
                consent_mode="operator_attested_offline", requires_affirmative_acceptance=False,
                defaults_after_acceptance={"internal_training": True, "external_sharing": False},
                optional_purposes=[], internal_only=True,
            )
            result["notice"]["internal_training"] = (
                "Internal-team use authorized offline according to the operator's recorded owner declaration. "
                "The historical effective date is unknown; collection/export eligibility begins no earlier "
                "than the server recording time. This is not an individual browser acceptance."
            )
            result["notice"]["external_sharing"] = "External sharing is not authorized by this internal-only policy."
        return result

    def _offline_authority(self, db, tenant_id):
        row = db.execute("""
            SELECT source,recorded_at,effective_at,evidence_note,research_notice_version,training_notice_version
            FROM training_offline_authorizations
            WHERE tenant_id=? AND research_notice_version=? AND training_notice_version=?
        """, (tenant_id, self.analytics.notice_version, NOTICE_VERSION)).fetchone()
        if row and not db.execute("""
            SELECT 1 FROM training_onboarding_acceptances WHERE tenant_id=?
            AND research_notice_version=? AND training_notice_version=? AND last_accepted_at>?
        """, (tenant_id, self.analytics.notice_version, NOTICE_VERSION, row["recorded_at"])).fetchone():
            return dict(row)
        return None

    def apply_offline_team_authorization(self, tenant_id, *, team_policy):
        """Trusted server configuration only; never map an HTTP body to this hook.

        team_policy = {mode: "internal_team_offline", tenant_ids: [...],
                       evidence_note: owner declaration}.
        The owner declaration is operational evidence, not a browser click or a
        signed historical date. Repeated calls preserve the recording boundary
        and manual revocations. No additional individual acceptance is required
        within this explicitly configured internal scope.
        """
        if (not isinstance(team_policy, dict) or set(team_policy) != {
            "mode", "tenant_ids", "evidence_note",
        } or team_policy["mode"] != "internal_team_offline"):
            raise ValueError("A server-private internal-team policy is required")
        tenants, note = team_policy["tenant_ids"], team_policy["evidence_note"]
        if (not isinstance(tenants, list) or not 1 <= len(tenants) <= 10
                or any(not isinstance(tenant, str) or tenant not in self.analytics.tenants for tenant in tenants)
                or tenant_id not in tenants):
            raise AnalyticsError(403, "outside_configured_internal_team")
        if not isinstance(note, str) or not note.strip() or len(note) > 2000 or _sensitive(note):
            raise ValueError("Provide a bounded, non-sensitive owner-declaration note")
        self._offline_team_tenants = set(tenants)
        now = self.analytics.clock()
        changed = False
        with self.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute("""
                SELECT recorded_at FROM training_offline_authorizations
                WHERE tenant_id=? AND research_notice_version=? AND training_notice_version=?
            """, (tenant_id, self.analytics.notice_version, NOTICE_VERSION)).fetchone()
            if receipt is None:
                db.execute("INSERT INTO training_offline_authorizations VALUES (?,?,?,?,?,?,?)", (
                    tenant_id, self.analytics.notice_version, NOTICE_VERSION,
                    "operator_attested_offline", now, None, note.strip(),
                ))
                changed = True
            recorded_at = receipt["recorded_at"] if receipt else now
            # Existing recorder authorization index; the separate receipt above
            # identifies the source as offline, not an individual online click.
            db.execute("INSERT OR IGNORE INTO consents VALUES (?,?,?)",
                       (tenant_id, self.analytics.notice_version, recorded_at))
            for purpose in PURPOSES:
                old = db.execute(
                    "SELECT * FROM training_permissions WHERE tenant_id=? AND purpose=?",
                    (tenant_id, purpose),
                ).fetchone()
                if receipt and old and old["notice_version"] == NOTICE_VERSION and (
                    purpose == "internal_training" or not old["granted"]
                ):
                    continue
                enabled = purpose == "internal_training" and (old is None or bool(old["granted"]))
                db.execute("""
                    INSERT INTO training_permissions VALUES (?,?,?,?,?,?)
                    ON CONFLICT(tenant_id,purpose) DO UPDATE SET
                        notice_version=excluded.notice_version,granted=excluded.granted,
                        granted_at=excluded.granted_at,updated_at=excluded.updated_at
                """, (tenant_id, purpose, NOTICE_VERSION, int(enabled), now if enabled else None, now))
                db.execute("""
                    INSERT INTO training_permission_history
                    (tenant_id,purpose,notice_version,granted,changed_at) VALUES (?,?,?,?,?)
                """, (tenant_id, purpose, NOTICE_VERSION, int(enabled), now))
                if old and old["granted"]:
                    db.execute("DELETE FROM training_tool_episodes WHERE tenant_id=?", (tenant_id,))
                changed = True
        if changed:
            self.audit("offline_authorization", "internal_training", outcome="completed", actor="operator")
        return self.permissions(tenant_id)

    def accept_onboarding(self, tenant_id, research_notice_version, *, accepted,
                          external_sharing=False, reauthorize=False, record_research=False):
        """Trusted portal hook for genuine, disclosed notice acceptance.

        Never call from session restoration or GET. Ordinary accepted logins
        preserve prior refusals/revocations, including those under older versions.
        external_sharing=True is a separate affirmative choice and can reauthorize
        that purpose alone; it never re-enables a revoked internal-training grant.
        reauthorize=True requires a separately explicit reauthorization action;
        it is not implied by logging in or ticking the usual onboarding checkbox.
        All newly enabled grants use this server-side acceptance time, never the
        timestamp of a legacy research receipt or a client-supplied timestamp.
        record_research=True is only for the authenticated, same-origin notice
        POST after validating its actual checked acceptance and disclosed version.
        It records receipts atomically; it is not proof of acceptance and must
        never be used to infer acceptance from invitation redemption or access.
        """
        self.analytics._tenant(tenant_id)
        if (accepted is not True or type(reauthorize) is not bool or type(record_research) is not bool
                or type(external_sharing) is not bool):
            raise AnalyticsError(400, "affirmative_combined_acceptance_required")
        if (research_notice_version != COMBINED_NOTICE_VERSION
                or self.analytics.notice_version != COMBINED_NOTICE_VERSION):
            raise AnalyticsError(409, "combined_notice_version_mismatch")
        if external_sharing and tenant_id in self._offline_team_tenants:
            raise AnalyticsError(403, "offline_policy_internal_only")
        now = self.analytics.clock()
        with self.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if record_research:
                db.execute("INSERT OR IGNORE INTO consents VALUES (?,?,?)",
                           (tenant_id, research_notice_version, now))
            if not db.execute("SELECT 1 FROM consents WHERE tenant_id=? AND version=?",
                              (tenant_id, research_notice_version)).fetchone():
                raise AnalyticsError(403, "research_consent_required")
            previous_offline = self._offline_authority(db, tenant_id)
            for purpose in PURPOSES:
                old = db.execute(
                    "SELECT * FROM training_permissions WHERE tenant_id=? AND purpose=?",
                    (tenant_id, purpose),
                ).fetchone()
                requested = purpose == "internal_training" or external_sharing
                explicit_external = purpose == "external_sharing" and external_sharing
                enabled = bool(requested and (reauthorize or explicit_external or old is None or old["granted"]))
                current = old and old["notice_version"] == NOTICE_VERSION and previous_offline is None
                if current and not reauthorize and not (explicit_external and not old["granted"]):
                    continue
                grant_at = now if enabled else None
                db.execute("""
                    INSERT INTO training_permissions VALUES (?,?,?,?,?,?)
                    ON CONFLICT(tenant_id,purpose) DO UPDATE SET
                        notice_version=excluded.notice_version,granted=excluded.granted,
                        granted_at=excluded.granted_at,updated_at=excluded.updated_at
                """, (tenant_id, purpose, NOTICE_VERSION, int(enabled), grant_at, now))
                db.execute("""
                    INSERT INTO training_permission_history
                    (tenant_id,purpose,notice_version,granted,changed_at) VALUES (?,?,?,?,?)
                """, (tenant_id, purpose, NOTICE_VERSION, int(enabled), now))
                if old and old["granted"]:
                    db.execute("DELETE FROM training_tool_episodes WHERE tenant_id=?", (tenant_id,))
            db.execute("""
                INSERT INTO training_onboarding_acceptances VALUES (?,?,?,?,?,?)
                ON CONFLICT(tenant_id,research_notice_version,training_notice_version)
                DO UPDATE SET last_accepted_at=excluded.last_accepted_at,
                    reauthorized_at=coalesce(excluded.reauthorized_at,
                                            training_onboarding_acceptances.reauthorized_at)
            """, (tenant_id, research_notice_version, NOTICE_VERSION, now, now,
                  now if reauthorize else None))
        self.audit("onboarding", outcome="completed", actor="tester")
        return self.permissions(tenant_id)

    def set_permissions(self, tenant_id, data):
        self.analytics._tenant(tenant_id)
        if not isinstance(data, dict) or set(data) != {*PURPOSES, "notice_version"}:
            raise ValueError("Provide internal_training, external_sharing and notice_version")
        if data["notice_version"] != NOTICE_VERSION:
            raise AnalyticsError(409, "training_notice_version_mismatch")
        if any(type(data[purpose]) is not bool for purpose in PURPOSES):
            raise ValueError("Permissions require explicit JSON booleans")
        if data["external_sharing"] and tenant_id in self._offline_team_tenants:
            raise AnalyticsError(403, "offline_policy_internal_only")
        if any(data[purpose] for purpose in PURPOSES):
            self.analytics._require_consent(tenant_id)
        now = self.analytics.clock()
        with self.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for purpose in PURPOSES:
                old = db.execute(
                    "SELECT * FROM training_permissions WHERE tenant_id=? AND purpose=?",
                    (tenant_id, purpose),
                ).fetchone()
                if old and old["granted"] and (
                    not data[purpose] or old["notice_version"] != NOTICE_VERSION
                ):
                    db.execute("DELETE FROM training_tool_episodes WHERE tenant_id=?", (tenant_id,))
                continuing = old and old["granted"] and old["notice_version"] == NOTICE_VERSION
                granted_at = (old["granted_at"] if continuing else now) if data[purpose] else None
                db.execute("""
                    INSERT INTO training_permissions VALUES (?,?,?,?,?,?)
                    ON CONFLICT(tenant_id,purpose) DO UPDATE SET
                        notice_version=excluded.notice_version,granted=excluded.granted,
                        granted_at=excluded.granted_at,updated_at=excluded.updated_at
                """, (tenant_id, purpose, NOTICE_VERSION, int(data[purpose]), granted_at, now))
                db.execute("""
                    INSERT INTO training_permission_history
                    (tenant_id,purpose,notice_version,granted,changed_at) VALUES (?,?,?,?,?)
                """, (tenant_id, purpose, NOTICE_VERSION, int(data[purpose]), now))
        self.audit("permissions", outcome="completed", actor="tester")
        return self.permissions(tenant_id)

    def _active(self, tenant):
        # Read only access metadata, never credentials or runtime files.
        if not self.analytics.trial_db.is_file():
            return False
        with closing(sqlite3.connect(
            self.analytics.trial_db.absolute().as_uri() + "?mode=ro", uri=True,
        )) as db:
            row = db.execute("""
                SELECT coalesce(a.enabled,1),a.expires_at
                FROM trial_keys k LEFT JOIN trial_access a ON a.key_id=k.key_id
                WHERE k.key_id=?
            """, (tenant,)).fetchone()
        return bool(row and row[0] and (row[1] is None or row[1] > self.analytics.clock()))

    def _grant(self, db, tenant, sid, purpose):
        offline = self._offline_authority(db, tenant)
        if offline and tenant not in self._offline_team_tenants:
            return None, "offline_team_policy_not_active"
        if tenant in self._offline_team_tenants and purpose == "external_sharing":
            return None, "offline_policy_internal_only"
        if not self._active(tenant):
            return None, "tester_access_inactive"
        if not db.execute("SELECT 1 FROM consents WHERE tenant_id=? AND version=?",
                          (tenant, self.analytics.notice_version)).fetchone():
            return None, "research_consent_required"
        if db.execute("SELECT 1 FROM journey_tombstones WHERE tenant_id=? AND sid=?",
                      (tenant, sid)).fetchone():
            return None, "research_deleted"
        row = db.execute(
            "SELECT * FROM training_permissions WHERE tenant_id=? AND purpose=?",
            (tenant, purpose),
        ).fetchone()
        if not row or not row["granted"] or row["notice_version"] != NOTICE_VERSION:
            return None, "purpose_consent_required"
        return row["granted_at"], None

    def _selection(self, projects):
        if not isinstance(projects, list) or not 1 <= len(projects) <= MAX_PROJECTS:
            raise ValueError(f"Select 1..{MAX_PROJECTS} projects")
        selected = []
        for row in projects:
            if not isinstance(row, dict) or set(row) != {"tenant_id", "sid"}:
                raise ValueError("Each project requires tenant_id and sid")
            if any(not isinstance(value, str) or not SID.fullmatch(value) or _sensitive(value)
                   for value in row.values()):
                raise ValueError("Invalid project identity")
            self.analytics._tenant(row["tenant_id"])
            selected.append((row["tenant_id"], row["sid"]))
        return sorted(set(selected))

    def _collect(self, purpose, projects, approved, context_approved=False):
        if purpose not in PURPOSES:
            raise ValueError("Unknown training purpose")
        selected = self._selection(projects)
        if self.journal is None:
            raise AnalyticsError(503, "training_journal_unavailable")
        self.capture.prune()
        now = self.analytics.clock()
        cutoff = now - min(30, self.analytics.retention_days) * 86400
        output, candidates, trajectories, provenance = [], [], [], []
        reasons, used, examined = Counter(), 0, 0
        with self.analytics._db() as db:
            db.execute("BEGIN")
            for tenant, sid in selected:
                grant, denied = self._grant(db, tenant, sid, purpose)
                project = {"tenant_id": tenant, "sid": sid, "eligible": denied is None}
                output.append(project)
                if denied:
                    project["reason"] = denied
                    reasons[denied] += 1
                    continue
                authority = self._offline_authority(db, tenant)
                if authority:
                    project["authorization"] = authority
                state = db.execute(
                    "SELECT pruned_events FROM journey_projects WHERE tenant_id=? AND sid=? "
                    "AND notice_version=?", (tenant, sid, self.analytics.notice_version),
                ).fetchone()
                if state is None:
                    project.update(eligible=False, reason="journal_not_found")
                    reasons["journal_not_found"] += 1
                    continue
                rows = db.execute("""
                    SELECT sequence,id,ingested_at,length(record) AS size,
                        substr(record,1,?) AS record FROM journey_events
                    WHERE tenant_id=? AND sid=? AND notice_version=? AND ingested_at>=?
                    ORDER BY sequence LIMIT ?
                """, (MAX_EVENT_BYTES + 1, tenant, sid, self.analytics.notice_version,
                      max(grant, cutoff), MAX_PROJECT_EVENTS + 1)).fetchall()
                if len(rows) > MAX_PROJECT_EVENTS:
                    raise AnalyticsError(413, "training_project_event_limit")
                project.update(granted_at=grant, retained_events=len(rows),
                               pruned_events=state["pruned_events"], complete=False)
                if not rows:
                    reasons["no_postgrant_retained_events"] += 1
                contexts, pairs, blocked_interactions = [], defaultdict(list), set()
                for row in rows:
                    examined += 1
                    used += min(row["size"], MAX_EVENT_BYTES + 1)
                    if examined > MAX_EVENTS or used > MAX_SOURCE_BYTES:
                        raise AnalyticsError(413, "training_source_size_limit")
                    reason = None
                    event = None
                    try:
                        if row["size"] > MAX_EVENT_BYTES:
                            raise ValueError
                        event = json.loads(row["record"])
                        if (not isinstance(event, dict) or event.get("id") != row["id"]
                                or event.get("tenant_id") != tenant or event.get("sid") != sid
                                or event.get("notice_version") != self.analytics.notice_version
                                or not isinstance(event.get("payload"), dict)
                                or not isinstance(event.get("source"), dict)
                                or not isinstance(event.get("kind"), str)
                                or (event.get("task_id") is not None
                                    and not isinstance(event["task_id"], str))
                                or (event.get("lifecycle") is not None
                                    and not isinstance(event["lifecycle"], str))
                                or not isinstance(event.get("association"), dict)
                                or not isinstance(event.get("warnings"), list)
                                or any(not isinstance(w, str) for w in event["warnings"])):
                            raise ValueError
                        event["sequence"] = row["sequence"]
                        stamp = _timestamp(event.get("source_timestamp"))
                        if event.get("source_kind") == "journal_gap":
                            reason = "journal_recording_gap"
                        elif stamp is None or not grant <= stamp <= row["ingested_at"] <= now:
                            reason = "content_time_unverified_or_before_grant"
                        else:
                            contexts.append({
                                key: event.get(key) for key in (
                                    "id", "kind", "source_timestamp", "task_id", "lifecycle",
                                )
                            })
                            if _sensitive(event):
                                reason = "sensitive_or_private_content"
                    except (ValueError, TypeError, RecursionError, OverflowError):
                        reason = "malformed_or_oversized_event"
                    origin = {
                        "event_id": row["id"], "tenant_id": tenant, "sid": sid,
                        "sequence": row["sequence"], "ingested_at": row["ingested_at"],
                        "consent": {"purpose": purpose, "notice_version": NOTICE_VERSION,
                                    "granted_at": grant},
                    }
                    if authority:
                        origin["authorization"] = authority
                    if reason:
                        reasons[reason] += 1
                        provenance.append({**origin, "disposition": "quarantined", "reason": reason})
                        if isinstance(event, dict) and isinstance(event.get("source"), dict):
                            interaction_id = event["source"].get("interaction_id")
                            if type(interaction_id) is int:
                                blocked_interactions.add(interaction_id)
                        continue
                    # Keep canonical IDs/associations, never raw unrelated source files.
                    trajectories.append(event)
                    entry = {**origin, "source": event["source"],
                             "association": event.get("association"), "disposition": "diagnostic"}
                    provenance.append(entry)
                    source = event["source"]
                    if event.get("source_kind") == "http_interaction" and (
                        type(source.get("interaction_id")) is int
                        and event.get("kind") in {"http.request", "http.response"}
                        and source.get("phase") == event["kind"].split(".")[1]
                    ):
                        pairs[source["interaction_id"]].append(event)
                    issue = _step_issue(event["payload"])
                    if issue or _tool_observation(event["payload"]):
                        reason = issue or "tool_schema_context_unavailable"
                        reasons[reason] += 1
                        entry.update(disposition="quarantined", reason=reason,
                                     safe_diagnostic_retained=True)
                        if type(source.get("interaction_id")) is int:
                            blocked_interactions.add(source["interaction_id"])
                    elif event["warnings"]:
                        entry["reason"] = "source_warnings_or_truncation"
                        reasons["source_warnings_or_truncation"] += 1
                for interaction_id, pair in pairs.items():
                    if interaction_id in blocked_interactions:
                        reasons["quarantined_turn_member"] += 1
                        continue
                    item, reason = self._chat(pair, contexts, db, tenant, sid, grant, cutoff, approved)
                    if reason:
                        reasons[reason] += 1
                    if item:
                        candidates.append(item)
                tool_candidates, tool_trajectories, tool_provenance = self.capture.collect(
                    db, tenant, sid, purpose, grant, approved, context_approved,
                )
                used += len(_json(tool_trajectories))
                examined += sum(len(episode["events"]) for episode in tool_trajectories)
                if used > MAX_SOURCE_BYTES or examined > MAX_EVENTS:
                    raise AnalyticsError(413, "training_source_size_limit")
                candidates.extend(tool_candidates)
                trajectories.extend(tool_trajectories)
                provenance.extend(tool_provenance)
                for origin in tool_provenance:
                    if authority:
                        origin["authorization"] = authority
                    if origin.get("reason"):
                        reasons[origin["reason"]] += 1
                if tool_candidates and not context_approved:
                    reasons["explicit_tool_context_review_required"] += len(tool_candidates)
        # Restore metadata only after the normal consent, retention and source checks.
        restored = self._restore_reviews(purpose, candidates, output, approved)
        for reason, count in restored.items():
            reasons[reason] = max(0, reasons[reason] - count)
            if not reasons[reason]:
                del reasons[reason]
        # Equal content is retained once; all source observations remain traceable.
        seen = {}
        for candidate in candidates:
            digest = _hash(_json(candidate["sample"]).encode())
            candidate["sample_id"] = digest
            if digest in seen:
                earlier = seen[digest]
                if candidate["quality_approved"] and not earlier["quality_approved"]:
                    earlier["duplicate_of"] = candidate["event_id"]
                    seen[digest] = candidate
                else:
                    candidate["duplicate_of"] = earlier["event_id"]
                    candidate["quality_approved"] = False
                reasons["duplicate_sample"] += 1
            else:
                seen[digest] = candidate
        self._splits(candidates)
        counts = {
            "projects": len(selected), "eligible_projects": sum(p["eligible"] for p in output),
            "events": len(trajectories), "candidates": len(candidates),
            "sft": sum(c["quality_approved"] for c in candidates),
            "quarantined": sum(p["disposition"] == "quarantined" for p in provenance),
            "duplicates": reasons["duplicate_sample"],
            "tool_candidates": sum("tools" in c["sample"] for c in candidates),
            "tool_sft": sum(c["quality_approved"] and "tools" in c["sample"] for c in candidates),
        }
        return {"projects": output, "candidates": candidates, "trajectories": trajectories,
                "provenance": provenance, "counts": counts, "reason_counts": dict(reasons)}

    def _chat(self, pair, events, db, tenant, sid, grant, cutoff, approved):
        request = [e for e in pair if e["kind"] == "http.request"]
        response = [e for e in pair if e["kind"] == "http.response"]
        if len(request) != 1 or len(response) != 1:
            return None, "unmatched_http_turn"
        request, response = request[0], response[0]
        start, end = _timestamp(request["source_timestamp"]), _timestamp(response["source_timestamp"])
        payload = response["payload"]
        result = payload.get("result")
        if (request["warnings"] or response["warnings"] or start > end
                or payload.get("state") != "response_complete" or payload.get("outcome") != "chat_response"
                or type(payload.get("status_code")) is not int
                or not 200 <= payload["status_code"] < 300 or payload.get("error_code")
                or not isinstance(result, dict) or result.get("kind") != "chat"
                or result.get("success") is False):
            return None, "incomplete_or_nonchat_turn"
        input_data = request["payload"].get("input")
        text = input_data.get("text") if isinstance(input_data, dict) else None
        reply = result.get("reply")
        if not isinstance(text, str) or not text.strip() or not isinstance(reply, str) or not reply.strip():
            return None, "missing_conversation_text"
        if request["payload"].get("path") not in {
            "/api/projects/:sid/message", "/api/projects/:sid/message/stream",
        }:
            return None, "unsupported_conversation_route"
        if request["payload"].get("path") != payload.get("path"):
            return None, "mismatched_conversation_route"
        # Never concatenate independent/interleaved streams or infer causal task IDs.
        if any(e["kind"] == "http.request" and e["id"] != request["id"]
               and start <= _timestamp(e["source_timestamp"]) <= end for e in events):
            return None, "overlapping_turns"
        task = response.get("task_id")
        authoritative = isinstance(response.get("association"), dict) and (
            response["association"].get("kind") == "authoritative"
        )
        if any(
            (e.get("lifecycle") in {"fail", "cancel", "pause"} or e["kind"] == "user.correction")
            and (not e.get("task_id") or e.get("task_id") == task)
            and _timestamp(e["source_timestamp"]) >= start for e in events
        ):
            return None, "failed_or_corrected_trajectory"
        evidence = None
        if task and authoritative:
            feedback = db.execute("""
                SELECT id,verdict FROM research_feedback WHERE tenant_id=? AND sid=?
                AND task_id=? AND created_at>=? ORDER BY created_at DESC,id DESC LIMIT 1
            """, (tenant, sid, task, max(end, grant, cutoff))).fetchone()
            if feedback and feedback["verdict"] == "met_need":
                evidence = {"kind": "explicit_task_feedback", "feedback_id": feedback["id"]}
            elif feedback and feedback["verdict"] == "needs_changes":
                return None, "human_requested_changes"
        if response["id"] in approved:
            evidence = {"kind": "operator_event_review", "event_id": response["id"]}
        return {
            "tenant_id": tenant, "sid": sid, "event_id": response["id"],
            "event_ids": [request["id"], response["id"]],
            "task_id": task if authoritative else None,
            "sample": {"messages": [{"role": "user", "content": text},
                                    {"role": "assistant", "content": reply}]},
            "quality_approved": evidence is not None, "quality_evidence": evidence,
            "sample_complete": True, "global_complete": False,
            "scope": "isolated_observed_chat_turn",
        }, None if evidence else "explicit_quality_review_required"

    @staticmethod
    def _splits(candidates):
        # Tester grouping is stricter than project grouping. Shared exact prompts
        # also connect testers, preventing the same observed task crossing splits.
        groups = {c["tenant_id"]: c["tenant_id"] for c in candidates}

        def root(key):
            while groups[key] != key:
                key = groups[key]
            return key

        prompts = {}
        for candidate in candidates:
            prompt = candidate["sample"]["messages"][0]["content"]
            tenant = candidate["tenant_id"]
            if prompt in prompts:
                left, right = sorted((root(tenant), root(prompts[prompt])))
                groups[right] = left
            prompts[prompt] = tenant
        roots = {root(key) for key in groups}
        for candidate in candidates:
            group = _hash(root(candidate["tenant_id"]).encode())
            candidate["split_group"] = group
            candidate["split"] = (
                "validation" if len(roots) > 1 and int(group[:8], 16) % 10 == 0 else "train"
            )

    def preview(self, purpose, projects=None, *, offset=0, tenant=None, query=""):
        self.audit("preview", purpose)
        try:
            if purpose not in PURPOSES:
                raise ValueError("Unknown training purpose")
            if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
                raise ValueError("Invalid project offset")
            if not isinstance(query, str) or len(query) > 160:
                raise ValueError("Project search must be at most 160 characters")
            if tenant is not None:
                self.analytics._tenant(tenant)
            if projects is not None and (offset or tenant is not None or query):
                raise ValueError("Explicit project selection cannot use browse filters")
            if projects is not None:
                self._selection(projects)
            total_projects = len(projects) if isinstance(projects, list) else 0
            if projects is None:
                if self.journal is None:
                    raise AnalyticsError(503, "training_journal_unavailable")
                tenants = [tenant] if tenant is not None else sorted(self.analytics.tenants)
                placeholders = ",".join("?" for _ in tenants) or "NULL"
                where = (f"notice_version=? AND tenant_id IN ({placeholders}) "
                         "AND instr(sid,?) > 0")
                args = (self.analytics.notice_version, *tenants, query)
                with self.analytics._db() as db:
                    total_projects = db.execute(
                        f"SELECT count(*) FROM journey_projects WHERE {where}", args,
                    ).fetchone()[0]
                    projects = [dict(row) for row in db.execute(
                        f"SELECT tenant_id,sid FROM journey_projects WHERE {where} "
                        "ORDER BY tenant_id,sid LIMIT ? OFFSET ?", (*args, MAX_PROJECTS, offset),
                    )]
            has_more_projects = offset + len(projects) < total_projects
            if not projects:
                result = {
                    "projects": [], "candidates": [],
                    "counts": {key: 0 for key in (
                        "projects", "eligible_projects", "events", "candidates",
                        "sft", "quarantined", "duplicates", "tool_sft", "tool_candidates",
                    )},
                    "reason_counts": {"no_matching_projects" if query or tenant else "no_retained_projects": 1},
                }
            else:
                with self.controls.capture_lock:
                    result = self._collect(purpose, projects, set())
                    titles = {}
                    for project in result["projects"]:
                        project["title"] = project["sid"]
                        if not project["eligible"]:
                            continue
                        tenant_id = project["tenant_id"]
                        if tenant_id not in titles:
                            try:
                                rows = self.analytics.projects(tenant_id)["projects"]
                            except (AnalyticsError, OSError, sqlite3.Error):
                                rows = []
                            titles[tenant_id] = {
                                row["id"]: row.get("title") or row.get("display_name") for row in rows
                            }
                        title = titles[tenant_id].get(project["sid"])
                        if isinstance(title, str) and title.strip():
                            project["title"] = title
                sources = {row["id"]: row for row in result.pop("trajectories")}
                for candidate in result["candidates"]:
                    source = sources.get(candidate["event_id"], {})
                    candidate["source"] = {key: source[key] for key in
                                           ("session_id", "runtime_profile", "runtime") if key in source}
                provenance = result.pop("provenance")
                result["diagnostics"] = [{key: row[key] for key in (
                    "event_id", "tenant_id", "sid", "disposition", "reason", "source", "episode_id",
                ) if key in row} for row in provenance if row.get("reason")][:100]
                result["diagnostics_total"] = sum(bool(row.get("reason")) for row in provenance)
                result["diagnostics_truncated"] = result["diagnostics_total"] > 100
            result.setdefault("diagnostics", [])
            result.setdefault("diagnostics_total", 0)
            result.setdefault("diagnostics_truncated", False)
            configured = sorted(self.analytics.tenants)
            placeholders = ",".join("?" for _ in configured) or "NULL"
            with self.analytics._db() as db:
                result["capture_status"] = {
                    "scope": "retained_tool_episodes_for_configured_tenants",
                    "states": [dict(row) for row in db.execute(
                        "SELECT state,runtime_profile,count(*) AS count,max(updated_at) AS last_updated_at "
                        f"FROM training_tool_episodes WHERE tenant_id IN ({placeholders}) "
                        "GROUP BY state,runtime_profile ORDER BY state,runtime_profile", configured,
                    )],
                    "note": "Retained observations only; counts do not establish runtime health or task quality.",
                }
            result.update(notice_version=NOTICE_VERSION, limits=LIMITATIONS,
                          selection_limit=MAX_PROJECTS, has_more_projects=has_more_projects,
                          offset=offset, total_projects=total_projects,
                          next_offset=offset + len(projects) if has_more_projects else None,
                          filters={"tenant": tenant, "query": query},
                          dataset_status=("reviewed_public_tool_episodes" if result["counts"]["tool_sft"] else
                                          "reviewed_chat_samples_only" if result["counts"]["sft"] else
                                          "no_eligible_sft_samples"),
                          agentic_tool_training_ready=bool(result["counts"]["tool_sft"]),
                          rights_status="unknown",
                          provider_license_review="pending",
                          review_required=["content_approved", "approved_event_ids",
                                           "tool_context_approved (Pi tool episodes)",
                                           "rights_reviewed (external_sharing)"])
            self.audit("preview", purpose, outcome="completed", counts=result["counts"])
            return result
        except Exception:
            self.audit("preview", purpose, outcome="failed")
            raise

    def export(self, purpose, projects, *, review=None):
        """Return (ZIP bytes, safe filename). Routes supply authenticated authority.

        review = {content_approved: true, approved_event_ids: [response event IDs],
                  rights_reviewed: true/false, tool_context_approved: true/false}.
        Tool context approval attests the public episode is self-contained despite
        intentionally omitted system instructions. Event review is explicit quality
        evidence; a general content approval never promotes every candidate.
        """
        audit_action = "export"
        self.audit(audit_action, purpose)
        try:
            review = review or {}
            if (not isinstance(review, dict) or set(review) - {
                "content_approved", "approved_event_ids", "rights_reviewed", "tool_context_approved",
                "reviewer_kind", "evidence_sha256",
            } or any(type(review[key]) is not bool for key in (
                "content_approved", "rights_reviewed", "tool_context_approved",
            ) if key in review)):
                raise ValueError("Invalid operator review")
            reviewer_kind = review.get("reviewer_kind", "unspecified")
            if reviewer_kind not in REVIEWER_KINDS:
                raise ValueError("Invalid reviewer kind")
            evidence = review.get("evidence_sha256")
            if evidence is not None and (not isinstance(evidence, str)
                                         or not re.fullmatch(r"[0-9a-f]{64}", evidence)):
                raise ValueError("Evidence must be a SHA256 digest")
            if reviewer_kind == "automated_acceptance" and evidence is None:
                raise AnalyticsError(409, "automated_acceptance_evidence_required")
            review = {**review, "reviewer_kind": reviewer_kind}
            audit_action = "export." + reviewer_kind
            self.audit(audit_action, purpose, review=review)
            approved = review.get("approved_event_ids", [])
            if (not isinstance(approved, list) or len(approved) > MAX_EVENTS
                    or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                           for v in approved)):
                raise ValueError("Review must identify retained response event IDs")
            if review.get("content_approved") is not True:
                raise AnalyticsError(409, "training_content_review_required")
            if purpose == "external_sharing" and review.get("rights_reviewed") is not True:
                raise AnalyticsError(409, "training_rights_review_required")
            with self.controls.capture_lock:
                result = self._collect(
                    purpose, projects, set(approved), review.get("tool_context_approved") is True,
                )
                if set(approved) - {c["event_id"] for c in result["candidates"]}:
                    raise AnalyticsError(409, "reviewed_events_no_longer_eligible")
                if any(c["event_id"] in approved and "tools" in c["sample"] for c in result["candidates"]
                       ) and review.get("tool_context_approved") is not True:
                    raise AnalyticsError(409, "training_tool_context_review_required")
                for candidate in result["candidates"]:
                    if candidate.get("quality_evidence"):
                        candidate["quality_evidence"].setdefault("reviewer_kind", "unspecified")
                        candidate["quality_evidence"].setdefault("human_reviewed", False)
                    if candidate["event_id"] in approved and candidate.get("quality_evidence"):
                        candidate["quality_evidence"].update(
                            reviewer_kind=reviewer_kind, human_reviewed=reviewer_kind == "human_operator",
                            evidence_sha256=evidence,
                        )
                data = self._package(purpose, result, review)
                # Fresh consent/access/deletion checks immediately before releasing
                # bytes; capture_lock serializes portal revocation/deletion.
                with self.analytics._db() as db:
                    for project in result["projects"]:
                        if project["eligible"]:
                            grant, reason = self._grant(
                                db, project["tenant_id"], project["sid"], purpose,
                            )
                            if reason or grant != project["granted_at"]:
                                raise AnalyticsError(409, "training_permissions_changed")
                self._save_reviews(purpose, result, review)
            self.audit(audit_action, purpose, outcome="completed", counts=result["counts"], review=review)
            stamp = datetime.fromtimestamp(self.analytics.clock(), timezone.utc).strftime("%Y%m%d")
            return data, f"argus-{purpose}-{stamp}-sft-{result['counts']['sft']}.zip"
        except Exception:
            self.audit(audit_action, purpose, outcome="failed")
            raise

    def _package(self, purpose, result, review):
        files = {}

        def jsonl(name, rows):
            files[name] = "".join(_json(row) + "\n" for row in rows).encode()
            if sum(map(len, files.values())) > MAX_EXPORT_BYTES:
                raise AnalyticsError(413, "training_export_size_limit")

        accepted = [c for c in result["candidates"] if c["quality_approved"]]
        for split in ("train", "validation"):
            samples = [c["sample"] for c in accepted if c["split"] == split]
            jsonl(f"hf_trl_{split}.jsonl", samples)
            portable = json.loads(_json(samples))
            for sample in portable:
                for message in sample["messages"]:
                    if message["role"] == "tool":
                        message.pop("name", None)
                    for call in message.get("tool_calls", []):
                        call["function"]["arguments"] = _json(call["function"]["arguments"])
            jsonl(f"sft_{split}.jsonl", portable)
        jsonl("trajectories.jsonl", result["trajectories"])
        jsonl("provenance.jsonl", result["provenance"])
        jsonl("samples.jsonl", [{k: v for k, v in c.items() if k != "sample"}
                               for c in result["candidates"]])
        report = {
            "counts": result["counts"], "reason_counts": result["reason_counts"],
            "dataset_status": ("reviewed_public_tool_episodes" if result["counts"]["tool_sft"] else
                               "reviewed_chat_samples_only" if accepted else "no_eligible_sft_samples"),
            "agentic_tool_training_ready": bool(result["counts"]["tool_sft"]),
            "global_completeness": {"complete": False},
            "projects": result["projects"],
            "train": sum(c["split"] == "train" for c in accepted),
            "validation": sum(c["split"] == "validation" for c in accepted),
            "validation_note": "An empty validation set is intentional for a single group or no validation bucket.",
            "review": review, "limitations": LIMITATIONS,
        }
        files["quality_report.json"] = _json(report).encode()
        files["README.txt"] = (
            "Argus purpose-consented dataset review package\n\n"
            "Only sft_train.jsonl / sft_validation.jsonl are ideal-answer candidates.\n"
            "HF TRL files retain arguments as objects; portable OpenAI-style files\n"
            "encode function.arguments as JSON strings. Tool rows can ONLY originate\n"
            "from forward Pi capture after accepting the combined trial/data notice,\n"
            "with full matching calls/results/schemas and explicit context review.\n"
            "No universal uploader or model/chat-template compatibility is claimed.\n"
            "samples.jsonl links SFT rows (accepted, deduplicated, in order per split)\n"
            "to actual event IDs and explicit quality evidence, including reviewer kind.\n"
            "Automated acceptance is delegated operator verification, not human review.\n"
            "trajectories.jsonl contains safe canonical diagnostics, NOT SFT targets.\n"
            "provenance.jsonl includes content-free quarantines; review reason codes.\n"
            "Consent scope is post-grant only for the selected purpose. Authorized\n"
            "workspace code and file contents may appear inside observed tool arguments\n"
            "and public results; unrelated raw files are not traversed or packaged.\n"
            "No uploads, training jobs, DPO preferences or GRPO rollouts are created.\n\n"
            + "\n".join(LIMITATIONS) + "\n"
        ).encode()
        manifest = {
            "format_version": "argus-training-package-v1", "notice_version": NOTICE_VERSION,
            "research_notice_version": self.analytics.notice_version,
            "purpose": purpose, "created_at": self.analytics.clock(),
            "consent_scope": "current recorded authority; source and ingestion at/after grant; no retroactive import",
            "authorizations": [
                {"tenant_id": project["tenant_id"], "sid": project["sid"], **project["authorization"]}
                for project in result["projects"] if project.get("authorization")
            ],
            "counts": result["counts"], "source_limits": LIMITATIONS,
            "dataset_status": report["dataset_status"],
            "agentic_tool_training_ready": report["agentic_tool_training_ready"],
            "split_policy": "tester groups connected by exact prompt; SHA256 bucket 0/10 validation; single group train",
            "rights_status": "unknown", "provider_license_review": "pending",
            "explicit_operator_rights_review": review.get("rights_reviewed") is True,
            "legal_certification": False, "content_approved": True,
            "reviewer_kind": review.get("reviewer_kind", "unspecified"),
            "human_reviewed": review.get("reviewer_kind") == "human_operator",
            "evidence_sha256": review.get("evidence_sha256"),
            "files": {name: {"sha256": _hash(content), "bytes": len(content)}
                      for name, content in files.items()},
        }
        files["manifest.json"] = _json(manifest).encode()
        if sum(map(len, files.values())) > MAX_EXPORT_BYTES:
            raise AnalyticsError(413, "training_export_size_limit")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        if stream.tell() > MAX_EXPORT_BYTES:
            raise AnalyticsError(413, "training_export_size_limit")
        return stream.getvalue()
