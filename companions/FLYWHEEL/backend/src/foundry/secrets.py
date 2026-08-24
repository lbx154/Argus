from __future__ import annotations

import os
from collections.abc import Mapping
from threading import RLock
from urllib.parse import urlsplit, urlunsplit


def normalize_endpoint(value: str) -> str:
    """Canonicalize an HTTP(S) Argus base URL for credential binding."""

    parsed = urlsplit(str(value or "").strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Argus credential endpoint must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Argus credential endpoint must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("Argus credential endpoint must not contain query or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Argus credential endpoint has an invalid port") from exc
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


class SecretVault:
    """Process-local vault; SQLite stores references, never bearer values."""

    def __init__(
        self, *, allowed_env_endpoints: Mapping[str, str] | None = None
    ) -> None:
        self._values: dict[str, str] = {}
        self._lock = RLock()
        self._allowed_env_endpoints = {
            name.strip(): normalize_endpoint(endpoint)
            for name, endpoint in (allowed_env_endpoints or {}).items()
            if name and name.strip() and endpoint and endpoint.strip()
        }

    def validate_env_reference(self, name: str, endpoint: str | None) -> None:
        expected = self._allowed_env_endpoints.get(str(name or "").strip())
        if expected is None or endpoint is None:
            raise ValueError(
                "Environment credential reference is not in the server Argus endpoint allowlist"
            )
        if normalize_endpoint(endpoint) != expected:
            raise ValueError(
                "Environment credential reference is not valid for this Argus endpoint"
            )

    def put(self, owner_id: str, value: str) -> str:
        with self._lock:
            self._values[owner_id] = value
        return f"memory:{owner_id}"

    def remove(self, owner_id: str) -> None:
        with self._lock:
            self._values.pop(owner_id, None)

    def resolve(
        self, owner_id: str, reference: str | None, *, endpoint: str | None = None
    ) -> str | None:
        if not reference:
            return None
        if reference.startswith("env:"):
            name = reference[4:]
            self.validate_env_reference(name, endpoint)
            return os.getenv(name)
        if reference == f"memory:{owner_id}":
            with self._lock:
                return self._values.get(owner_id)
        return None
