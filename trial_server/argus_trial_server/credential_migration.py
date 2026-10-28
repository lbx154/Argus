"""Migration only: delete in the next release.

Earlier releases derived each trial credential from the master key with an HMAC
and stored only its SHA-256. The master key still derives them, so each is
written out once as the stored credential and the hash column goes away. This
is the one place Argus still hashes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import sqlite3
from contextlib import closing
from pathlib import Path


def migrate_derived_credentials(db_path: Path, master_key: Path) -> None:
    """Replace the legacy ``credential_hash`` column by the credentials themselves."""
    if not db_path.exists():
        return
    with closing(sqlite3.connect(db_path)) as db:
        if "credential_hash" not in {row[1] for row in db.execute("PRAGMA table_info(trial_keys)")}:
            return
        signing = hmac.digest(base64.urlsafe_b64decode(master_key.read_bytes().strip()),
                              b"argus-trial-key-v1", "sha256")
        rows = []
        for key_id, stored, used, claim in db.execute(
            "SELECT key_id, credential_hash, used, claim_hash FROM trial_keys"
        ):
            credential = "argus_trial_" + hmac.new(signing, key_id.encode(), hashlib.sha256).hexdigest()
            if hashlib.sha256(credential.encode()).hexdigest() != stored:
                raise ValueError("Existing trial keys do not match the master key; restore the original key")
            rows.append((key_id, credential, used, claim))
        with db:
            db.execute("""CREATE TABLE trial_keys_migrated (
                key_id TEXT PRIMARY KEY,
                credential TEXT UNIQUE NOT NULL,
                used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                claim_hash TEXT UNIQUE
            )""")
            db.executemany("INSERT INTO trial_keys_migrated VALUES (?, ?, ?, ?)", rows)
            db.execute("DROP TABLE trial_keys")
            db.execute("ALTER TABLE trial_keys_migrated RENAME TO trial_keys")


__all__ = ["migrate_derived_credentials"]
