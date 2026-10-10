"""Durable admission and usage accounting for one trial gateway instance."""
from __future__ import annotations

import hashlib
import logging
import math
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from ..provider_integrations.copilot_usage import NANO_AIU_PER_USD
from . import GLOBAL_TPM, MAX_CONCURRENCY, TOKEN_LIMIT, TPM_WINDOW_SECONDS, TRIAL_KEY_COUNT

log = logging.getLogger(__name__)


class TrialError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int = 5):
        super().__init__(message)
        self.status, self.code = status, code
        self.retry_after = retry_after


def usd(nano_aiu: int) -> float:
    """Activation-code spend is kept in the provider's integer unit, nano-AIU."""
    return nano_aiu / NANO_AIU_PER_USD


def quota_refusal(remaining: int, limit: int, needed: int) -> TrialError:
    balance = f"${usd(max(0, remaining)):.4f} of ${usd(limit):.2f} remaining"
    if remaining <= 0:
        return TrialError(402, "quota_exhausted", f"Activation code quota used up (额度已用完, quota_exhausted): {balance}.")
    # Admission reserves an upper bound, so a large request can be refused
    # before the balance reaches zero; say so instead of claiming it is spent.
    return TrialError(
        402, "quota_insufficient",
        f"Activation code balance too low for this request (额度不足, quota_insufficient): {balance}; this request "
        f"reserves up to ${usd(needed):.4f}. Send a smaller request or ask the operator for more allowance.",
    )


