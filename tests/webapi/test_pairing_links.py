from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from argus.webapi.routes import pairing
from argus.webapi.server import create_app

TOKEN = "test-owner-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


def test_pairing_requires_owner_to_issue_and_explicit_single_use_redemption(tmp_path):
    with TestClient(create_app(global_root=tmp_path, auth_token=TOKEN)) as client:
        assert client.post("/api/pairing-links").status_code == 401
        issued = client.post("/api/pairing-links", headers=AUTH)
        assert issued.status_code == 200 and TOKEN not in issued.text
        assert issued.headers["Cache-Control"] == "no-store"
        path = issued.json()["path"]
        for _ in range(2):
            landing = client.get(path)
            assert landing.status_code == 200 and TOKEN not in landing.text
            assert 'method="post"' in landing.text
            assert landing.headers["Cache-Control"] == "no-store"
            assert landing.headers["Referrer-Policy"] == "no-referrer"
        redeemed = client.post(path, follow_redirects=False)
        assert redeemed.status_code == 303
        assert parse_qs(urlparse(redeemed.headers["location"]).query)["token"] == [TOKEN]
        assert redeemed.headers["Cache-Control"] == "no-store"
        assert client.post(path, follow_redirects=False).status_code == 410
        assert client.get(path).status_code == 410
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/projects", headers=AUTH).status_code == 200


def test_pairing_registry_is_bounded_and_expired_capacity_is_reclaimed(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pairing, "time", SimpleNamespace(monotonic=lambda: now[0]))
    with TestClient(create_app(global_root=tmp_path, auth_token=TOKEN)) as client:
        for _ in range(pairing._MAX_LINKS):
            assert client.post("/api/pairing-links", headers=AUTH).status_code == 200
        assert client.post("/api/pairing-links", headers=AUTH).status_code == 429
        now[0] += pairing._TTL_SECONDS + 1
        assert client.post("/api/pairing-links", headers=AUTH).status_code == 200


def test_local_unauthenticated_server_does_not_issue_links(tmp_path):
    with TestClient(create_app(global_root=tmp_path, auth_token="")) as client:
        assert client.post("/api/pairing-links").status_code == 409
