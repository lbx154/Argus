import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from argus_skill.trial.admin import issue_keys
from argus_skill.trial.gateway import Settings, create_app
from argus_skill.trial.secrets import Vault, write_private
from argus_skill.trial.store import Store


@pytest.fixture
def pool(tmp_path):
    key = tmp_path / "master.key"
    write_private(key, Fernet.generate_key())
    vault = Vault(key, tmp_path / "github-token.enc")
    issue_keys(vault, tmp_path, tmp_path / "operator-keys.json")
    return Settings(tmp_path, key), vault, Store(tmp_path / "usage.sqlite3")


def test_fixed_ten_keys_and_rerun_preserves_usage(pool, tmp_path):
    settings, vault, store = pool
    record = store.reserve("trial-01", 100)
    store.settle(record, 30)
    exported = (tmp_path / "operator-keys.json").read_bytes()
    issue_keys(vault, tmp_path, tmp_path / "operator-keys.json")
    assert (tmp_path / "operator-keys.json").read_bytes() == exported
    assert store.status("trial-01")["tokens_used"] == 30
    keys = json.loads(exported)
    assert len(keys) == len({k["api_key"] for k in keys}) == 10
    assert (tmp_path / "operator-keys.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="10 trial keys"):
        store.issue("eleventh", vault.credential("eleventh"))
    assert all(k["api_key"].encode() not in store.path.read_bytes() for k in keys)


def test_concurrent_issuance_never_exceeds_ten_keys(tmp_path):
    store = Store(tmp_path / "usage.sqlite3")

    def issue(i):
        try:
            store.issue(f"key-{i}", f"secret-{i}")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=20) as workers:
        results = list(workers.map(issue, range(30)))
    assert sum(results) == 10
    assert store.availability()["issued_keys"] == 10


def test_public_claim_and_recovery_are_removed_but_private_keys_work(pool):
    settings, vault, store = pool
    key = vault.credential("trial-01")
    record = store.reserve("trial-01", 100)
    store.settle(record, 30)
    with TestClient(create_app(settings), base_url="https://argusbot.cn") as browser:
        for method, path in (("get", "/trial/session"), ("post", "/trial/claim"),
                             ("post", "/trial/register")):
            response = getattr(browser, method)(path)
            assert response.status_code == 404
            assert key not in response.text and "set-cookie" not in response.headers
        # The same private key shares its existing balance across devices.
        for _ in range(2):
            browser.cookies.clear()
            status = browser.get("/trial/status", headers={"Authorization": "Bearer " + key}).json()
            assert status["token_limit"] == 1_000_000 and status["tokens_used"] == 30
        assert browser.get("/trial/status").status_code == 401


def test_old_machine_credential_is_not_accepted(pool):
    settings, vault, _store = pool
    with TestClient(create_app(settings)) as client:
        old = vault.credential("a" * 64)
        assert client.get("/trial/status", headers={"Authorization": "Bearer " + old}).status_code == 401


def test_only_downloads_are_public_even_if_old_pages_remain_on_disk(pool, tmp_path):
    from dataclasses import replace

    settings, vault, _store = pool
    site = tmp_path / "site"
    for folder in ("trial", "zh/trial", "trial/downloads"):
        path = site / folder
        path.mkdir(parents=True, exist_ok=True)
        (path / "index.html").write_text("<html>Retired claim page</html>")
    (site / "trial/downloads/argus.whl").write_bytes(b"wheel")
    with TestClient(create_app(replace(settings, site_dir=site)), base_url="https://argusbot.cn") as client:
        for url in ("/trial", "/trial/", "/zh/trial", "/zh/trial/", "/trial/index.html",
                    "/trial/operator-keys.json", "/trial/session", "/trial/downloads/"):
            assert client.get(url).status_code == 404
        assert client.post("/trial/claim", json={}).status_code == 404
        response = client.get("/trial/downloads/argus.whl")
        assert response.status_code == 200 and response.content == b"wheel"
        assert response.headers["cache-control"] == "no-store"
        auth = {"Authorization": "Bearer " + vault.credential("trial-01")}
        assert client.get("/trial/status", headers=auth).status_code == 200
