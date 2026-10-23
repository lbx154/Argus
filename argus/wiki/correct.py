"""Correct a knowledge page where its readers look first, keeping what it said.

A page that turned out to be wrong is fixed in the two places a role or a
person reads before anything else: the summary in its front matter and the
paragraph that opens its body. The previous wording moves to a ``## History``
section at the end of the page, which recall does not search, and the file as
it was is kept under the library's ``.history`` directory, which browsing and
recall skip. Each History entry starts with the date and who corrected the
page, which is where recall reads the "corrected <date>" it shows in the page's
line: the front matter keeps the two fields of :mod:`argus.wiki.schema`, and
only its summary line is rewritten, so comments and other keys stay as written.
The knowledge journal records the correction so the feed shows it.

A page that no longer holds at all -- its problem was fixed, later facts
overturned it -- is retired: it moves whole into ``.history`` with a
``retired by`` History entry, so nothing reads it again, and the journal
records who retired it and why.

Layer: capabilities
"""
from __future__ import annotations

import logging
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .journal import append_knowledge_event
from .store import _atomic_write_text

log = logging.getLogger(__name__)

HISTORY_DIRNAME = ".history"
HISTORY_HEADING = "## History"
# A kept copy is named ``<stem>.<HISTORY_STAMP>[-<n>]<suffix>``, in UTC.
HISTORY_STAMP = "%Y%m%dT%H%M%SZ"
_FRONT_MATTER_CHARS = 8_000
_TEXT_LIMIT = 4_000
# Lines that open something other than a prose paragraph.
_NOT_PROSE_RE = re.compile(r"^(?:#|\||---|```|~~~|>|[-*+]\s|\d+[.)]\s)")
_TOP_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:")


@dataclass(frozen=True)
class PageCorrection:
    """What a correction did to one page."""

    page: Path
    relative: str
    title: str
    page_kind: str
    source: str
    corrected: str
    previous_description: str
    description: str
    previous_lead: str
    statement: str
    history_copy: Path
    index_updated: bool


class PageCorrectionError(ValueError):
    """The page cannot be corrected as asked; the message says why."""


def _utc(now: float | None) -> datetime:
    return datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc)


def _one_paragraph(value: str, *, name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise PageCorrectionError(f"a correction needs a non-empty {name}")
    if len(text) > _TEXT_LIMIT:
        raise PageCorrectionError(f"the {name} is longer than {_TEXT_LIMIT} characters")
    return text


def _resolve_page(library: Path, page: str | Path) -> tuple[Path, str]:
    """The page file and its path relative to the library (``pages/...``)."""
    pages_root = (library / "pages").resolve()
    candidate = Path(page)
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        parts = candidate.parts
        if parts and parts[0] == "pages":
            candidate = Path(*parts[1:]) if len(parts) > 1 else Path()
        target = (pages_root / candidate).resolve()
    if not target.is_relative_to(pages_root) or target == pages_root:
        raise PageCorrectionError("the page must sit under the library's pages/ directory")
    relative = target.relative_to(pages_root)
    if target.suffix.casefold() != ".md":
        raise PageCorrectionError("a knowledge page ends in .md")
    if any(part.startswith(".") or part == "_retired" for part in relative.parts):
        raise PageCorrectionError("hidden and retired pages are not corrected in place")
    if not target.is_file():
        raise PageCorrectionError(f"no page at {Path('pages') / relative}")
    return target, (Path("pages") / relative).as_posix()


def _split_front_matter(text: str) -> tuple[dict[str, Any] | None, str, str]:
    """(front matter mapping, its raw text, body); the mapping is None when the page has none.

    A page that opens with a front matter block which does not parse is refused
    rather than given a second block on top of the first.
    """
    if not text.startswith("---\n"):
        return None, "", text
    front, separator, body = text[4:].partition("\n---\n")
    loaded: Any = None
    if separator and len(front) <= _FRONT_MATTER_CHARS:
        try:
            loaded = yaml.safe_load(front) or {}
        except yaml.YAMLError:
            loaded = None
    if not isinstance(loaded, dict):
        raise PageCorrectionError("the page's front matter cannot be read; fix it by hand first")
    return dict(loaded), front, body


def _is_prose(raw: str) -> bool:
    line = raw.strip()
    return bool(line) and not _NOT_PROSE_RE.match(line)


def _lead_span(lines: list[str]) -> tuple[int, int] | None:
    """[start, end) of the page's opening paragraph, None when the page opens otherwise.

    The lead is the paragraph right after the title (or at the top when there
    is no title); a section, list, table, quote or code block there means the
    page has no lead, and prose further down belongs to its section.
    """
    start = _insert_index(lines)
    if start >= len(lines) or not _is_prose(lines[start]):
        return None
    end = start
    while end < len(lines) and _is_prose(lines[end]):
        end += 1
    return start, end


def _insert_index(lines: list[str]) -> int:
    """Where a lead goes when the page has none: after the title and its blank line."""
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].lstrip().startswith("# "):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    return index


