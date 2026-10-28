"""Deterministic release identity shared by backend, daemon, and frontends."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

MANIFEST_FILE = "release_manifest.json"
MANIFEST_SCHEMA_VERSION = 2


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
        "argus/**/*.py",
        "argus/**/*.json",
        "argus/**/*.md",
        "argus/**/*.yaml",
        "argus/**/*.yml",
        # First-party browser/CLI adapters are product code too. Keep this
        # bounded to their source directories, never dependencies or bundles.
        "argus/webapi/*.js",
        "argus/agent_cli/*.mjs",
        "argus/advisor/*.mjs",
        "argus/core/*.mjs",
        "argus/life/*.mjs",
        "argus/messaging/*.mjs",
        "argus/verticals/**/*.mjs",
        "argus/verticals/**/*.ts",
        "argus/verticals/**/*.tsx",
        "argus/verticals/**/*.css",
        "argus/verticals/**/*.html",
        "argus/verticals/**/*.svg",
        "argus/verticals/**/*.toml",
        "frontend/core/src/**/*",
        "packages/*/src/**/*.ts",
        "packages/*/schemas/*.json",
        "packages/*/scripts/*.ts",
        "packages/*/package.json",
        "packages/*/tsconfig.json",
        "package.json",
        "package-lock.json",
        "tsconfig.base.json",
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
        "desktop-tauri/src-tauri/installer-template.nsi",
        "desktop-tauri/src-tauri/installer-template.LICENSE",
        "desktop-tauri/src-tauri/tauri.conf.json",
        "desktop-tauri/src-tauri/tauri.*.conf.json",
        # Only source scripts belong to the release. A broad glob would include
        # ignored __pycache__ bytecode produced by the packaged-host smoke test
        # and make the manifest change merely by running verification.
        "desktop-tauri/scripts/*.js",
        "desktop-tauri/scripts/*.mjs",
        "desktop-tauri/scripts/*.ps1",
        "desktop-tauri/scripts/*.py",
        "desktop-tauri/scripts/*.rs",
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
            relative_parts = path.relative_to(root).parts
            if len(relative_parts) > 3 and relative_parts[:2] == ("argus", "verticals"):
                optional = root / "argus" / "verticals" / relative_parts[2] / "workbench.json"
                if optional.is_file():
                    continue  # Optional packages ship as their own artifacts.
            # A bundled workbench is source, but its local dependencies and
            # generated assets must not change release identity or differ from
            # the installed wheel's file list.
            if path.is_relative_to(root / "argus" / "verticals") and any(
                part in {"node_modules", "__pycache__", "dist", ".venv", ".pytest_cache"}
                or part.endswith(".egg-info")
                for part in path.relative_to(root).parts
            ):
                continue
            if (
                not path.is_file()
                or path.name == MANIFEST_FILE
                or path.name.endswith(".generated.ts")
            ):
                continue
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            if relative == "argus/release_tools/check_repository_parity.py":
                # This private-repository CI helper checks publication parity;
                # it is not a product input. Keeping it must not give otherwise
                # identical public/private releases different identities.
                continue
            if (
                tracked is not None
                and relative not in tracked
                and relative.startswith("argus/builtin_skills/")
            ):
                # Builtin-skill evolution may materialize runtime-only files in
                # an editable checkout. Those are deliberately outside the
                # shipped release identity. Other untracked source files are
                # release candidates (for example a newly added Python module
                # before its first commit) and must participate now; excluding
                # them makes build_release produce a file list that changes as
                # soon as the exact same files are committed.
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def _source_stats(root: Path) -> dict[str, os.stat_result]:
    base = root.resolve()
    return {
        path.resolve().relative_to(base).as_posix(): path.stat()
        for path in sorted(_source_files(base), key=lambda item: item.as_posix())
    }


def source_files(root: Path | str) -> dict[str, int]:
    """Every shipped source file under ``root``, by relative path, with its size."""
    return {path: stat.st_size for path, stat in _source_stats(Path(root)).items()}


def _summary(files: dict[str, int]) -> str:
    return f"{len(files)} files, {sum(files.values())} bytes"


@lru_cache(maxsize=1)
def release_manifest() -> dict[str, Any]:
    path = Path(__file__).with_name(MANIFEST_FILE)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "package_version": "unknown",
            "release_id": "unknown",
        }
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=4)
def release_identity(source_root: Path | str | None = None) -> dict[str, Any]:
    """The shipped release, and whether a source checkout still matches it.

    A manifest lists every shipped source file with its size; a checkout whose
    files or sizes differ no longer matches. The runtime summary adds the newest
    modification time, so two processes on one machine also see an edit that
    kept a file's size.
    """
    manifest = release_manifest()
    root = Path(source_root).resolve() if source_root is not None else None
    stats: dict[str, os.stat_result] | None = None
    if (
        root is not None
        and (root / "pyproject.toml").is_file()
        and (root / "frontend" / "core" / "src").is_dir()
    ):
        try:
            stats = _source_stats(root)
        except OSError:
            stats = None
    expected = manifest.get("source_files")
    expected = expected if isinstance(expected, dict) and expected else None
    current = None if stats is None else {path: stat.st_size for path, stat in stats.items()}
    newest = max((stat.st_mtime_ns for stat in (stats or {}).values()), default=0)
    return {
        "release_id": str(manifest.get("release_id") or "unknown"),
        "manifest_source_digest": _summary(expected) if expected else None,
        "runtime_source_digest": (
            None if current is None else f"{_summary(current)}, newest {newest}"
        ),
        "release_matches_source": (
            None if current is None or expected is None else current == expected
        ),
    }


__all__ = [
    "MANIFEST_FILE",
    "MANIFEST_SCHEMA_VERSION",
    "release_identity",
    "release_manifest",
    "source_files",
]
