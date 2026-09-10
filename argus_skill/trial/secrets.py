"""Authenticated encryption at rest; the master key is server-owned."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import tempfile
from pathlib import Path


def write_private(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".trial-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Vault:
    def __init__(self, key_path: Path, token_path: Path):
        from cryptography.fernet import Fernet

        key = key_path.read_bytes().strip()
        if os.name != "nt" and key_path.stat().st_mode & 0o077:
            raise ValueError("Trial master key must have mode 0600")
        self.cipher = Fernet(key)
        # Separate derivation domain from the encryption use of the master key.
        self.signing_key = hmac.digest(base64.urlsafe_b64decode(key), b"argus-trial-key-v1", "sha256")
        self.token_path = token_path

    def credential(self, key_id: str) -> str:
        signature = hmac.new(self.signing_key, key_id.encode(), hashlib.sha256).hexdigest()
        return "argus_trial_" + signature

    def save(self, token: str):
        if not token.strip() or any(c.isspace() for c in token):
            raise ValueError("Invalid GitHub credential")
        write_private(self.token_path, self.cipher.encrypt(token.encode()))

    def read(self) -> str:
        from cryptography.fernet import InvalidToken

        try:
            return self.cipher.decrypt(self.token_path.read_bytes()).decode()
        except (OSError, InvalidToken, UnicodeError):
            raise ValueError("Copilot credential is missing or cannot be decrypted") from None
