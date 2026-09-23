"""Bounded public-web discovery. Search snippets locate sources; they do not verify claims."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

MAX_RESPONSE_BYTES = 1_000_000
CACHE_SECONDS = 6 * 3600


def _search(query: str) -> list[dict[str, str]]:
    request = Request("https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"}),
                      headers={"User-Agent": "Mozilla/5.0 (compatible; Argus source discovery)"})
    with urlopen(request, timeout=12) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Search response is oversized or has an unsupported document declaration")
    tree = ElementTree.fromstring(raw)
    if tree.tag != "rss" or tree.find("channel") is None:
        raise ValueError("Search returned a challenge or non-search document")
    rows = []
    for item in tree.findall("./channel/item")[:8]:
        url = str(item.findtext("link") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            continue
        rows.append({"title": str(item.findtext("title") or "")[:240], "url": url[:2000],
                     "snippet": str(item.findtext("description") or "")[:1200]})
    return rows


def search_web(query: str, workdir: Path, *, refresh: bool = False) -> dict:
    query = " ".join(str(query).split())
    if not query or len(query) > 200:
        raise ValueError("Use a public search phrase of 1–200 characters")
    directory = workdir.resolve() / ".argus" / "sources"
    key = hashlib.sha256(query.encode()).hexdigest()[:24]
    cache = directory / f"search-{key}.json"
    if not refresh:
        try:
            if cache.stat().st_size > MAX_RESPONSE_BYTES:
                raise ValueError("Oversized search cache")
            prior = json.loads(cache.read_text())
            age = time.time() - float(prior["accessed_at"])
            if prior["query"] == query and prior["status"] == "ok" and 0 <= age < CACHE_SECONDS:
                return {**prior, "cached": True, "path": str(cache)}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        rows = _search(query)
        result = {"status": "ok" if rows else "no_results", "query": query, "results": rows,
                  "accessed_at": time.time(), "provider": "bing"}
    except (OSError, ValueError, ElementTree.ParseError):
        # Neither a network failure nor an HTML challenge proves nonexistence.
        return {"status": "unavailable", "query": query, "results": [], "cached": False,
                "message": "Search unavailable; the subject's identity remains unverified."}
    try:
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".search-", dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        return {**result, "cached": False, "message": "Search completed; could not retain the search snapshot."}
    return {**result, "cached": False, "path": str(cache)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    result = search_web(args.query, Path.cwd(), refresh=args.refresh)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
