"""One streaming file hash for the whole runtime.

Six modules — the image API, the run contract, and four verticals — each
carried a byte-identical copy of this loop. A digest that provenance and
evidence checks compare across subsystems has to be computed the same way in
all of them, so it is computed in one place.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

#: Large enough that hashing a multi-gigabyte artifact is not syscall-bound,
#: small enough that it never holds one in memory.
_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path | str) -> str:
    """Return the hex SHA-256 of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["sha256_file"]
