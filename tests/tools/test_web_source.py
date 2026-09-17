from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from argus.tools import web_source


@pytest.fixture
def source_server():
    content = b"<h1>Primary result</h1><p>Accuracy is <b>42%</b> on test A.</p><script>secret_noise()</script>"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/paper")
                self.end_headers()
                return
            if self.path == "/missing":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", content
    server.shutdown()
    server.server_close()
    thread.join()


def test_fetched_text_and_provenance_are_available_at_the_returned_project_path(tmp_path, source_server):
    base, body = source_server
    result = web_source.fetch_source(base + "/redirect", tmp_path)
    paths = list((tmp_path / ".argus/sources").iterdir())
    assert len(paths) == 1 and str(paths[0]) == result["path"]
    header, text = paths[0].read_text().split("\n\n", 1)
    metadata = json.loads(header)
    assert metadata["url"] == base + "/redirect"
    assert metadata["resolved_url"] == base + "/paper"
    assert metadata["accessed_at"] and metadata["response_bytes"] == len(body)
    assert metadata["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert "Accuracy is 42% on test A." in text
    assert "secret_noise" not in text
    assert result["excerpt"] in text and len(result["excerpt"]) <= 2000


def test_failed_or_oversized_fetch_does_not_create_source_evidence(tmp_path, source_server, monkeypatch):
    base, _ = source_server
    with pytest.raises(OSError):
        web_source.fetch_source(base + "/missing", tmp_path)
    monkeypatch.setattr(web_source, "MAX_SOURCE_BYTES", 16)
    with pytest.raises(ValueError, match="exceeds"):
        web_source.fetch_source(base + "/paper", tmp_path)
    assert not (tmp_path / ".argus/sources").exists()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:password@example.com", "ftp://example.com"])
def test_non_web_or_credential_urls_are_rejected_before_fetch(tmp_path, url):
    with pytest.raises(ValueError, match="HTTP"):
        web_source.fetch_source(url, tmp_path)


def test_changed_content_keeps_the_previous_cited_snapshot(tmp_path, monkeypatch):
    from email.message import Message
    from io import BytesIO

    headers = Message()
    headers["Content-Type"] = "text/plain"
    responses = []
    for body in (b"First finding", b"Revised finding"):
        response = BytesIO(body)
        response.headers = headers
        response.geturl = lambda: "https://example.com/paper"
        responses.append(response)
    monkeypatch.setattr(web_source, "urlopen", lambda *args, **kwargs: responses.pop(0))
    first = web_source.fetch_source("https://example.com/paper", tmp_path)
    second = web_source.fetch_source("https://example.com/paper", tmp_path)
    assert first["path"] != second["path"]
    assert len(list((tmp_path / ".argus/sources").iterdir())) == 2
