"""Fetch reusable primary-source text into the working project's source cache."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_SOURCE_BYTES = 8 * 1024 * 1024

ENGINEER_SOURCE_HANDOFF = (
    "Web sources: Argus Python `-m argus.tools.web_source URL`. Cite exact paths "
    "in the deliverable; retain other fetched text in `.argus/sources`, not `/tmp`."
)

REVIEWER_SOURCE_HANDOFF = (
    "Compare web claims with cited source text, not HTTP status or summaries. "
    "For a missing path, check this cache once; never hunt downloads in shared "
    "`/tmp`. Return the exact claim and missing URL/path if essential evidence "
    "is absent."
)


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored.append(tag)
        if not self.ignored and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.ignored and tag == self.ignored[-1]:
            self.ignored.pop()
        if not self.ignored and tag in {"p", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def fetch_source(url: str, workdir: Path) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an HTTP(S) source URL without embedded credentials")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Argus source reader)",
                                   "Accept-Encoding": "identity"})
    with urlopen(request, timeout=25) as response:
        body = response.read(MAX_SOURCE_BYTES + 1)
        if len(body) > MAX_SOURCE_BYTES:
            raise ValueError("Source exceeds 8 MiB; fetch the relevant source with a suitable tool")
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        resolved_url = response.geturl()
    if content_type == "application/pdf" or body.startswith(b"%PDF-"):
        from pypdf import PdfReader

        extracted = "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages)
        extraction = "PDF extracted text; layout, images and equations may require direct PDF inspection"
    elif content_type in {"text/html", "application/xhtml+xml"}:
        parser = _PageText()
        parser.feed(body.decode(charset, errors="replace"))
        extracted = "\n".join(filter(None, (re.sub(r"\s+", " ", line).strip()
                                             for line in "".join(parser.parts).splitlines())))
        extraction = "HTML text with scripts, styles and SVG omitted"
    elif content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
        extracted = body.decode(charset, errors="replace")
        extraction = "decoded response text"
    else:
        raise ValueError(f"Unsupported source type: {content_type}; use a suitable fetch tool")
    if not extracted.strip():
        raise ValueError("Source has no extractable text; use a suitable fetch tool")
    metadata = {
        "url": url, "resolved_url": resolved_url,
        "accessed_at": datetime.now(timezone.utc).isoformat(),
        "content_type": content_type, "response_bytes": len(body),
        "response_sha256": hashlib.sha256(body).hexdigest(), "extraction": extraction,
    }
    # Content-specific names preserve the cited snapshot if a URL changes later.
    name = hashlib.sha256((url + metadata["response_sha256"]).encode()).hexdigest()[:24] + ".txt"
    directory = workdir.resolve() / ".argus" / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(json.dumps(metadata, ensure_ascii=False) + "\n\n" + extracted + "\n", encoding="utf-8")
    return {"path": str(target), "url": url, "characters": len(extracted), "excerpt": extracted[:2000]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    try:
        result = fetch_source(args.url, Path.cwd())
    except (OSError, ValueError, LookupError) as exc:
        print(f"Source fetch failed: {str(exc)[:500]}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