class Store:
    def __init__(self, path: Path, *, clock=time.time, token_limit: int | None = TOKEN_LIMIT,
                 key_limit: int = TRIAL_KEY_COUNT):
        if token_limit is not None and (type(token_limit) is not int or token_limit <= 0):
            raise ValueError("Trial token limit must be a positive integer")
        if type(key_limit) is not int or not 1 <= key_limit <= 100:
            raise ValueError("Trial key limit must be between 1 and 100")
        self.path = path
        self.clock = clock
        self.token_limit = token_limit
        self.key_limit = key_limit
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS trial_keys (
                    key_id TEXT PRIMARY KEY,
                    credential_hash TEXT UNIQUE NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                    claim_hash TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS trial_requests (
                    id INTEGER PRIMARY KEY,
                    key_id TEXT NOT NULL REFERENCES trial_keys(key_id),
                    reserved INTEGER NOT NULL,
                    charged INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS trial_tpm_reservations (
                    request_id INTEGER PRIMARY KEY REFERENCES trial_requests(id),
                    tokens INTEGER NOT NULL,
                    retain_until REAL
                );
                CREATE INDEX IF NOT EXISTS trial_tpm_expiry ON trial_tpm_reservations(retain_until);
                CREATE TABLE IF NOT EXISTS trial_request_operations (
                    operation_key TEXT PRIMARY KEY,
                    request_id INTEGER UNIQUE NOT NULL REFERENCES trial_requests(id),
                    phase TEXT NOT NULL CHECK (phase IN ('reserved', 'submitted'))
                );
                CREATE TABLE IF NOT EXISTS trial_gateway_attempts (
                    id INTEGER PRIMARY KEY,
                    key_id TEXT NOT NULL REFERENCES trial_keys(key_id),
                    started_at REAL NOT NULL,
                    slot_acquired_at REAL,
                    admitted_at REAL,
                    finished_at REAL,
                    recovered_at REAL,
                    phase TEXT NOT NULL,
                    outcome TEXT,
                    queue_reason TEXT,
                    slot_wait_ms REAL NOT NULL DEFAULT 0,
                    tpm_wait_ms REAL NOT NULL DEFAULT 0,
                    upstream_status INTEGER,
                    selected_response_status INTEGER,
                    client_error_code TEXT,
                    retry_after INTEGER,
                    reservation_id INTEGER REFERENCES trial_requests(id),
                    estimated_tokens INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS trial_gateway_finished ON trial_gateway_attempts(finished_at, key_id);
                CREATE TABLE IF NOT EXISTS trial_access (
                    key_id TEXT PRIMARY KEY REFERENCES trial_keys(key_id),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS trial_testers (
                    key_id TEXT PRIMARY KEY REFERENCES trial_keys(key_id),
                    tester_id TEXT UNIQUE NOT NULL,
                    created_at REAL NOT NULL
                );
                -- Activation codes: trial keys limited in USD (nano-AIU) only.
                CREATE TABLE IF NOT EXISTS trial_codes (
                    key_id TEXT PRIMARY KEY REFERENCES trial_keys(key_id),
                    label TEXT NOT NULL DEFAULT '',
                    usd_limit INTEGER NOT NULL CHECK (usd_limit > 0),
                    cost_used INTEGER NOT NULL DEFAULT 0 CHECK (cost_used >= 0),
                    created_at REAL NOT NULL,
                    last_used_at REAL
                );
                CREATE TABLE IF NOT EXISTS trial_request_costs (
                    request_id INTEGER PRIMARY KEY REFERENCES trial_requests(id),
                    reserved INTEGER NOT NULL,
                    charged INTEGER NOT NULL,
                    source TEXT
                );
            """)
        path.chmod(0o600)

    @contextmanager
    def transaction(self, *, timeout: float = 10):
        with closing(sqlite3.connect(self.path, timeout=timeout)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db

    def recover(self):
        # Only called while holding the process-lifetime exclusive gateway lock.
        # Unknown usage stays charged, so killing the server never refunds work.
        with self.transaction() as db:
            # A managed reservation cannot reach a provider until its submit
            # intent has committed. A process lost before that boundary owes
            # no usage, including a reserve that finished after cancellation.
            for row in db.execute("""SELECT r.* FROM trial_requests r
                    JOIN trial_request_operations o ON o.request_id=r.id
                    WHERE r.state='active' AND o.phase='reserved'""").fetchall():
                self._settle_row(db, row, 0)
            # Recover interrupted work without refunding unknown usage.
            db.execute("""INSERT OR IGNORE INTO trial_tpm_reservations(request_id, tokens)
                SELECT id, reserved FROM trial_requests WHERE state='active'""")
            db.execute(
                "UPDATE trial_tpm_reservations SET retain_until=? WHERE retain_until IS NULL",
                (self.clock() + TPM_WINDOW_SECONDS,),
            )
            db.execute("UPDATE trial_requests SET state='interrupted' WHERE state='active'")
            # Older gateways kept the byte-based admission estimate even after
            # receiving usage. Restore the remaining window to reported tokens
            # without changing lifetime charges or extending its expiry.
            db.execute("""UPDATE trial_tpm_reservations SET tokens=(
                SELECT charged FROM trial_requests WHERE id=request_id
            ) WHERE request_id IN (SELECT id FROM trial_requests WHERE state='settled')""")
        # Observation failure cannot roll back the accounting recovery above.
        try:
            with self.transaction(timeout=0) as db:
                # The interruption's actual time remains unknown. Old ledger
                # rows never acquire invented gateway timestamps.
                db.execute("""UPDATE trial_gateway_attempts SET outcome='interrupted', recovered_at=?
                    WHERE outcome IS NULL""", (self.clock(),))
        except sqlite3.Error:
            log.exception("Could not recover gateway attempt observations")

    def begin_gateway_attempt(self, key_id: str, estimated_tokens: int, *, started_at: float | None = None) -> int:
        """One validated HTTP attempt, including requests never admitted."""
        # Observations are optional, including callers outside the gateway
        # scheduler. They must never wait for an external SQLite writer.
        with self.transaction(timeout=0) as db:
            return db.execute("""INSERT INTO trial_gateway_attempts
                (key_id,started_at,phase,estimated_tokens) VALUES(?,?,'slot',?)""",
                (key_id, self.clock() if started_at is None else started_at, estimated_tokens)).lastrowid

    def update_gateway_attempt(self, attempt_id: int, **fields):
        """First terminal observation wins, independently of usage settlement."""
        allowed = {
            "slot_acquired_at", "admitted_at", "finished_at", "phase", "outcome", "queue_reason",
            "slot_wait_ms", "tpm_wait_ms", "upstream_status", "selected_response_status",
            "client_error_code", "retry_after", "reservation_id",
        }
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid gateway observation fields")
        with self.transaction(timeout=0) as db:
            db.execute("UPDATE trial_gateway_attempts SET " + ",".join(f"{key}=?" for key in fields)
                       + " WHERE id=? AND outcome IS NULL", (*fields.values(), attempt_id))

    def issue(self, key_id: str, credential: str):
        digest = hashlib.sha256(credential.encode()).hexdigest()
        with self.transaction() as db:
            existing = db.execute("SELECT credential_hash FROM trial_keys WHERE key_id=?", (key_id,)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError("Existing trial key differs; restore the original master key")
                return
            else:
                count = db.execute("SELECT count(*) FROM trial_keys").fetchone()[0]
                if count >= self.key_limit:
                    raise ValueError(f"All {self.key_limit} trial keys have already been issued")
            db.execute(
                "INSERT OR IGNORE INTO trial_keys(key_id, credential_hash) VALUES (?, ?)",
                (key_id, digest),
            )

    def issue_code(self, key_id: str, credential: str, *, usd_limit: int, label: str = "",
                   expires_at: float | None = None):
        """Issue an activation code: a trial key with a USD allowance in nano-AIU.

        Only the credential's hash is stored. Reissuing the same code keeps its
        allowance and spend.
        """
        if type(usd_limit) is not int or usd_limit <= 0:
            raise ValueError("Activation code allowance must be a positive amount")
        if not isinstance(label, str) or len(label) > 200:
            raise ValueError("Activation code label must be text of at most 200 characters")
        self.issue(key_id, credential)
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO trial_codes(key_id,label,usd_limit,created_at) VALUES (?,?,?,?)",
                       (key_id, label, usd_limit, self.clock()))
        if expires_at is not None:
            self.set_access(key_id, enabled=True, expires_at=expires_at)

    def list_codes(self) -> list[dict]:
        """Operator view of every activation code, never including the code itself."""
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("""
                SELECT c.key_id,c.label,c.usd_limit,c.created_at,c.cost_used,c.last_used_at,
                       coalesce(a.enabled,1),a.expires_at,
                       (SELECT coalesce(sum(cost.charged),0) FROM trial_request_costs cost
                        JOIN trial_requests r ON r.id=cost.request_id
                        WHERE r.key_id=c.key_id AND r.state='active')
                FROM trial_codes c LEFT JOIN trial_access a ON a.key_id=c.key_id ORDER BY c.key_id
            """).fetchall()
        now = self.clock()
        return [{
            "key_id": key_id, "label": label, "usd_limit": usd(limit), "usd_spent": usd(used),
            "usd_reserved": usd(reserved), "usd_remaining": usd(max(0, limit - used)),
            "created_at": created, "last_used_at": last_used, "expires_at": expires_at,
            "active": bool(enabled) and (expires_at is None or expires_at > now) and used < limit,
        } for key_id, label, limit, created, used, last_used, enabled, expires_at, reserved in rows]

    def availability(self) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            issued, available = db.execute(
                "SELECT count(*), coalesce(sum(claim_hash IS NULL), 0) FROM trial_keys"
            ).fetchone()
        return {"total_keys": max(self.key_limit, issued), "issued_keys": issued, "available_keys": available}

    def authenticate(self, credential: str) -> str:
        digest = hashlib.sha256(credential.encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT key_id FROM trial_keys WHERE credential_hash=?", (digest,)
            ).fetchone()
        if not row:
            raise TrialError(401, "invalid_trial_key", "Invalid trial credential.")
        self.check_access(row[0])
        return row[0]

    def _check_access(self, db, key_id: str):
        row = db.execute("""
            SELECT coalesce(a.enabled,1),a.expires_at FROM trial_keys k
            LEFT JOIN trial_access a ON a.key_id=k.key_id WHERE k.key_id=?
        """, (key_id,)).fetchone()
        if not row:
            raise TrialError(401, "invalid_trial_key", "Invalid trial credential.")
        if not row[0] or (row[1] is not None and row[1] <= self.clock()):
            raise TrialError(403, "trial_access_disabled", "Invitation disabled or expired.")

    def check_access(self, key_id: str):
        with closing(sqlite3.connect(self.path)) as db:
            self._check_access(db, key_id)

    def tester_id(self, key_id: str) -> str:
        with self.transaction() as db:
            self._check_access(db, key_id)
            db.execute(
                "INSERT OR IGNORE INTO trial_testers(key_id,tester_id,created_at) VALUES (?,?,?)",
                (key_id, "tester_" + secrets.token_hex(16), self.clock()),
            )
            return db.execute("SELECT tester_id FROM trial_testers WHERE key_id=?", (key_id,)).fetchone()[0]

    def set_access(self, key_id: str, *, enabled: bool, expires_at: float | None = None):
        if type(enabled) is not bool or (
            expires_at is not None and (
                type(expires_at) not in (float, int) or not math.isfinite(expires_at) or expires_at < 0
            )
        ):
            raise ValueError("Access requires a boolean and a finite expiry timestamp or null")
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM trial_keys WHERE key_id=?", (key_id,)).fetchone():
                raise TrialError(404, "tester_not_found", "Tester not found.")
            db.execute("""
                INSERT INTO trial_access(key_id,enabled,expires_at) VALUES (?,?,?)
                ON CONFLICT(key_id) DO UPDATE SET enabled=excluded.enabled,expires_at=excluded.expires_at
            """, (key_id, int(enabled), expires_at))

    def access_info(self, key_id: str) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("""
                SELECT coalesce(a.enabled,1),a.expires_at,t.tester_id
                FROM trial_keys k LEFT JOIN trial_access a ON a.key_id=k.key_id
                LEFT JOIN trial_testers t ON t.key_id=k.key_id WHERE k.key_id=?
            """, (key_id,)).fetchone()
        if row is None:
            raise TrialError(404, "tester_not_found", "Tester not found.")
        return {
            "tenant_id": key_id, "tester_id": row[2], "enabled": bool(row[0]),
            "expires_at": row[1],
            "access_active": bool(row[0]) and (row[1] is None or row[1] > self.clock()),
        }

    def status(self, key_id: str) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            used = db.execute(
                "SELECT used FROM trial_keys WHERE key_id=?", (key_id,)
            ).fetchone()[0]
            active = db.execute("SELECT count(*) FROM trial_requests WHERE state='active'").fetchone()[0]
            tpm_used = db.execute(
                "SELECT coalesce(sum(tokens),0) FROM trial_tpm_reservations WHERE retain_until IS NULL OR retain_until>?",
                (self.clock(),),
            ).fetchone()[0]
            charges = dict(db.execute(
                "SELECT state,sum(charged) FROM trial_requests WHERE key_id=? GROUP BY state", (key_id,),
            ).fetchall())
            code = db.execute("""SELECT label,usd_limit,cost_used,
                (SELECT coalesce(sum(cost.charged),0) FROM trial_request_costs cost
                 JOIN trial_requests r ON r.id=cost.request_id WHERE r.key_id=? AND r.state='active')
                FROM trial_codes WHERE key_id=?""", (key_id, key_id)).fetchone()
        result = {
            "token_limit": self.token_limit, "tokens_used": used,
            "tokens_remaining": None if self.token_limit is None else max(0, self.token_limit - used),
            "token_unlimited": self.token_limit is None,
            "active_requests": active, "max_concurrency": MAX_CONCURRENCY,
            "global_tpm_limit": GLOBAL_TPM, "global_tpm_reserved": tpm_used,
            "global_tpm_remaining": max(0, GLOBAL_TPM - tpm_used),
            "tokens_settled": charges.get("settled", 0),
            "tokens_reserved": charges.get("active", 0),
            "tokens_uncertain": sum(v for k, v in charges.items() if k not in {"settled", "active"}),
            "tokens_unattributed": max(0, used - sum(charges.values())),
        }
        if code is not None:
            # Spend includes in-flight reservations, so the balance shown is
            # what a new request can actually be admitted against.
            label, limit, cost_used, cost_reserved = code
            result.update({"label": label, "usd_limit": usd(limit), "usd_spent": usd(cost_used),
                           "usd_reserved": usd(cost_reserved), "usd_remaining": usd(max(0, limit - cost_used)),
                           "quota_exhausted": cost_used >= limit})
        return result

    def reserve(self, key_id: str, amount: int, *, cost: int = 0, operation_key: str | None = None) -> int:
        """Reserve tokens and, for an activation code, a USD upper bound in nano-AIU."""
        if amount <= 0:
            raise ValueError("Reservation must be positive")
        if type(cost) is not int or cost < 0:
            raise ValueError("Cost reservation must be a non-negative integer")
        if operation_key is not None and (not isinstance(operation_key, str) or not 1 <= len(operation_key) <= 128):
            raise ValueError("Invalid billing operation key")
        with self.transaction() as db:
            if operation_key is not None:
                previous = db.execute("""SELECT r.* FROM trial_requests r
                    JOIN trial_request_operations o ON o.request_id=r.id
                    WHERE o.operation_key=?""", (operation_key,)).fetchone()
                if previous is not None:
                    if previous["key_id"] != key_id or previous["reserved"] != amount:
                        raise ValueError("Billing operation key belongs to another reservation")
                    return previous["id"]
            self._check_access(db, key_id)
            now = self.clock()
            db.execute("DELETE FROM trial_tpm_reservations WHERE retain_until<=?", (now,))
            used = db.execute(
                "SELECT used FROM trial_keys WHERE key_id=?", (key_id,)
            ).fetchone()[0]
            code = db.execute("SELECT usd_limit,cost_used FROM trial_codes WHERE key_id=?", (key_id,)).fetchone()
            # An activation code is limited in USD only; other keys keep tokens.
            if code is None and self.token_limit is not None and used + amount > self.token_limit:
                raise TrialError(402, "trial_quota_exceeded", "Insufficient trial tokens for this request.")
            if code is not None:
                if cost <= 0:
                    # A USD allowance cannot be enforced without a cost bound.
                    raise TrialError(400, "model_unpriced",
                                     "This model has no configured price, so the gateway refuses to meter it (model_unpriced).")
                if code["cost_used"] + cost > code["usd_limit"]:
                    raise quota_refusal(code["usd_limit"] - code["cost_used"], code["usd_limit"], cost)
            active = db.execute("SELECT count(*) FROM trial_requests WHERE state='active'").fetchone()[0]
            if active >= MAX_CONCURRENCY:
                raise TrialError(429, "trial_busy", "All 10 trial request slots are busy. Try again later.")
            tpm_used = db.execute("SELECT coalesce(sum(tokens),0) FROM trial_tpm_reservations").fetchone()[0]
            if tpm_used + amount > GLOBAL_TPM:
                remaining = tpm_used
                retry_after = TPM_WINDOW_SECONDS
                for row in db.execute("""SELECT tokens,retain_until FROM trial_tpm_reservations
                        WHERE retain_until IS NOT NULL ORDER BY retain_until"""):
                    remaining -= row["tokens"]
                    if remaining + amount <= GLOBAL_TPM:
                        retry_after = max(1, math.ceil(row["retain_until"] - now))
                        break
                raise TrialError(
                    429, "trial_tpm_exceeded", "Global trial TPM limit reached. Try again later.",
                    retry_after,
                )
            db.execute("UPDATE trial_keys SET used=used+? WHERE key_id=?", (amount, key_id))
            cursor = db.execute(
                "INSERT INTO trial_requests(key_id, reserved, charged) VALUES (?, ?, ?)",
                (key_id, amount, amount),
            )
            request_id = cursor.lastrowid
            db.execute("INSERT INTO trial_tpm_reservations(request_id,tokens) VALUES (?,?)", (request_id, amount))
            if code is not None:
                db.execute("UPDATE trial_codes SET cost_used=cost_used+?, last_used_at=? WHERE key_id=?",
                           (cost, now, key_id))
                db.execute("INSERT INTO trial_request_costs(request_id,reserved,charged) VALUES (?,?,?)",
                           (request_id, cost, cost))
            if operation_key is not None:
                db.execute("""INSERT INTO trial_request_operations(operation_key,request_id,phase)
                    VALUES (?,?,'reserved')""", (operation_key, request_id))
            return request_id

    def submit_operation(self, operation_key: str) -> None:
        """Commit permission to contact the provider, not a delivery receipt."""
        with self.transaction() as db:
            row = db.execute("""SELECT r.* FROM trial_requests r
                JOIN trial_request_operations o ON o.request_id=r.id
                WHERE o.operation_key=?""", (operation_key,)).fetchone()
            if row is None or row["state"] != "active":
                raise TrialError(409, "billing_operation_closed", "The model request is no longer active.")
            self._check_access(db, row["key_id"])
            db.execute("UPDATE trial_request_operations SET phase='submitted' WHERE operation_key=?", (operation_key,))

    def settle_operation(self, operation_key: str, actual: int | None, cost: int | None = None,
                         cost_source: str | None = None) -> None:
        """Recover ownership even if reserve committed without returning its ID."""
        self._check_usage(actual, cost)
        with self.transaction() as db:
            row = db.execute("""SELECT r.* FROM trial_requests r
                JOIN trial_request_operations o ON o.request_id=r.id
                WHERE o.operation_key=?""", (operation_key,)).fetchone()
            if row is not None:
                self._settle_row(db, row, actual, cost, cost_source)

    def settle(self, request_id: int, actual: int | None, cost: int | None = None,
               cost_source: str | None = None):
        self._check_usage(actual, cost)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM trial_requests WHERE id=?", (request_id,)).fetchone()
            self._settle_row(db, row, actual, cost, cost_source)

    @staticmethod
    def _check_usage(actual, cost):
        if actual is not None and actual < 0:
            raise ValueError("Usage cannot be negative")
        if cost is not None and (type(cost) is not int or cost < 0):
            raise ValueError("Cost cannot be negative")

    def _settle_row(self, db, row, actual, cost=None, cost_source=None):
        if row["state"] != "active":
            return
        reserved = db.execute("SELECT * FROM trial_request_costs WHERE request_id=?", (row["id"],)).fetchone()
        if reserved is not None:
            # Unknown usage keeps the whole reservation, whatever cost was seen.
            # Known zero usage (nothing generated) owes nothing. Known usage
            # without a cost figure keeps the bound rather than guess a refund.
            if actual is None or (cost is None and actual != 0):
                cost, cost_source = reserved["reserved"], "reserved"
            elif cost is None:
                cost = 0
            db.execute("UPDATE trial_codes SET cost_used=cost_used+? WHERE key_id=?",
                       (cost - reserved["charged"], row["key_id"]))
            db.execute("UPDATE trial_request_costs SET charged=?, source=? WHERE request_id=?",
                       (cost, cost_source, row["id"]))
        charge = row["reserved"] if actual is None else actual
        db.execute(
            "UPDATE trial_keys SET used=used+? WHERE key_id=?",
            (charge - row["charged"], row["key_id"]),
        )
        db.execute(
            "UPDATE trial_requests SET charged=?, state=? WHERE id=?",
            (charge, "unknown" if actual is None else "settled", row["id"]),
        )
        # One committed terminal transition fixes both lifetime charge and
        # rolling-window expiry; repeated cleanup cannot extend that window.
        db.execute(
            "UPDATE trial_tpm_reservations SET tokens=?, retain_until=? WHERE request_id=?",
            (charge, self.clock() + TPM_WINDOW_SECONDS, row["id"]),
        )
