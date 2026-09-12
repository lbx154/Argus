import base64
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.core.transcript import append_turn
from argus_skill.webapi.artifact_preview import HtmlPackage
from argus_skill.webapi.server import create_app


def site(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "styles").mkdir()
    (root / "styles" / "main.css").write_text("body{color:red;background:url(../image.svg)}")
    (root / "image.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (root / "app.js").write_text("document.body.dataset.ready='true';")
    entry = root / "index.html"
    entry.write_text(
        '<!doctype html><link rel="stylesheet" href="styles/main.css"><script src="app.js"></script><h1>Ready</h1>'
    )
    return entry


def test_packages_only_referenced_assets_and_resolves_nested_css(tmp_path):
    entry = site(tmp_path)
    (tmp_path / "unreferenced.js").write_text("private")
    package = HtmlPackage(entry)
    result = package.build()
    assert result["warnings"] == []
    assert set(package.files) == {"index.html", "styles/main.css", "image.svg", "app.js"}
    # The page's own stylesheet is inlined as a <style> block a strict host
    # style policy accepts, not a data: URL it would refuse.
    assert "data:text/css" not in result["html"]
    css = re.search(r"<style>(.*?)</style>", result["html"], re.S)[1]
    assert "data:image/svg+xml;base64," in css
    assert "data:text/javascript;base64," in result["html"]
    assert "<h1>Ready</h1>" in result["html"]


def test_confines_assets_and_rejects_private_files_even_through_symlinks(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (tmp_path / "outside.js").write_text("OUTSIDE_SECRET")
    (root / "secret.js").write_text("INSIDE_SECRET")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "app.js").write_text("HIDDEN_SECRET")
    (root / "linked.js").symlink_to(tmp_path / "outside.js")
    (root / "innocent.js").symlink_to(root / ".hidden" / "app.js")
    entry = root / "index.html"
    entry.write_text(
        "".join(
            f'<script src="{path}"></script>'
            for path in [
                "../outside.js",
                "%2e%2e/outside.js",
                "linked.js",
                "innocent.js",
                "secret.js",
                ".hidden/app.js",
            ]
        )
    )
    package = HtmlPackage(entry)
    result = package.build()
    assert set(package.files) == {"index.html"}
    assert len(result["warnings"]) == 6
    assert "SECRET" not in result["html"]


def test_module_imports_and_css_cycles_are_bounded(tmp_path):
    (tmp_path / "a.js").write_text("import {n} from './b.js'; document.body.textContent=n;")
    (tmp_path / "b.js").write_text("export const n=42;")
    (tmp_path / "a.css").write_text('@import "a.css";')
    entry = tmp_path / "index.html"
    entry.write_text(
        '<link rel="stylesheet" href="a.css"><script type="module" src="a.js"></script>'
    )
    result = HtmlPackage(entry).build()
    assert result["file_count"] == 4
    assert len(result["warnings"]) == 1
    assert "data:text/javascript;base64," in result["html"]


def test_large_assets_are_not_read_into_the_preview(tmp_path, monkeypatch):
    import argus_skill.webapi.artifact_preview as module

    monkeypatch.setattr(module, "MAX_BYTES", 300)
    entry = tmp_path / "index.html"
    entry.write_text('<script src="huge.js"></script>')
    (tmp_path / "huge.js").write_text("x" * 1000)
    package = HtmlPackage(entry)
    result = package.build()
    assert result["file_count"] == 1
    assert result["warnings"]


def test_preview_and_bundle_require_auth_and_an_allowlisted_html_entry(tmp_path):
    root = tmp_path / "website"
    entry = site(root)
    state = tmp_path / "state"
    sid = "s-delivery-preview"
    life = state / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(state, SessionMeta(id=sid, cwd=str(life), workdir=str(root)))
    append_turn(
        life,
        "argus",
        "Ready",
        metadata={
            "delivery": {
                "schema_version": 1,
                "delivery_id": "ready",
                "targets": [
                    {
                        "path": entry.name,
                        "label": "Website",
                        "source": "delivery",
                        "why": "Reviewed",
                    }
                ],
            }
        },
    )
    client = TestClient(create_app(global_root=state, auth_token="test-token"))
    route = f"/api/projects/{sid}/artifact"
    headers = {"Authorization": "Bearer test-token"}
    for suffix in ["preview", "bundle"]:
        assert client.get(f"{route}/{suffix}", params={"path": "index.html"}).status_code == 401
        for path in ["app.js", "../website/index.html", "missing.html"]:
            assert (
                client.get(f"{route}/{suffix}", params={"path": path}, headers=headers).status_code
                == 404
            )
    preview = client.get(f"{route}/preview", params={"path": "index.html"}, headers=headers)
    assert preview.status_code == 200
    assert preview.json()["file_count"] == 4
    assert preview.json()["served_page"] is True
    assert preview.headers["cache-control"] == "private, no-store"
    bundle = client.get(f"{route}/bundle", params={"path": "index.html"}, headers=headers)
    assert bundle.status_code == 200
    with ZipFile(BytesIO(bundle.content)) as archive:
        assert set(archive.namelist()) == {"index.html", "app.js", "styles/main.css", "image.svg"}
        assert archive.read("index.html") == entry.read_bytes()
    # The existing raw endpoint must continue serving HTML as inert text.
    raw = client.get(f"{route}/raw", params={"path": "index.html"}, headers=headers)
    assert raw.headers["content-type"].startswith("text/plain")
    assert raw.headers["x-content-type-options"] == "nosniff"

    # The served preview page carries a self-sandboxing policy so the site's own
    # inline styles and scripts run, reachable by header or, for an iframe that
    # cannot send one, the same token in the query.
    page_route = f"{route}/preview/page"
    assert client.get(page_route, params={"path": "index.html"}).status_code == 401
    assert (
        client.get(page_route, params={"path": "index.html", "token": "wrong"}).status_code == 401
    )
    for auth in ({"headers": headers}, {"params_token": "test-token"}):
        params = {"path": "index.html"}
        if "params_token" in auth:
            params["token"] = auth["params_token"]
        page = client.get(page_route, params=params, headers=auth.get("headers"))
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        csp = page.headers["content-security-policy"]
        assert csp.startswith("sandbox ")
        assert "script-src 'unsafe-inline'" in csp and "connect-src 'none'" in csp
        assert page.headers["x-content-type-options"] == "nosniff"
        assert "<style>" in page.text and "data:text/css" not in page.text
    # A page path that is not an allowlisted HTML entry is refused.
    assert (
        client.get(page_route, params={"path": "app.js"}, headers=headers).status_code == 404
    )


def test_inline_svg_shapes_remain_siblings_after_packaging(tmp_path):
    from xml.etree import ElementTree
    entry = tmp_path / "index.html"
    entry.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><filter id="glow"><feGaussianBlur stdDeviation="2" /></filter></defs><rect width="100" height="100" /><g id="roads"><path d="M 0 0 L 100 100" /></g><g id="nodes"><circle cx="50" cy="50" r="5" /></g></svg>')
    preview = HtmlPackage(entry).build()["html"]
    svg = ElementTree.fromstring(preview)
    ns = {"s": "http://www.w3.org/2000/svg"}
    assert len(svg.findall("s:g", ns)) == 2
    assert svg.find("s:g[@id='roads']/s:path", ns) is not None
    assert svg.find("s:g[@id='nodes']/s:circle", ns) is not None