def _with_history(body_lines: list[str], entry: str) -> list[str]:
    """The body with ``entry`` appended under the trailing History section."""
    lines = list(body_lines)
    while lines and not lines[-1].strip():
        lines.pop()
    heading_at = next(
        (index for index, raw in enumerate(lines) if raw.strip().casefold() == HISTORY_HEADING.casefold()),
        None,
    )
    if heading_at is not None and not any(
        raw.lstrip().startswith("#") and 0 < len(raw.lstrip().split(" ")[0]) <= 2
        for raw in lines[heading_at + 1:]
    ):
        return [*lines, entry]
    return [*lines, "", HISTORY_HEADING, "", entry]


def _quoted(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def _sentence(text: str) -> str:
    """``text`` ending in sentence punctuation, so a following sentence reads as one."""
    return text if text[-1] in ".!?。！？…\"'”’)" else text + "."


def _yaml_line(key: str, value: str) -> str:
    return yaml.safe_dump({key: value}, allow_unicode=True, width=1_000_000).strip()


def _set_front_value(front_text: str, key: str, value: str) -> str:
    """``front_text`` with top-level ``key`` set to ``value``; every other line kept as written."""
    lines = front_text.split("\n") if front_text else []
    new = _yaml_line(key, value).split("\n")
    for index, raw in enumerate(lines):
        match = _TOP_KEY_RE.match(raw)
        if not match or match[1] != key:
            continue
        end = index + 1
        # The value continues on indented (or blank) lines: a block or folded scalar.
        while end < len(lines) and (not lines[end].strip() or lines[end][:1] in " \t"):
            end += 1
        while end > index + 1 and not lines[end - 1].strip():
            end -= 1
        return "\n".join([*lines[:index], *new, *lines[end:]])
    return "\n".join([*lines, *new])


def _update_index_line(library: Path, relative: str, line: str) -> bool:
    """Replace the INDEX.md entry linking ``relative`` with ``line``; an empty ``line`` drops it."""
    index_path = library / "INDEX.md"
    try:
        existing = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    marker = f"]({relative})"
    if marker not in existing:
        return False
    lines = existing.splitlines(keepends=True)
    changed = [line if marker in old else old for old in lines]
    if changed == lines:
        return False
    _atomic_write_text(index_path, "".join(changed))
    return True


def _keep_history_copy(library: Path, relative: str, target: Path, stamp: datetime,
                       data: bytes | None = None) -> Path:
    """Keep the page (or ``data`` in its place) in ``.history``; a taken name gets a counter."""
    folder = (library / HISTORY_DIRNAME / Path(relative).relative_to("pages")).parent
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{target.stem}.{stamp.strftime(HISTORY_STAMP)}"
    data = target.read_bytes() if data is None else data
    counter = 0
    while True:
        copy = folder / (f"{base}{target.suffix}" if not counter else f"{base}-{counter}{target.suffix}")
        try:
            with copy.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            counter += 1
            continue
        shutil.copystat(target, copy)
        return copy


def _title_of(front: dict[str, Any], body: str, page: Path) -> str:
    title = str(front.get("title") or front.get("name") or "").strip()
    if title:
        return title
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return page.stem


def correct_page(
    library: str | Path,
    page: str | Path,
    *,
    statement: str,
    reason: str,
    description: str = "",
    corrected_by: str = "operator",
    scope: str = "project",
    vertical: str = "",
    global_root: str | Path | None = None,
    now: float | None = None,
) -> PageCorrection:
    """Replace the page's lead (and summary) with ``statement``; keep what it said.

    ``statement`` becomes the first paragraph of the body, ``description`` the
    front matter summary when given, and ``reason`` explains in the History
    section why the earlier wording was wrong. The earlier file is copied to
    ``<library>/.history/`` before the page is rewritten. When ``global_root``
    is given the knowledge journal records the correction.
    """
    library = Path(library).expanduser().resolve()
    statement = _one_paragraph(statement, name="corrected statement")
    reason = _one_paragraph(reason, name="reason")
    description = " ".join(str(description or "").split())
    if len(description) > _TEXT_LIMIT:
        raise PageCorrectionError(f"the description is longer than {_TEXT_LIMIT} characters")
    corrected_by = " ".join(str(corrected_by or "").split()) or "operator"
    target, relative = _resolve_page(library, page)
    try:
        original = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PageCorrectionError(f"cannot read {relative}: {exc}") from exc
    text = original.replace("\r\n", "\n")
    front, front_text, body = _split_front_matter(text)
    had_front = front is not None
    front = front or {}
    title = _title_of(front, body, target)
    previous_description = str(front.get("description") or "").strip()
    page_kind = str(front.get("kind") or "").strip().lower() or "page"
    source = str(front.get("source") or "").strip()

    body_lines = body.split("\n")
    span = _lead_span(body_lines)
    if span is None:
        previous_lead = ""
        at = _insert_index(body_lines)
        body_lines[at:at] = [statement, ""]
    else:
        start, end = span
        previous_lead = " ".join(" ".join(body_lines[start:end]).split())
        body_lines[start:end] = [statement]
    stamp = _utc(now)
    date = stamp.date().isoformat()
    entry = f"- {date}, corrected by {corrected_by}: {_sentence(reason)}"
    if previous_lead and previous_lead != statement:
        entry += f" The page previously said: {_quoted(previous_lead)}."
    if description and previous_description and previous_description != description:
        entry += f" Its summary was: {_quoted(previous_description)}."
    body_lines = _with_history(body_lines, entry)
    body_text = "\n".join(body_lines).rstrip("\n") + "\n"

    new_description = description or previous_description or statement
    if not had_front:
        front_text = "\n".join([_yaml_line("title", title), _yaml_line("description", new_description)])
        body_text = "\n" + body_text.lstrip("\n")
    else:
        if not str(front.get("title") or "").strip():
            front_text = _set_front_value(front_text, "title", title)
        if new_description != previous_description:
            front_text = _set_front_value(front_text, "description", new_description)
    new_text = f"---\n{front_text}\n---\n{body_text}"

    history_copy = _keep_history_copy(library, relative, target, stamp)
    _atomic_write_text(target, new_text)
    index_updated = _update_index_line(
        library, relative, f"- [{title}]({relative}) — {new_description}\n")

    if global_root is not None:
        append_knowledge_event(
            global_root,
            kind="corrected",
            scope=scope,
            vertical=vertical,
            path=relative,
            title=title,
            source_project=source,
            role=corrected_by,
            page_kind=page_kind,
            note=reason,
        )
    return PageCorrection(
        page=target,
        relative=relative,
        title=title,
        page_kind=page_kind,
        source=source,
        corrected=date,
        previous_description=previous_description,
        description=new_description,
        previous_lead=previous_lead,
        statement=statement,
        history_copy=history_copy,
        index_updated=index_updated,
    )


def retire_page(
    library: str | Path,
    page: str | Path,
    *,
    reason: str,
    retired_by: str = "operator",
    scope: str = "project",
    vertical: str = "",
    global_root: str | Path | None = None,
    mission_id: str = "",
    now: float | None = None,
) -> Path:
    """Take a page that no longer holds out of its library; return where it is kept.

    The page gains a ``- <date>, retired by <who>: <reason>`` History entry and
    moves into the library's ``.history`` directory, which browsing, recall,
    promotion and consolidation skip. Its INDEX line goes, and when
    ``global_root`` is given the knowledge journal records the retirement.
    """
    library = Path(library).expanduser().resolve()
    reason = _one_paragraph(reason, name="reason")
    retired_by = " ".join(str(retired_by or "").split()) or "operator"
    target, relative = _resolve_page(library, page)
    try:
        text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError) as exc:
        raise PageCorrectionError(f"cannot read {relative}: {exc}") from exc
    front, _front_text, body = _split_front_matter(text)
    front = front or {}
    stamp = _utc(now)
    entry = f"- {stamp.date().isoformat()}, retired by {retired_by}: {_sentence(reason)}"
    head = text[: len(text) - len(body)]
    retired = head + "\n".join(_with_history(body.split("\n"), entry)) + "\n"
    kept = _keep_history_copy(library, relative, target, stamp, retired.encode("utf-8"))
    target.unlink()
    _update_index_line(library, relative, "")
    if global_root is not None:
        append_knowledge_event(
            global_root,
            kind="retired",
            scope=scope,
            vertical=vertical,
            path=relative,
            title=_title_of(front, body, target),
            source_project=str(front.get("source") or "").strip(),
            mission_id=mission_id,
            role=retired_by,
            page_kind=str(front.get("kind") or "").strip().lower() or "page",
            note=reason,
        )
    return kept


