"""Bounded, consent-gated operator analytics, with no network or training hooks.

The portal must authenticate operators before calling dashboard/projects/trace/
export_trace, and authenticate the invitation before recording consent/requests.
Pass the exact displayed notice version as ``notice_version``. Consent is not
inferred from visiting the site. It permits request metadata, bounded copies of
selected request inputs and visible responses, and inspection of existing
observable session traces; the notice must explain those scopes.
analytics.sqlite3 holds metadata events and consent-gated interaction copies
written by interaction_capture, not copies of source project trace files.
``prune`` retains its metadata-only count; the admin retention action also calls
prune_interactions. Neither deletes source trace files or consent receipts,
whose lifecycles must be managed separately.

Trace reads use existing session.json, transcript.jsonl, events.jsonl,
backlog[.archive].jsonl and daemon.status.json schemas. Unlike the general Argus
readers, they use bounded, descriptor-relative, no-symlink reads. Raw agent_io,
provider ledgers, Manager control conversations and arbitrary workspace files
are deliberately unsupported. Artifact declarations are metadata, not verified
file existence/content. Rotated events and pre-consent/uninstrumented requests
are not reconstructed. Observations are untrusted, not proof of task success.
"""
from __future__ import annotations

import errno
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
from collections.abc import Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from argus_skill.core.secret_guard import redact_secrets_record
from argus_skill.core.session import SESSION_META_FILE

