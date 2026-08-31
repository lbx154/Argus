"""Deterministic release identity shared by backend, daemon, and frontends."""

from __future__ import annotations

import hashlib
import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

MANIFEST_FILE = "release_manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def _git_tracked_files(root: Path) -> set[str] | None:
    """Return repo-relative tracked paths, or None outside a Git checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }


def _source_files(root: Path) -> Iterable[Path]:
    patterns = (
        "argus_skill/**/*.py",
        "argus_skill/**/*.json",
        "argus_skill/**/*.md",
        "argus_skill/**/*.yaml",
        "argus_skill/**/*.yml",
        "frontend/core/src/**/*",
        "frontend/tui/src/**/*",
        "frontend/tui/bin/**/*",
        "frontend/tui/scripts/**/*",
        "frontend/web/src/**/*",
        "frontend/web/public/**/*",
        "frontend/*/package.json",
        "frontend/*/package-lock.json",
        "frontend/*/pnpm-lock.yaml",
        "frontend/*/tsconfig*.json",
        "frontend/web/*.config.*",
        "frontend/web/index.html",
        ".agents/plugins/marketplace.json",
        ".claude-plugin/marketplace.json",
        "plugins/argus/**/*.md",
        "plugins/argus/**/*.json",
        "plugins/argus/**/*.yaml",
        "plugins/argus/bin/*",
        "plugins/argus/install.sh",
        "plugins/argus/install.ps1",
        # Tauri is the sole Windows release host. Keep its Rust supervisor,
        # frozen-backend specification, local shell, updater configuration,
        # installer hook and packaging scripts in the same backend/cockpit
        # release identity.
        "desktop-tauri/src/**/*",
        "desktop-tauri/src-tauri/src/**/*",
        "desktop-tauri/src-tauri/capabilities/**/*",
        "desktop-tauri/src-tauri/icons/**/*",
        "desktop-tauri/resources/.gitignore",
        "desktop-tauri/resources/argus-backend/.gitkeep",
        "desktop-tauri/argus_backend.spec",
        "desktop-tauri/src-tauri/Cargo.toml",
        "desktop-tauri/src-tauri/Cargo.lock",
        "desktop-tauri/src-tauri/build.rs",
        "desktop-tauri/src-tauri/installer-hooks.nsh",
        "desktop-tauri/src-tauri/tauri.conf.json",
        # Only source scripts belong to the release. A broad glob would include
        # ignored __pycache__ bytecode produced by the packaged-host smoke test
        # and make the manifest change merely by running verification.
        "desktop-tauri/scripts/*.js",
        "desktop-tauri/scripts/*.mjs",
        "desktop-tauri/scripts/*.ps1",
        "desktop-tauri/scripts/*.py",
        "desktop-tauri/package.json",
        "desktop-tauri/package-lock.json",
        "desktop-tauri/tsconfig.json",
        "desktop-tauri/vite.config.ts",
        "argus_doctor.py",
        "pyproject.toml",
    )
    tracked = _git_tracked_files(root)
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if (
                not path.is_file()
                or path.name == MANIFEST_FILE
                or path.name.endswith(".generated.ts")
            ):
                continue
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            if (
                tracked is not None
                and relative not in tracked
                and relative.startswith("argus_skill/builtin_skills/")
            ):
                # Builtin-skill evolution may materialize runtime-only files in
                # an editable checkout. Those are deliberately outside the
                # shipped release identity. Other untracked source files are
                # release candidates (for example a newly added Python module
                # before its first commit) and must participate now; excluding
                # them makes build_release produce a digest that changes as soon
                # as the exact same files are committed.
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def compute_source_digest(root: Path | str) -> str:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    for path in sorted(_source_files(base), key=lambda item: item.as_posix()):
        relative = path.resolve().relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def release_manifest() -> dict[str, Any]:
    path = Path(__file__).with_name(MANIFEST_FILE)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "package_version": "unknown",
            "source_digest": "",
            "release_id": "unknown",
        }
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=4)
def release_identity(source_root: Path | str | None = None) -> dict[str, Any]:
    manifest = release_manifest()
    root = Path(source_root).resolve() if source_root is not None else None
    runtime_digest: str | None = None
    if (
        root is not None
        and (root / "pyproject.toml").is_file()
        and (root / "frontend" / "core" / "src").is_dir()
    ):
        try:
            runtime_digest = compute_source_digest(root)
        except OSError:
            runtime_digest = None
    expected = str(manifest.get("source_digest") or "")
    return {
        "release_id": str(manifest.get("release_id") or "unknown"),
        "manifest_source_digest": expected or None,
        "runtime_source_digest": runtime_digest,
        "release_matches_source": (
            None if runtime_digest is None or not expected else runtime_digest == expected
        ),
    }


__all__ = [
    "MANIFEST_FILE",
    "MANIFEST_SCHEMA_VERSION",
    "compute_source_digest",
    "release_identity",
    "release_manifest",
]
