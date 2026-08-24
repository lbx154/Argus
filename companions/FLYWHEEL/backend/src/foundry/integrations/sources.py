"""Evidence-source refresh adapters with explicit cache and demo semantics."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class SourceItem:
    item_id: str
    title: str
    url: str
    updated_at: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class SourceUpdate:
    source: str
    query: str
    status: str  # fresh | unchanged | cache | stale_cache | demo | error
    fetched_at: str
    items: tuple[SourceItem, ...]
    added_ids: tuple[str, ...]
    removed_ids: tuple[str, ...]
    changed_ids: tuple[str, ...]
    difference_summary: str
    error: str | None = None
    cache_age_seconds: float | None = None


Fetcher = Callable[[str, Mapping[str, str], float], tuple[int, bytes, Mapping[str, str]]]


class JsonCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, namespace: str, query: str) -> Path:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return self.root / namespace / f"{digest}.json"

    def read(self, namespace: str, query: str) -> dict[str, Any] | None:
        path = self.path_for(namespace, query)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def write(self, namespace: str, query: str, value: Mapping[str, Any]) -> None:
        path = self.path_for(namespace, query)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _url_fetch(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes, Mapping[str, str]]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured official APIs
            return int(response.status), response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(), dict(exc.headers)


class _CachedAdapter:
    namespace = "source"
    ttl_seconds = 86400.0

    def __init__(
        self,
        *,
        cache_dir: Path,
        timeout: float = 12.0,
        fetcher: Fetcher = _url_fetch,
    ) -> None:
        self.cache = JsonCache(cache_dir)
        self.timeout = timeout
        self.fetcher = fetcher

    def _cached(self, query: str) -> tuple[dict[str, Any] | None, float | None]:
        cached = self.cache.read(self.namespace, query)
        if not cached:
            return None, None
        age = max(0.0, time.time() - float(cached.get("fetched_timestamp", 0.0)))
        return cached, age

    @staticmethod
    def _items_from_cache(cached: Mapping[str, Any]) -> tuple[SourceItem, ...]:
        return tuple(SourceItem(**row) for row in cached.get("items", []) if isinstance(row, dict))

    def _finish(
        self,
        query: str,
        items: tuple[SourceItem, ...],
        previous: Mapping[str, Any] | None,
        *,
        status: str = "fresh",
        response_metadata: Mapping[str, Any] | None = None,
    ) -> SourceUpdate:
        old_items = self._items_from_cache(previous or {})
        old = {item.item_id: item for item in old_items}
        new = {item.item_id: item for item in items}
        added = tuple(sorted(new.keys() - old.keys()))
        removed = tuple(sorted(old.keys() - new.keys()))
        changed = tuple(sorted(key for key in new.keys() & old.keys() if new[key] != old[key]))
        if previous is not None and not added and not removed and not changed:
            status = "unchanged"
        summary = f"新增 {len(added)}，移除 {len(removed)}，元数据变化 {len(changed)}"
        payload = {
            "fetched_at": _utc_now(),
            "fetched_timestamp": time.time(),
            "items": [asdict(item) for item in items],
            "response_metadata": dict(response_metadata or {}),
        }
        self.cache.write(self.namespace, query, payload)
        return SourceUpdate(
            self.namespace, query, status, payload["fetched_at"], items,
            added, removed, changed, summary,
        )

    def _cache_update(self, query: str, cached: Mapping[str, Any], age: float, status: str) -> SourceUpdate:
        items = self._items_from_cache(cached)
        return SourceUpdate(
            self.namespace, query, status, str(cached.get("fetched_at") or _utc_now()),
            items, (), (), (), "缓存未刷新；没有声称外部来源发生变化", cache_age_seconds=age,
        )

    def _error_or_stale(self, query: str, cached: Mapping[str, Any] | None, age: float | None, exc: Exception) -> SourceUpdate:
        message = str(exc)
        if cached is not None:
            result = self._cache_update(query, cached, age or 0.0, "stale_cache")
            return SourceUpdate(
                source=result.source,
                query=result.query,
                status=result.status,
                fetched_at=result.fetched_at,
                items=result.items,
                added_ids=result.added_ids,
                removed_ids=result.removed_ids,
                changed_ids=result.changed_ids,
                difference_summary=result.difference_summary,
                error=message,
                cache_age_seconds=result.cache_age_seconds,
            )
        return SourceUpdate(
            self.namespace, query, "error", _utc_now(), (), (), (), (),
            "外部更新失败；没有生成伪造条目", error=message,
        )

    def demo(self, query: str, fixture: Path) -> SourceUpdate:
        data = json.loads(fixture.read_text(encoding="utf-8"))
        items = tuple(SourceItem(**row) for row in data.get("items", []))
        return SourceUpdate(
            self.namespace, query, "demo", _utc_now(), items,
            tuple(item.item_id for item in items), (), (),
            f"演示 fixture：{len(items)} 条；未调用外部服务",
        )


class ArxivAdapter(_CachedAdapter):
    namespace = "arxiv"
    ttl_seconds = 86400.0  # same query: at most once per day
    _rate_lock = threading.Lock()
    _last_request = 0.0

    def refresh(self, query: str, *, max_results: int = 50, force: bool = False) -> SourceUpdate:
        cache_key = f"{query}|{max_results}"
        cached, age = self._cached(cache_key)
        if cached and age is not None and age < self.ttl_seconds and not force:
            return self._cache_update(cache_key, cached, age, "cache")
        params = urllib.parse.urlencode({
            "search_query": query, "start": 0, "max_results": max_results,
            "sortBy": "lastUpdatedDate", "sortOrder": "descending",
        })
        url = "https://export.arxiv.org/api/query?" + params
        try:
            with self._rate_lock:
                delay = max(0.0, 3.0 - (time.monotonic() - self._last_request))
                if delay:
                    time.sleep(delay)
                status, raw, headers = self.fetcher(
                    url, {"User-Agent": "Argus-Flywheel/1 (research source monitor)"}, self.timeout
                )
                self.__class__._last_request = time.monotonic()
            if status != 200:
                raise RuntimeError(f"arXiv returned HTTP {status}")
            root = ET.fromstring(raw)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items: list[SourceItem] = []
            for entry in root.findall("atom:entry", ns):
                entry_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
                title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
                updated = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip() or None
                if entry_id:
                    items.append(SourceItem(entry_id.rsplit("/", 1)[-1], title, entry_id, updated))
            return self._finish(cache_key, tuple(items), cached, response_metadata=headers)
        except (OSError, RuntimeError, ET.ParseError, urllib.error.URLError) as exc:
            return self._error_or_stale(cache_key, cached, age, exc)


class OpenReviewAdapter(_CachedAdapter):
    namespace = "openreview"
    ttl_seconds = 21600.0

    def refresh(self, accepted_venue_id: str, *, limit: int = 1000, force: bool = False) -> SourceUpdate:
        query = f"{accepted_venue_id}|{limit}"
        cached, age = self._cached(query)
        if cached and age is not None and age < self.ttl_seconds and not force:
            return self._cache_update(query, cached, age, "cache")
        params = urllib.parse.urlencode({"content.venueid": accepted_venue_id, "limit": limit})
        url = "https://api2.openreview.net/notes?" + params
        try:
            status, raw, headers = self.fetcher(url, {"User-Agent": "Argus-Flywheel/1"}, self.timeout)
            if status != 200:
                raise RuntimeError(f"OpenReview returned HTTP {status}")
            data = json.loads(raw.decode("utf-8"))
            items: list[SourceItem] = []
            for note in data.get("notes", []):
                content = note.get("content") or {}
                title_value = content.get("title") or ""
                title = str(title_value.get("value") if isinstance(title_value, dict) else title_value)
                note_id = str(note.get("id") or note.get("forum") or "")
                if note_id:
                    items.append(SourceItem(
                        note_id, title, f"https://openreview.net/forum?id={note.get('forum') or note_id}",
                        metadata={"venue_id": accepted_venue_id},
                    ))
            return self._finish(query, tuple(items), cached, response_metadata=headers)
        except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
            return self._error_or_stale(query, cached, age, exc)


class GitHubAdapter(_CachedAdapter):
    namespace = "github"
    ttl_seconds = 3600.0

    def __init__(self, *, cache_dir: Path, token: str | None = None, **kwargs: Any) -> None:
        super().__init__(cache_dir=cache_dir, **kwargs)
        self.token = token

    def refresh(self, repository: str, *, per_page: int = 30, force: bool = False) -> SourceUpdate:
        if repository.count("/") != 1:
            raise ValueError("repository must be 'owner/name'")
        query = f"{repository}|{per_page}"
        cached, age = self._cached(query)
        if cached and age is not None and age < self.ttl_seconds and not force:
            return self._cache_update(query, cached, age, "cache")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Argus-Flywheel/1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        metadata = (cached or {}).get("response_metadata") or {}
        if metadata.get("etag"):
            headers["If-None-Match"] = str(metadata["etag"])
        url = f"https://api.github.com/repos/{repository}/commits?per_page={per_page}"
        try:
            status, raw, response_headers = self.fetcher(url, headers, self.timeout)
            normalized_headers = {str(k).lower(): str(v) for k, v in response_headers.items()}
            if status == 304 and cached is not None:
                items = self._items_from_cache(cached)
                return self._finish(
                    query, items, cached, status="unchanged",
                    response_metadata={**metadata, "etag": normalized_headers.get("etag", metadata.get("etag"))},
                )
            if status in {403, 429}:
                reset = normalized_headers.get("x-ratelimit-reset")
                raise RuntimeError("GitHub rate limit reached" + (f"; reset={reset}" if reset else ""))
            if status != 200:
                raise RuntimeError(f"GitHub returned HTTP {status}")
            data = json.loads(raw.decode("utf-8"))
            items: list[SourceItem] = []
            for commit in data:
                sha = str(commit.get("sha") or "")
                details = commit.get("commit") or {}
                message = str(details.get("message") or "").splitlines()[0]
                date = ((details.get("author") or {}).get("date"))
                if sha:
                    items.append(SourceItem(sha, message, str(commit.get("html_url") or ""), date))
            return self._finish(
                query, tuple(items), cached,
                response_metadata={
                    "etag": normalized_headers.get("etag"),
                    "rate_remaining": normalized_headers.get("x-ratelimit-remaining"),
                    "rate_reset": normalized_headers.get("x-ratelimit-reset"),
                },
            )
        except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
            return self._error_or_stale(query, cached, age, exc)