POLICY_VERSION = "operator-analytics-v1"
MAX_FILE_BYTES = 1024 * 1024
MAX_META_BYTES = 64 * 1024
MAX_ROWS = 5000
MAX_PROJECTS = 100
MAX_DIRECTORY_ENTRIES = 1000
SOURCES = (
    "transcript.jsonl", "backlog.jsonl", "backlog.archive.jsonl",
    "events.jsonl", "daemon.status.json",
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
_SECRET_KEY = re.compile(
    r"(?i)authorization|cookie|password|passwd|api.?key|secret|credential|"
    r"(?:^|[_-])(?:token|auth)(?:$|[_-])"
)
_CREDENTIAL = re.compile(
    r"(?i)\b(?:argus_trial_[A-Za-z0-9_-]+|github_pat_[A-Za-z0-9_]+|"
    r"gh[pousr]_[A-Za-z0-9_]+|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{8,})"
)
_INLINE = re.compile(
    r"""(?ix)\b(?:authorization|proxy-authorization|cookie|set-cookie)
    \s*["']?\s*[:=]\s*[^\r\n]+
    |\bbearer\s+[A-Za-z0-9._~+/=-]+
    |\b(?:[\w-]*api[_-]?key|password|passwd|secret|token|credential)
    \s*["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)"""
)
_PRIVATE = re.compile(
    r"(?i)reasoning|chain[_\s-]*of[_\s-]*thought|scratch[_\s-]*pad|"
    r"internal[_\s-]*thought|<think>|<analysis>"
)
_FIELDS = frozenset("""
    id ts timestamp time type kind role text content message input output result
    summary title objective status success error last_error item_id task_id call_id
    tool tool_name arguments args command exit_code duration elapsed_ms duration_ms
    started_ts finished_ts created last_active tokens input_tokens output_tokens
    total_tokens cost cost_usd usage model delivery delivery_id targets path label
    artifacts size size_bytes mime filename mission_result steps data payload
    task_type tags vertical run_label event_type actor agent_layer message_id
    round_index experiment_id branch_id fatal_error cached_input_tokens cache_write_tokens
""".split())


class AnalyticsError(ValueError):
    """Safe portal error; translate ``status`` and ``code`` into an HTTP response."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _server_replay_notice(version):
    """Later notice revisions retain the v2 no-historical-backfill boundary."""
    match = re.fullmatch(r"operator-analytics-v([0-9]+)(?:-[a-z0-9-]+)?", version)
    return match is not None and int(match[1]) >= 2


def _private(value, depth=0):
    """Reject the whole row, including nested/JSON-encoded reasoning payloads."""
    if depth > 30:
        return True
    if isinstance(value, dict):
        return any(
            _PRIVATE.search(str(key))
            or (key in {"type", "kind", "role", "channel", "event_type", "field"}
                and _PRIVATE.search(str(item)))
            or _private(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_private(item, depth + 1) for item in value)
    if isinstance(value, str):
        # Conservative: omit rather than risk exporting embedded private reasoning.
        return bool(_PRIVATE.search(value))
    return False


def _sanitize(value):
    if isinstance(value, dict):
        return {
            _sanitize(str(key)): (
                "[REDACTED]" if _SECRET_KEY.search(str(key)) else _sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _INLINE.sub("[REDACTED]", _CREDENTIAL.sub("[REDACTED]", value))
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_row(row):
    if not isinstance(row, dict) or _private(row):
        return None
    # Drop raw/control fields; redaction also applies to nested selected fields.
    selected = {key: value for key, value in row.items() if key in _FIELDS}
    return _sanitize(redact_secrets_record(selected))


@contextmanager
def _directory(path):
    """Walk from / without following any symlink, including intermediate ones."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:]:
            if part in {".", ".."}:
                raise AnalyticsError(400, "unsafe_path")
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _read(fd, name, cap):
    """Only callers' constant basenames; no special files or hardlinked files."""
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None, {"state": "missing", "truncated": False}
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise AnalyticsError(400, "unsafe_path") from None
        return None, {"state": "unreadable", "truncated": False}
    with os.fdopen(handle, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AnalyticsError(400, "unsafe_path")
        raw = stream.read(cap + 1)
    return raw[:cap], {
        "state": "ok", "bytes": info.st_size, "truncated": len(raw) > cap,
    }


def _json(raw):
    return json.loads(raw, parse_constant=lambda _: None)


class Analytics:
    def __init__(
        self, state_dir: Path, tenants: Mapping, trial_db: Path, compute_db: Path,
        *, notice_version: str = POLICY_VERSION, retention_days: int = 30,
        clock=time.time, token_limit: int | None = 10_000_000,
    ):
        if not isinstance(notice_version, str) or not _VERSION.fullmatch(notice_version):
            raise ValueError("Invalid notice version")
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be 1..365")
        if token_limit is not None and (type(token_limit) is not int or token_limit <= 0):
            raise ValueError("token_limit must be a positive integer or null")
        self.token_limit = token_limit
        self.tenants = {}
        if not 1 <= len(tenants) <= 100:
            raise ValueError("Expected 1..100 configured invitation accounts")
        for tenant, config in tenants.items():
            if not _ID.fullmatch(tenant) or type(config.get("internal_test")) is not bool:
                raise ValueError("Invalid tenant configuration")
            path = Path(config["data_dir"])
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("data_dir must be an absolute path")
            global_root = Path(config.get("global_root", path / "home/.argus-skill"))
            if not global_root.is_absolute() or ".." in global_root.parts:
                raise ValueError("global_root must be an absolute path")
            runtime_mode = config.get("runtime_mode", "container")
            if runtime_mode not in ("container", "host"):
                raise ValueError("runtime_mode must be container or host")
            self.tenants[tenant] = {
                "data_dir": path, "global_root": global_root, "runtime_mode": runtime_mode,
                "internal_test": config["internal_test"],
            }
        self.notice_version = notice_version
        self.retention_days = retention_days
        self.clock = clock
        self.trial_db, self.compute_db = Path(trial_db), Path(compute_db)
        state_dir = Path(state_dir).absolute()
        # The operator-owned index must never be placed in a tenant's writable tree.
        for config in self.tenants.values():
            if any(state_dir.resolve().is_relative_to(config[field].resolve()) for field in ("data_dir", "global_root")):
                raise ValueError("analytics state must be separate from tenant data")
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = state_dir / "analytics.sqlite3"
        self._storage_anchor = None
        self._storage_lock = threading.Lock()
        if self.path.is_symlink():
            raise ValueError("analytics database may not be a symlink")
        with self._db() as db:
            # Consent checks must remain readable while the persistent collector writes.
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS consents (
                    tenant_id TEXT NOT NULL, version TEXT NOT NULL,
                    consented_at REAL NOT NULL, PRIMARY KEY(tenant_id, version)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
                    ts REAL NOT NULL, consent_version TEXT NOT NULL,
                    method TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL, task_type TEXT
                );
                CREATE INDEX IF NOT EXISTS analytics_events_time ON events(ts);
                CREATE INDEX IF NOT EXISTS analytics_events_tenant_time
                    ON events(tenant_id, ts);
            """)
        self.path.chmod(0o600)
        self.prune()

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            # WAL commits stay transactional without a device flush for every
            # HTTP counter or streamed token. Checkpoints retain WAL recovery.
            db.execute("PRAGMA synchronous=NORMAL")
            with db:
                yield db

    def open_storage(self):
        """Keep an idle WAL reader attached for the portal's lifetime.

        Closing the last short-lived connection checkpoints and unlinks WAL.
        Under a busy filesystem that flush can hold an exclusive database lock
        for seconds, blocking both new observations and ordinary page requests.
        This anchor owns no transaction and does not serialize actual readers.
        """
        with self._storage_lock:
            if self._storage_anchor is None:
                anchor = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
                try:
                    anchor.execute("PRAGMA synchronous=NORMAL")
                    anchor.execute("SELECT count(*) FROM sqlite_master").fetchone()
                    anchor.execute("PRAGMA query_only=ON")
                except BaseException:
                    anchor.close()
                    raise
                self._storage_anchor = anchor

    def close_storage(self):
        with self._storage_lock:
            anchor, self._storage_anchor = self._storage_anchor, None
            if anchor is not None:
                anchor.close()

    def _tenant(self, tenant):
        if tenant not in self.tenants:
            raise AnalyticsError(404, "tenant_not_found")
        return self.tenants[tenant]

    def policy(self):
        policy = {
            "policy_version": POLICY_VERSION, "notice_version": self.notice_version,
            "retention_days": self.retention_days, "redaction": "best-effort",
            "operator_review_required_before_training_or_publication": True,
            "automatic_training_or_external_transmission": False,
            "counts_unit": "invitation accounts, not unique people",
            "internal_tests_are_customer_traction": False,
            "consent_scope": (
                "request metadata, bounded copies of selected request inputs and visible "
                "responses, and existing observable project traces; current notice consent required"
            ),
            "request_metadata": "route templates, timing, status and request categories; no bodies",
            "interaction_copies": (
                "bounded selected request inputs and filtered visible response frames/results "
                "in analytics.sqlite3; best-effort redaction, not raw provider streams or "
                "private reasoning; truncation and incomplete responses are recorded"
            ),
            "source_project_traces": (
                "read on demand from tenant files with consent, filtering and byte/row limits; "
                "not copied into the analytics index"
            ),
            "retention_scope": (
                "admin retention action deletes metadata events and captured interactions "
                "older than retention_days, across tenants and notice versions; "
                "interaction age is measured from invocation creation, not completion"
            ),
            "source_trace_files_deleted_by_retention": False,
            "consent_receipts_retention": (
                "retained separately from metadata and interaction retention"
            ),
        }
        if _server_replay_notice(self.notice_version):
            policy.update(
                policy_version=self.notice_version,
                consent_scope=(
                    "selected task inputs, visible responses, observed runtime events, artifact "
                    "references, project names, explicit human feedback and annotations; "
                    "current notice consent required before task input; browser-independent polling"
                ),
                source_project_traces=(
                    "bounded allowlisted events.jsonl projections copied into a separate research "
                    "journal after its collection boundary; no historical backfill; raw tool output, "
                    "attachments, provider streams and hidden reasoning excluded"
                ),
                retention_scope=(
                    "automatic periodic cleanup of metadata events and captured interactions, "
                    "journal events, human annotations, feedback and read/export audit; maximum "
                    "30 days; journal capacity may expire records earlier"
                ),
                ordering="unique event identifiers; sequence is ingestion order, not causal order",
                completeness="always unverified; omissions, truncations and detected gaps are explicit",
                deletion_scope=(
                    "project research copies only, with future project collection disabled; "
                    "runtime/workspace/source logs and accounting are not deleted"
                ),
                separate_metadata_retention=(
                    "consent receipts, content-free ingestion checkpoints, HTTP receipts and deletion "
                    "tombstones retained separately to prevent reimport; no automatic expiry"
                ),
                backup_policy=(
                    "this feature creates no research database backups; existing host snapshots, "
                    "independent backups and downloaded exports are outside automatic deletion; "
                    "operator follow-up is required; logical deletion is not secure disk erasure"
                ),
            )
        return policy

    def record_consent(self, tenant_id, version):
        self._tenant(tenant_id)
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError("Invalid notice version")
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO consents VALUES (?, ?, ?)",
                (tenant_id, version, self.clock()),
            )
            return dict(db.execute(
                "SELECT * FROM consents WHERE tenant_id=? AND version=?",
                (tenant_id, version),
            ).fetchone())

    def consented(self, tenant_id, version):
        self._tenant(tenant_id)
        with self._db() as db:
            return db.execute(
                "SELECT 1 FROM consents WHERE tenant_id=? AND version=?",
                (tenant_id, version),
            ).fetchone() is not None

    def _require_consent(self, tenant):
        self._tenant(tenant)
        if not self.consented(tenant, self.notice_version):
            raise AnalyticsError(403, "consent_required")

    @staticmethod
    def _route(path):
        # Route templates prevent path segments from becoming a content side channel.
        raw = urlsplit(path[:8192]).path
        match = re.fullmatch(
            r"/api/projects/[^/]+/(message(?:/stream)?|tasks|stream|status|"
            r"events|backlog|transcript|artifacts|nudge|note|plan)", raw,
        )
        if match:
            return "/api/projects/:sid/" + match[1]
        if re.fullmatch(r"/api/projects/[^/]+", raw):
            return "/api/projects/:sid"
        if re.fullmatch(r"/compute/(?:api/)?jobs/[0-9]+(?:/logs|/cancel)?", raw):
            return "/compute/jobs/:id"
        return raw if raw in {
            "/", "/api/projects", "/api/daemons", "/compute/jobs",
            "/compute/api/jobs", "/compute/status", "/trial/me",
        } else "/:other"

    def record_request(self, tenant_id, method, path, status, elapsed_ms):
        """Return monotonic event ID, or None before current-version consent."""
        self._tenant(tenant_id)
        if not self.consented(tenant_id, self.notice_version):
            return None
        method = str(method).upper()
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "WS"}:
            raise ValueError("Invalid method")
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("Invalid status")
        if not isinstance(elapsed_ms, (int, float)) or not math.isfinite(elapsed_ms) or elapsed_ms < 0:
            raise ValueError("Invalid elapsed_ms")
        route = self._route(path)
        task_type = None
        if method == "POST":
            if route in {"/api/projects/:sid/message", "/api/projects/:sid/message/stream"}:
                task_type = "message"
            elif route == "/api/projects/:sid/tasks":
                task_type = "task"
            elif route in {"/compute/jobs", "/compute/api/jobs"}:
                task_type = "compute_job"
        with self._db() as db:
            self._prune(db)
            cursor = db.execute(
                "INSERT INTO events(tenant_id,ts,consent_version,method,path,status,"
                "elapsed_ms,task_type) VALUES(?,?,?,?,?,?,?,?)",
                (tenant_id, self.clock(), self.notice_version, method, route, status,
                 elapsed_ms, task_type),
            )
            return cursor.lastrowid

    def _prune(self, db):
        return db.execute(
            "DELETE FROM events WHERE ts < ?",
            (self.clock() - self.retention_days * 86400,),
        ).rowcount

    def prune(self):
        """Delete expired request metadata only; never traverse tenant storage."""
        with self._db() as db:
            return self._prune(db)

    @staticmethod
    def _ledger(path, query, args):
        # mode=ro never creates a missing ledger or invokes mutating Store/Compute constructors.
        with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            return db.execute(query, args).fetchall()

    def _token_account(self, tenant):
        # Read the total and its decomposition in one read-only snapshot.
        with closing(sqlite3.connect(self.trial_db.absolute().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            meter = db.execute("SELECT used FROM trial_keys WHERE key_id=?", (tenant,)).fetchone()
            if meter is None:
                return {"issued": False, "meter_state": "account_missing"}
            result = {"issued": True, "token_used": meter[0], "meter_state": "ok"}
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='trial_requests' AND type='table'").fetchone():
                return {**result, "tokens_unattributed": meter[0], "token_breakdown_state": "legacy"}
            charges = dict(db.execute(
                "SELECT state,sum(charged) FROM trial_requests WHERE key_id=? GROUP BY state", (tenant,),
            ))
            accounted = sum(charges.values())
            result.update(
                tokens_settled=charges.get("settled", 0),
                tokens_reserved=charges.get("active", 0),
                tokens_uncertain=sum(value for key, value in charges.items() if key not in {"settled", "active"}),
                tokens_unattributed=max(0, meter[0] - accounted),
                token_breakdown_state="ok" if accounted <= meter[0] else "inconsistent",
            )
            return result

    def _account(self, tenant, since, now):
        with self._db() as db:
            row = dict(db.execute("""
                SELECT count(*) AS request_count,
                    max(ts) AS last_request_at,
                    coalesce(sum(ts >= ?),0) AS requests_last_5min,
                    coalesce(sum(task_type='message' AND status BETWEEN 200 AND 299),0)
                        AS accepted_message_requests,
                    coalesce(sum(task_type='compute_job' AND status BETWEEN 200 AND 299),0)
                        AS accepted_compute_job_requests
                FROM events WHERE tenant_id=? AND ts>=? AND ts<=? AND consent_version=?
            """, (now - 300, tenant, since, now, self.notice_version)).fetchone())
            row.update(dict(db.execute("""
                SELECT count(*) AS accepted_task_requests,
                    coalesce(sum(finished_at>=?),0) AS active_task_requests_last_5min
                FROM interactions WHERE tenant_id=? AND notice_version=? AND task_accepted=1
                    AND finished_at>=? AND finished_at<=?
            """, (now - 300, tenant, self.notice_version, since, now)).fetchone()))
        row.update(
            tenant_id=tenant, internal_test=self.tenants[tenant]["internal_test"],
            consented=self.consented(tenant, self.notice_version),
            issued=None, token_used=None, gpu_seconds_used=None, gpu_seconds_reserved=None,
            token_limit=self.token_limit, token_unlimited=self.token_limit is None,
            gpu_seconds_limit=200 * 3600,
            tokens_settled=None, tokens_reserved=None, tokens_uncertain=None,
            tokens_unattributed=None, token_breakdown_state="unavailable",
        )
        try:
            row.update(self._token_account(tenant))
        except (sqlite3.Error, OSError):
            row["meter_state"] = "unavailable"
        try:
            gpu = self._ledger(self.compute_db, """
                SELECT coalesce(sum(CASE WHEN status IN
                    ('succeeded','failed','cancelled','timed_out') THEN charged ELSE 0 END),0),
                    coalesce(sum(CASE WHEN status NOT IN
                    ('succeeded','failed','cancelled','timed_out') THEN charged ELSE 0 END),0)
                FROM jobs WHERE tenant_id=?
            """, (tenant,))[0]
            row.update(gpu_seconds_used=gpu[0], gpu_seconds_reserved=gpu[1], compute_state="ok")
        except (sqlite3.Error, OSError):
            row["compute_state"] = "unavailable"
        return row

    @staticmethod
    def _summary(accounts):
        result = {
            "configured_accounts": len(accounts),
            "issued_accounts": sum(row["issued"] is True for row in accounts),
            "issued_count_complete": all(row["issued"] is not None for row in accounts),
            "consented_accounts": sum(row["consented"] for row in accounts),
            "active_codes_last_5min": sum(row["requests_last_5min"] > 0 for row in accounts),
            "task_active_accounts": sum(row["accepted_task_requests"] > 0 for row in accounts),
            "message_active_accounts": sum(row["accepted_message_requests"] > 0 for row in accounts),
            "compute_active_accounts": sum(row["accepted_compute_job_requests"] > 0 for row in accounts),
            "task_active_accounts_last_5min": sum(
                row["active_task_requests_last_5min"] > 0 for row in accounts
            ),
        }
        for key in ("token_used", "tokens_settled", "tokens_reserved", "tokens_uncertain",
                    "tokens_unattributed", "gpu_seconds_used", "gpu_seconds_reserved",
                    "accepted_message_requests", "accepted_task_requests", "accepted_compute_job_requests"):
            result[key] = (
                sum(row[key] for row in accounts)
                if all(row[key] is not None for row in accounts) else None
            )
        result["token_breakdown_state"] = (
            "ok" if all(row["token_breakdown_state"] == "ok" for row in accounts) else "incomplete"
        )
        return result

    def dashboard(self, days=1, include_internal=False):
        if type(days) is not int or not 1 <= days <= self.retention_days:
            raise ValueError("days must be within event retention")
        self.prune()
        from .interaction_capture import _schema

        _schema(self)
        now = self.clock()
        accounts = [
            self._account(tenant, now - days * 86400, now) for tenant in sorted(self.tenants)
        ]
        external = [row for row in accounts if not row["internal_test"]]
        internal = [row for row in accounts if row["internal_test"]]
        selected = accounts if include_internal else external
        ids = [row["tenant_id"] for row in selected]
        with self._db() as db:
            placeholders = ",".join("?" for _ in ids) or "NULL"
            where = (
                f"tenant_id IN ({placeholders}) AND ts>=? AND ts<=? "
                "AND consent_version=? AND task_type IS NOT NULL"
            )
            args = (*ids, now - days * 86400, now, self.notice_version)
            recent = [dict(row) for row in db.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY id DESC LIMIT 101", args,
            )]
            types = dict(db.execute(
                f"SELECT task_type,count(*) FROM events WHERE {where} GROUP BY task_type", args,
            ))
        for row in recent:
            row["internal_test"] = self.tenants[row["tenant_id"]]["internal_test"]
        return {
            "policy": self.policy(), "days": days, "generated_at": now,
            "include_internal": bool(include_internal),
            "summary": self._summary(selected), "accounts": selected,
            "external_accounts": self._summary(external),
            "internal_testing": {"summary": self._summary(internal), "accounts": internal},
            "task_types": types, "recent_task_requests": recent[:100],
            "recent_task_requests_truncated": len(recent) > 100,
            "activity_definition": (
                "task activity requires a complete accepted task response with a validated task ID; "
                "chat, errors, duplicate dispatches and HTTP status alone are not task evidence; "
                "message and compute HTTP submissions are counted separately; not completed work"
            ),
            "task_type_definition": "request categories, not inferred scientific/industry taxonomy",
            "quota_scope": "lifetime, not days; meter used includes conservative reservations",
            "token_breakdown_definition": (
                "settled=reported usage; reserved=active requests; uncertain=unknown/interrupted "
                "usage retained by the meter; unattributed=meter balance without request receipts; "
                "null means unavailable, not zero"
            ),
        }

    @contextmanager
    def _projects_dir(self, tenant):
        self._require_consent(tenant)
        path = self.tenants[tenant]["global_root"] / "projects"
        try:
            with _directory(path) as fd:
                yield fd
        except FileNotFoundError:
            raise AnalyticsError(404, "project_not_found") from None
        except OSError:
            raise AnalyticsError(400, "unsafe_path") from None

    @contextmanager
    def _project(self, tenant, sid):
        if not isinstance(sid, str) or not _ID.fullmatch(sid):
            raise AnalyticsError(400, "invalid_session_id")
        with self._projects_dir(tenant) as root:
            try:
                fd = os.open(sid, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            except FileNotFoundError:
                raise AnalyticsError(404, "project_not_found") from None
            except OSError:
                raise AnalyticsError(400, "unsafe_path") from None
            try:
                yield fd
            finally:
                os.close(fd)

    @staticmethod
    def _meta(fd, sid):
        raw, state = _read(fd, SESSION_META_FILE, MAX_META_BYTES)
        if raw is None or state["truncated"]:
            return None, state
        try:
            row = _json(raw)
            if not isinstance(row, dict) or row.get("id") != sid:
                return None, {**state, "state": "invalid_metadata"}
            if _private(row):
                return None, {**state, "state": "suppressed"}
            result = _safe_row({
                key: value for key, value in row.items()
                if key in {"id", "display_name", "objective", "created", "last_active"}
            })
            if result is None:
                return None, {**state, "state": "suppressed"}
            # display_name is deliberately renamed into the observable title schema.
            result["title"] = _sanitize(redact_secrets_record(str(row.get("display_name", ""))))
            return result, state
        except (ValueError, TypeError, RecursionError):
            return None, {**state, "state": "invalid_json"}

    def projects(self, tenant_id):
        """Bounded session metadata only; legacy metadata-less projects are not listed."""
        output, skipped, truncated = [], 0, False
        try:
            with self._projects_dir(tenant_id) as root, os.scandir(root) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_DIRECTORY_ENTRIES or len(output) >= MAX_PROJECTS:
                        truncated = True
                        break
                    if not _ID.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
                        skipped += 1
                        continue
                    try:
                        with self._project(tenant_id, entry.name) as fd:
                            meta, state = self._meta(fd, entry.name)
                        if meta is not None:
                            output.append(meta)
                        else:
                            skipped += 1
                        truncated |= state["truncated"]
                    except AnalyticsError:
                        skipped += 1
        except AnalyticsError as exc:
            if exc.status != 404:
                raise
            return {"projects": [], "truncated": False, "skipped": 0,
                    "state": "projects_directory_missing", "policy": self.policy()}
        return {"projects": sorted(output, key=lambda row: row["id"]),
                "truncated": truncated, "skipped": skipped, "state": "ok",
                "policy": self.policy()}

    def trace(self, tenant_id, sid, limit=500):
        if type(limit) is not int or not 1 <= limit <= MAX_ROWS:
            raise ValueError(f"limit must be 1..{MAX_ROWS}")
        rows, sources = [], {}
        with self._project(tenant_id, sid) as fd:
            meta, state = self._meta(fd, sid)
            sources[SESSION_META_FILE] = state
            if state["state"] == "missing":
                # Recognize legacy sessions only by a named existing Argus state file.
                if not any(_read(fd, name, 1)[0] is not None for name in SOURCES):
                    raise AnalyticsError(404, "project_not_found")
            elif meta is None:
                raise AnalyticsError(422, "invalid_project_metadata")
            for name in SOURCES:
                raw, info = _read(fd, name, MAX_FILE_BYTES)
                info.update(returned_rows=0, suppressed_rows=0, invalid_rows=0)
                sources[name] = info
                if raw is None:
                    continue
                lines = raw.splitlines() if name.endswith(".jsonl") else [raw]
                if info["truncated"]:
                    # Never parse an incomplete JSON record at the byte boundary.
                    lines = lines[:-1] if name.endswith(".jsonl") else []
                for index, line in enumerate(lines):
                    if index >= MAX_ROWS or len(rows) >= limit:
                        info["truncated"] = True
                        break
                    if not line.strip():
                        continue
                    try:
                        original = _json(line)
                        event_type = str(original.get("type", "")) if isinstance(original, dict) else ""
                        # Raw provider stream/control logs are not observable deliveries.
                        if event_type in {"agent.io.stream", "role.session.turn"}:
                            info["suppressed_rows"] += 1
                            continue
                        clean = _safe_row(original)
                    except (ValueError, TypeError, RecursionError):
                        info["invalid_rows"] += 1
                        continue
                    if clean is None:
                        info["suppressed_rows"] += 1
                        continue
                    rows.append({"source": name, "source_row": index + 1, "data": clean})
                    info["returned_rows"] += 1
        return {
            "tenant_id": tenant_id, "sid": _sanitize(sid),
            "internal_test": self.tenants[tenant_id]["internal_test"],
            "policy": self.policy(), "project": meta, "rows": rows, "sources": sources,
            "truncated": any(info["truncated"] for info in sources.values()),
            "coverage": {
                "ordering": "source order and file order, not a globally ordered timeline",
                "artifact_metadata": "declared delivery targets/artifacts only; not stat-verified",
                "gaps": [
                    "rotated events and rows beyond byte/row limits",
                    "raw provider streams, reasoning and Manager control conversations",
                    "workspace artifact contents and unrecorded tool outputs",
                    "no guaranteed input-to-result linkage or independent success verification",
                ],
            },
        }

    def export_trace(self, tenant_id, sid, limit=500):
        """Return selected sanitized trace JSON text, never a raw file/archive download."""
        return json.dumps(self.trace(tenant_id, sid, limit), ensure_ascii=False, allow_nan=False)
