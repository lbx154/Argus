"""Trial keys stored before credentials were kept as themselves still sign in."""
import base64
import hashlib
import hmac
import sqlite3

import pytest
from cryptography.fernet import Fernet

from argus_trial_server.secrets import write_private
from argus_trial_server.store import Store


def _legacy_pool(tmp_path, key_ids):
    """A pool as releases before stored credentials wrote it, and its derived credentials."""
    master = tmp_path / "master.key"
    write_private(master, Fernet.generate_key())
    signing = hmac.digest(base64.urlsafe_b64decode(master.read_bytes().strip()), b"argus-trial-key-v1", "sha256")
    credentials = {k: "argus_trial_" + hmac.new(signing, k.encode(), hashlib.sha256).hexdigest() for k in key_ids}
    path = tmp_path / "usage.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE trial_keys (key_id TEXT PRIMARY KEY, credential_hash TEXT UNIQUE NOT NULL,
                      used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0), claim_hash TEXT UNIQUE)""")
        db.executemany("INSERT INTO trial_keys(key_id, credential_hash, used) VALUES (?, ?, 7)",
                       [(k, hashlib.sha256(c.encode()).hexdigest()) for k, c in credentials.items()])
    return master, path, credentials


def test_derived_credentials_are_stored_once_and_keep_signing_in(tmp_path):
    master, path, credentials = _legacy_pool(tmp_path, ["trial-01", "trial-02"])
    with pytest.raises(ValueError, match="master key"):
        Store(path)
    store = Store(path, master_key=master)
    for key_id, credential in credentials.items():
        assert store.authenticate(credential) == key_id
        assert store.credential(key_id) == credential
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(trial_keys)")}
        assert "credential_hash" not in columns
        assert db.execute("SELECT used FROM trial_keys WHERE key_id='trial-01'").fetchone()[0] == 7
    assert Store(path).credential("trial-02") == credentials["trial-02"]


def test_a_different_master_key_leaves_the_legacy_pool_untouched(tmp_path):
    _, path, _ = _legacy_pool(tmp_path, ["trial-01"])
    other = tmp_path / "other.key"
    write_private(other, Fernet.generate_key())
    with pytest.raises(ValueError, match="original key"):
        Store(path, master_key=other)
    with sqlite3.connect(path) as db:
        assert "credential_hash" in {row[1] for row in db.execute("PRAGMA table_info(trial_keys)")}


def test_sessions_are_random_server_records(tmp_path):
    store = Store(tmp_path / "usage.sqlite3")
    token = store.open_session({"tenant": "trial-01", "role": "trial"}, store.clock() + 60)
    assert store.session(token) == {"tenant": "trial-01", "role": "trial"}
    assert store.session(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert store.open_session({}, store.clock() + 60) != token
    store.close_session(token)
    assert store.session(token) is None
    expired = store.open_session({"tenant": "trial-01"}, store.clock() - 1)
    assert store.session(expired) is None