def retire_named(
    rows: Iterable[Mapping[str, str]],
    libraries: dict[Path, tuple[str, str]],
    *,
    retired_by: str,
    global_root: str | Path,
    mission_id: str = "",
) -> list[str]:
    """Retire the pages a model named, each a ``{"page", "why"}`` row of its form.

    The model judges which pages no longer hold; the host only retires a named
    page that sits in one of ``libraries`` (library root to its scope and
    vertical) and records it. Returns the pages retired, as named.
    """
    retired: list[str] = []
    for page, reason in ((row["page"], row["why"]) for row in rows):
        for library, (scope, vertical) in libraries.items():
            try:
                retire_page(
                    library, page, reason=reason, retired_by=retired_by, scope=scope,
                    vertical=vertical, global_root=global_root, mission_id=mission_id,
                )
            except PageCorrectionError:
                continue
            except OSError:
                log.warning("%s could not retire %s", retired_by, page, exc_info=True)
            else:
                retired.append(page)
            break
        else:
            log.warning("%s named %r to retire without a reason or outside its libraries",
                        retired_by, page)
    return retired


__all__ = [
    "HISTORY_DIRNAME",
    "HISTORY_HEADING",
    "HISTORY_STAMP",
    "PageCorrection",
    "PageCorrectionError",
    "correct_page",
    "retire_named",
    "retire_page",
]
