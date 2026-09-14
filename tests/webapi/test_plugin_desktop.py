"""The native picker is a first-party overlay, never a rewritten wheel."""
from __future__ import annotations

import asyncio

from argus_skill.core.plugin_manager import catalog
from argus_skill.webapi import plugin_desktop as adapter


def exchange(parts, *, content_type=b"text/html; charset=utf-8", encoding=None):
    result = []

    async def run():
        async def send(message):
            result.append(message)
        wrapped = adapter.html_adapter(send)
        headers = [(b"content-type", content_type), (b"content-length", b"7"), (b"etag", b"old")]
        if encoding:
            headers.append((b"content-encoding", encoding))
        await wrapped({"type": "http.response.start", "status": 200, "headers": headers})
        for index, part in enumerate(parts):
            await wrapped({"type": "http.response.body", "body": part, "more_body": index < len(parts) - 1})
    asyncio.run(run())
    return result


def test_adapter_is_pinned_to_the_known_public_control_contract():
    spec = catalog()["crystalpilot"]
    assert adapter.supported(spec)
    assert not adapter.supported({**spec, "version": "0.5.0"})
    assert not adapter.supported({**spec, "artifact": {"sha256": "different"}})
    assert not adapter.supported({**spec, "host_api": 2})


def test_streamed_html_gets_same_origin_script_and_correct_length():
    result = exchange([b"<!doctype html><head><title>Fixture</title></he", b"ad><body>unchanged</body>"])
    body = result[1]["body"]
    headers = dict(result[0]["headers"])
    assert body.count(adapter.ADAPTER_NAME.encode()) == 1
    assert body.endswith(b"<body>unchanged</body>")
    assert int(headers[b"content-length"]) == len(body)
    assert headers[b"cache-control"] == b"no-store"
    assert b"etag" not in headers


def test_non_html_encoded_and_large_streams_are_not_rewritten():
    for kind, encoding in [(b"application/javascript", None), (b"text/html", b"gzip")]:
        result = exchange([b"<head></head>"], content_type=kind, encoding=encoding)
        assert result[1]["body"] == b"<head></head>"
        assert dict(result[0]["headers"])[b"etag"] == b"old"
    data = b"<head>" + b"a" * (adapter._MAX_HTML + 1) + b"</head>"
    result = exchange([data[:100], data[100:], b"last"])
    assert b"".join(row.get("body", b"") for row in result) == data + b"last"
    assert adapter.ADAPTER_NAME.encode() not in result[1]["body"]


def test_browser_fallback_and_no_framework_or_upload_hooks():
    script = adapter.script_bytes().decode()
    assert 'button[data-testid="browse-folder"]' in script
    assert "event.isTrusted" in script
    assert "event.source !== parent" in script
    assert "nativeOrigins.has(event.origin)" in script
    assert "_valueTracker" not in script
    assert "fetch(" not in script
    assert "input.dispatchEvent" in script


def test_surface_serves_adapter_without_altering_plugin_package(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.testclient import TestClient

    from argus_skill.core import plugin_manager as pm
    from argus_skill.webapi.server import create_app

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(pm, "compatibility", lambda *_: {"supported": True})
    installed = {"version": "0.4.0", "sha256": pm.catalog()["crystalpilot"]["artifact"]["sha256"]}
    monkeypatch.setattr(pm, "state_entry", lambda *a: installed)

    class Plugin:
        def mount(self, host: FastAPI, ctx):
            @host.get("/plugins/crystalpilot/")
            def home():
                return HTMLResponse("<html><head></head><body>synthetic plugin</body></html>")

            @host.get("/api/plugins/crystalpilot/report")
            def report():
                return HTMLResponse("<html><head></head><body>scientific report</body></html>")

    plugin = Plugin()
    monkeypatch.setattr(pm, "load_plugin", lambda *a, **kw: plugin)
    client = TestClient(create_app(global_root=tmp_path, auth_token="synthetic"))
    html = client.get("/plugins/crystalpilot/")
    assert html.status_code == 200 and adapter.ADAPTER_NAME in html.text
    script = client.get("/plugins/crystalpilot/" + adapter.ADAPTER_NAME)
    assert script.status_code == 200
    assert script.content == adapter.script_bytes()
    assert script.headers["x-content-type-options"] == "nosniff"
    assert client.post("/api/plugins/crystalpilot/manage/update").status_code == 401
    assert adapter.ADAPTER_NAME not in client.get("/api/plugins/crystalpilot/report").text
    installed["version"] = "0.3.0"
    assert adapter.ADAPTER_NAME not in client.get("/plugins/crystalpilot/").text
