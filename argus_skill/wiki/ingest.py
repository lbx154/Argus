"""Reviewer-side helpers for converting engineer-produced lit artifacts.

This module backfills wiki sources from `paper/refs.bib` and
`research/LIT_MATRIX.tsv`. It exists because the engineer may use native
search and write refs directly, bypassing the four named ingestion skills
that have per-skill wiki hooks.

Pure file I/O + stdlib parsing; no LLM calls.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .schema import SourcePaper, parse_frontmatter, serialize_frontmatter
from .store import WikiStore, _atomic_write_text

_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>[^?#\s]+)",
    flags=re.IGNORECASE,
)
_DOI_URL_RE = re.compile(r"(?:doi\.org/)(?P<doi>10\.[^?#\s]+)", flags=re.IGNORECASE)


@dataclass
class IngestResult:
    written: list[Path] = field(default_factory=list)
    enriched_count: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def parse_bib_entries(text: str) -> list[dict[str, str]]:
    """Return a list of BibTeX entries as `{key, field...}` dicts.

    Tolerates common entry types and nested/doubled braces in values.
    URL falls back to `https://doi.org/<doi>` when only `doi` is present.
    """
    entries: list[dict[str, str]] = []
    for raw_entry in _iter_bib_entries(text):
        parsed = _parse_bib_entry(raw_entry)
        if not parsed:
            continue
        if "url" not in parsed and "doi" in parsed:
            parsed["url"] = f"https://doi.org/{parsed['doi']}"
        entries.append(parsed)
    return entries


def canonical_paper_id(*, url: str | None, doi: str | None, key: str) -> str:
    """Return the canonical source stem for a paper.

    Priority: arXiv URL, DOI, then BibTeX key. Returned values are safe
    filename stems accepted by `WikiStore`.
    """
    arxiv_id = _arxiv_id_from_url(url or "")
    if arxiv_id:
        return f"arxiv-{_safe_component(_strip_arxiv_version(arxiv_id))}"
    doi_value = (doi or "").strip() or _doi_from_url(url or "")
    if doi_value:
        return "doi-" + _safe_component(doi_value.lower())
    return _safe_component(key)


def ingest_refs_bib(
    store: WikiStore,
    *,
    bib_path: Path,
    ingested_by: str,
    today: date | None = None,
) -> IngestResult:
    """Write one immutable `sources/papers/<key>.md` per BibTeX entry.

    Returns newly written paths. Already-present sources are skipped.
    Malformed entries are ignored rather than aborting the whole batch.
    """
    today = today or date.today()
    text = bib_path.read_text(encoding="utf-8")
    result = IngestResult()
    for entry in parse_bib_entries(text):
        try:
            key = entry.get("key", "").strip()
            if not key:
                result.skipped += 1
                continue
            canonical = canonical_paper_id(
                url=entry.get("url"),
                doi=entry.get("doi"),
                key=key,
            )
            title = entry.get("title", "").strip() or "(untitled)"
            url = entry.get("url", "").strip() or "about:blank"
            stanza = _reconstruct_stanza(entry)
            src = SourcePaper(
                id=f"papers/{canonical}",
                url=url,
                title=title,
                ingested_at=today,
                ingested_by=ingested_by,
                checksum=_checksum(stanza),
                body=stanza,
            )
            with store._wiki_lock():
                aliases = _load_aliases(store)
                existing = aliases.get(key)
                if existing and _paper_source_path_for_key(store, existing).exists():
                    result.skipped += 1
                    continue
                aliases[key] = canonical
                canonical_path = store.root / "sources" / "papers" / f"{canonical}.md"
                if canonical_path.exists():
                    _save_aliases(store, aliases)
                    result.skipped += 1
                    continue
                _atomic_write_text(canonical_path, serialize_frontmatter(src))
                _save_aliases(store, aliases)
                result.written.append(canonical_path)
        except Exception as exc:  # noqa: BLE001
            result.skipped += 1
            result.warnings.append(
                f"skipped bib entry {entry.get('key', '<unknown>')}: {type(exc).__name__}: {exc}"
            )
    return result


def ingest_lit_matrix(
    store: WikiStore,
    *,
    tsv_path: Path,
) -> IngestResult:
    """Append LIT_MATRIX relevance text to matching existing paper sources."""
    result = IngestResult()
    with tsv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        relevance_col = next(
            (c for c in (reader.fieldnames or []) if c.startswith("relevance")),
            None,
        )
        if relevance_col is None:
            return result
        for row in reader:
            key = (row.get("id") or "").strip()
            try:
                relevance = (row.get(relevance_col) or "").strip()
                if not key or not relevance:
                    result.skipped += 1
                    continue
                canonical = canonical_paper_id(
                    url=(row.get("url") or "").strip() or None,
                    doi=None,
                    key=key,
                )
                with store._wiki_lock():
                    path = _paper_source_path_for_key(store, key)
                    if not path.exists():
                        path = _paper_source_path_for_key(store, canonical)
                    if not path.exists():
                        result.skipped += 1
                        continue
                    src = parse_frontmatter(path.read_text(encoding="utf-8"), SourcePaper)
                    if relevance in src.body:
                        result.skipped += 1
                        continue
                    new_body = (src.body + f"\n\nrelevance: {relevance}").strip()
                    updated = SourcePaper(**{**src.__dict__, "body": new_body})
                    _atomic_write_text(path, serialize_frontmatter(updated))
                    aliases = _load_aliases(store)
                    aliases[key] = path.stem
                    _save_aliases(store, aliases)
                    result.enriched_count += 1
            except Exception as exc:  # noqa: BLE001
                result.skipped += 1
                result.warnings.append(
                    f"skipped LIT_MATRIX row {key or '<unknown>'}: {type(exc).__name__}: {exc}"
                )
                continue
    return result


def _paper_source_path_for_key(store: WikiStore, key: str) -> Path:
    aliases = _load_aliases(store)
    if key in aliases:
        alias_path = store.root / "sources" / "papers" / f"{aliases[key]}.md"
        if alias_path.exists():
            return alias_path
    direct = store.root / "sources" / "papers" / f"{key}.md"
    if direct.exists():
        return direct
    normalized = _normalize_key(key)
    if not normalized:
        return direct
    papers_root = store.root / "sources" / "papers"
    matches = [
        candidate
        for candidate in papers_root.glob("*.md")
        if _normalize_key(candidate.stem) == normalized
    ]
    if len(matches) == 1:
        return matches[0]
    return direct


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.lower())


def _alias_path(store: WikiStore) -> Path:
    return store.root / "data" / "paper_aliases.json"


def _load_aliases(store: WikiStore) -> dict[str, str]:
    path = _alias_path(store)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        str(k): str(v)
        for k, v in data.items()
        if isinstance(k, str) and isinstance(v, str)
    } if isinstance(data, dict) else {}


def _save_aliases(store: WikiStore, aliases: dict[str, str]) -> None:
    text = json.dumps(dict(sorted(aliases.items())), indent=2, sort_keys=True)
    _atomic_write_text(_alias_path(store), text + "\n")


def _arxiv_id_from_url(url: str) -> str:
    match = _ARXIV_URL_RE.search(url)
    if not match:
        return ""
    value = match.group("id").strip()
    if value.endswith(".pdf"):
        value = value[:-4]
    return value


def _doi_from_url(url: str) -> str:
    match = _DOI_URL_RE.search(url)
    return match.group("doi").strip() if match else ""


def _strip_arxiv_version(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id.strip(), flags=re.IGNORECASE)


def _safe_component(value: str) -> str:
    cleaned = value.strip().replace("/", "__").replace("\\", "__")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned).strip("_").lower()
    if not cleaned:
        raise ValueError("paper id is empty after normalization")
    if ".." in cleaned or cleaned.startswith("."):
        raise ValueError(f"invalid paper id after normalization: {value!r}")
    return cleaned


def _iter_bib_entries(text: str) -> list[str]:
    entries: list[str] = []
    idx = 0
    while True:
        at = text.find("@", idx)
        if at < 0:
            break
        open_brace = text.find("{", at)
        if open_brace < 0:
            break
        depth = 0
        in_quote = False
        escaped = False
        end = None
        for pos in range(open_brace, len(text)):
            ch = text[pos]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"' and not escaped:
                in_quote = not in_quote
                continue
            if in_quote:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = pos + 1
                    break
        if end is None:
            next_at = text.find("@", at + 1)
            if next_at < 0:
                break
            idx = next_at
            continue
        entries.append(text[at:end])
        idx = end
    return entries


def _parse_bib_entry(raw: str) -> dict[str, str]:
    header = re.match(r"@\w+\s*\{\s*([^,\s]+)\s*,", raw, flags=re.DOTALL)
    if not header:
        return {}
    key = header.group(1).strip()
    body = raw[header.end() :].strip()
    if body.endswith("}"):
        body = body[:-1].strip()
    fields = {"key": key}
    for name, value in _parse_fields(body):
        fields[name.lower()] = _clean_bib_value(value)
    return fields


def _parse_fields(body: str) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i] in " \t\r\n,":
            i += 1
        name_start = i
        while i < n and (body[i].isalnum() or body[i] in "_-"):
            i += 1
        name = body[name_start:i].strip()
        if not name:
            break
        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != "=":
            break
        i += 1
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            fields.append((name, ""))
            break
        if body[i] == "{":
            value, i = _read_braced_value(body, i)
        elif body[i] == '"':
            value, i = _read_quoted_value(body, i)
        else:
            start = i
            while i < n and body[i] != ",":
                i += 1
            value = body[start:i].strip()
        fields.append((name, value))
        while i < n and body[i] not in ",":
            i += 1
        if i < n and body[i] == ",":
            i += 1
    return fields


def _read_braced_value(text: str, start: int) -> tuple[str, int]:
    depth = 0
    escaped = False
    value_start = start + 1
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[value_start:i], i + 1
    return text[value_start:], len(text)


def _read_quoted_value(text: str, start: int) -> tuple[str, int]:
    escaped = False
    value_start = start + 1
    for i in range(start + 1, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            return text[value_start:i], i + 1
    return text[value_start:], len(text)


def _clean_bib_value(value: str) -> str:
    value = value.strip()
    # Common BibTeX protection for corporate authors: {{DeepSeek-AI}}.
    while value.startswith("{") and value.endswith("}"):
        inner, end = _read_braced_value(value, 0)
        if end != len(value):
            break
        value = inner.strip()
    return " ".join(value.split())


def _checksum(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reconstruct_stanza(entry: dict[str, str]) -> str:
    """Re-render a BibTeX entry as a provenance trail for the source body."""
    key = entry.get("key", "?")
    fields = [(k, v) for k, v in entry.items() if k != "key"]
    lines = [f"@misc{{{key},"]
    for k, v in fields:
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)
