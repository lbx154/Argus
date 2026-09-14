"""Read a bounded set of explicit local evidence references."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Callable

from ..core.scoped_file import open_regular_file


def collect_evidence(
    references: list[str], *, workspace: Path, project_root: Path,
    byte_limit: int, redact: Callable[[str], str],
) -> list[dict]:
    if not isinstance(references, list) or len(references) > 16 or any(not isinstance(ref, str) for ref in references):
        raise ValueError("provide at most 16 evidence references")
    evidence = []
    remaining = byte_limit
    for reference in dict.fromkeys(references):
        if not isinstance(reference, str) or not reference or len(reference) > 1024:
            raise ValueError("invalid evidence reference")
        scope, separator, relative = reference.partition(":")
        if not separator:
            scope, relative = "workspace", reference
        if scope not in {"workspace", "state"}:
            raise ValueError("evidence scope must be workspace or state")
        path = Path(relative)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("evidence must name a relative project file")
        if any(part.lower() in {".git", ".ssh", "secrets", "credentials", "auth.json", "advisor"}
               or part.lower().startswith(".env") for part in path.parts):
            raise ValueError("credential and private runtime files are not advisor evidence")
        root = (workspace if scope == "workspace" else project_root).absolute()
        if root.resolve() != root:
            raise ValueError("evidence root must be canonical")
        target = (root / path).resolve(strict=True)
        if not target.is_relative_to(root):
            raise ValueError("evidence escapes its project")
        if remaining <= 0:
            raise ValueError("advisor evidence byte limit exceeded")
        with open_regular_file(target) as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("advisor evidence must be a regular file")
            raw = source.read(remaining + 1)
            after = os.fstat(source.fileno())
        if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise ValueError("evidence file changed during read")
        truncated = len(raw) > remaining
        raw = raw[:remaining]
        remaining -= len(raw)
        if b"\x00" in raw:
            raise ValueError("advisor evidence must be text")
        text = redact(raw.decode("utf-8", errors="replace"))
        evidence.append({
            "ref": f"{scope}:{path.as_posix()}", "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes_read": len(raw), "file_size": metadata.st_size,
            "truncated": truncated,
        })
    return evidence
