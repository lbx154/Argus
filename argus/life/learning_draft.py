"""Review answer-learning edits before they become canonical library content.

Provider errors and malformed drafts leave the live library intact. Each
published file is replaced atomically, with the previous version kept outside
normal discovery. A concurrent edit causes a retryable conflict, not lost work.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

import yaml

from ..core.scoped_file import open_regular_file

_FILE_LIMIT = 131_072
_TOTAL_LIMIT = 16_000_000


def _read(path: Path) -> str:
    with open_regular_file(path) as handle:
        raw = handle.read(_FILE_LIMIT + 1)
    if len(raw) > _FILE_LIMIT:
        raise ValueError("learning page exceeds the page size limit")
    return raw.decode("utf-8")


def _pages(root: Path) -> dict[Path, str]:
    pages = {}
    total = 0
    if not root.exists():
        return pages
    if root.resolve() != root.absolute():
        raise ValueError("learning library cannot be a symlink")
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not name.startswith((".", "_"))]
        if any((Path(directory) / name).is_symlink() for name in folders):
            raise ValueError("learning draft cannot contain directory aliases")
        for name in sorted(files):
            if name.startswith((".", "_")) or not name.endswith(".md"):
                continue
            path = Path(directory) / name
            text = _read(path)
            total += len(text.encode())
            if total > _TOTAL_LIMIT:
                raise ValueError("learning library exceeds the snapshot budget")
            pages[path.relative_to(root)] = text
    return pages


def _body(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    _front, separator, body = text[4:].partition("\n---\n")
    return body.strip() if separator else ""


def _validate(text: str, *, skill: bool) -> None:
    body = _body(text)
    if not body:
        raise ValueError("learning draft is missing its body or front matter")
    front = yaml.safe_load(text[4:].partition("\n---\n")[0])
    if not isinstance(front, dict) or any(
        not isinstance(front.get(key), str) or not front[key].strip()
        for key in ("name" if skill else "title", "description")
    ):
        raise ValueError("learning draft is missing its title/name or description")


def _replace(path: Path, text: str) -> None:
    if path.resolve() != path.absolute():
        raise ValueError("learning destination cannot follow filesystem aliases")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".learning-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class LearningDraft:
    def __init__(self, roots: dict[str, Path], scratch: Path):
        self.roots = roots
        self.before = {name: _pages(root) for name, root in roots.items()}
        scratch.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="answer-", dir=scratch)
        self.directory = Path(self._temporary.name)
        self.paths = {name: self.directory / name for name in roots}
        if "knowledge" in self.paths:
            self.paths["knowledge"] /= "pages/surveys"

    def __enter__(self):
        try:
            for name, pages in self.before.items():
                self.paths[name].mkdir(parents=True)
                for relative, content in pages.items():
                    path = self.paths[name] / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
            return self
        except Exception:
            self._temporary.cleanup()
            raise

    def __exit__(self, *_args):
        self._temporary.cleanup()

    def publish(self) -> list[Path]:
        edits: list[tuple[str, Path, str]] = []
        for name, root in self.paths.items():
            after = _pages(root)
            before = self.before[name]
            if before.keys() - after.keys():
                raise ValueError("learning cannot delete an existing page")
            known_bodies = {re.sub(r"\s+", " ", _body(text)).casefold() for text in before.values()}
            for relative, content in after.items():
                if relative.name == "INDEX.md" or before.get(relative) == content:
                    continue
                _validate(content, skill=name == "skills")
                # Retry or a new filename for an identical finding is not growth.
                signature = re.sub(r"\s+", " ", _body(content)).casefold()
                if relative not in before and signature in known_bodies:
                    continue
                known_bodies.add(signature)
                edits.append((name, relative, content))
        # Check all preconditions before publishing anything. Agents may edit a
        # canonical page while this background pass is still drafting.
        for name, relative, _content in edits:
            target = self.roots[name] / relative
            previous = self.before[name].get(relative)
            current = _read(target) if target.exists() else None
            if current != previous:
                raise ValueError("learning page changed while drafting; retry against the current version")
        published: list[tuple[str, Path]] = []
        try:
            for name, relative, content in edits:
                target = self.roots[name] / relative
                previous = self.before[name].get(relative)
                if previous is not None:
                    digest = hashlib.sha256(previous.encode()).hexdigest()[:16]
                    history = self.roots[name] / ".history" / relative.parent / f"{relative.stem}.{digest}.md"
                    _replace(history, previous)
                _replace(target, content)
                published.append((name, relative))
        except Exception:
            for name, relative in reversed(published):
                target = self.roots[name] / relative
                previous = self.before[name].get(relative)
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    _replace(target, previous)
            raise
        return [self.roots[name] / relative for name, relative in published]
