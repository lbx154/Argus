"""Authenticated encryption at rest; the master key is server-owned."""
from __future__ import annotations

import os
from pathlib import Path

from argus.core.atomic_replace import write_atomic


def write_private(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_atomic(path, content, fsync=True, mode=0o600)


class Vault:
    def __init__(self, key_path: Path, token_path: Path):
        from cryptography.fernet import Fernet

        key = key_path.read_bytes().strip()
        if os.name != "nt" and key_path.stat().st_mode & 0o077:
            raise ValueError("Trial master key must have mode 0600")
        self.cipher = Fernet(key)
        self.key_path = key_path
        self.token_path = token_path

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
