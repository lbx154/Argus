import asyncio
import json

import httpx
import pytest
from cryptography.fernet import Fernet

from argus_skill.trial.admin import import_cli_login
from argus_skill.trial.copilot import Copilot
from argus_skill.trial.secrets import Vault, write_private
from argus_skill.trial.store import TrialError


@pytest.mark.parametrize("valid", [True, False])
def test_import_selected_cli_account_encrypted_and_preserve_prior_on_failure(tmp_path, monkeypatch, capsys, valid):
    home = tmp_path / "copilot"
    home.mkdir()
    config = {
        "lastLoggedInUser": {"host": "https://github.com", "login": "chosen"},
        "authTokens": {
            "https://github.com:other": {"token": "unselected-secret"},
            "https://github.com:chosen": {"token": "selected-secret"},
        },
    }
    source = home / "config.json"
    source.write_text("// managed\n" + json.dumps(config))
    original = source.read_bytes()
    key = tmp_path / "master.key"
    write_private(key, Fernet.generate_key())
    vault = Vault(key, tmp_path / "vault.enc")
    vault.save("previous-secret")

    async def authorize(self):
        assert self.vault.read() == "selected-secret"
        if not valid:
            raise TrialError(503, "provider_auth_failed", "Provider auth failed")
        return "https://api.individual.githubcopilot.com", {}

    monkeypatch.setattr(Copilot, "verify", authorize)
    if valid:
        asyncio.run(import_cli_login(vault, home))
        assert vault.read() == "selected-secret"
    else:
        with pytest.raises(TrialError):
            asyncio.run(import_cli_login(vault, home))
        assert vault.read() == "previous-secret"
    assert source.read_bytes() == original
    assert b"selected-secret" not in vault.token_path.read_bytes()
    assert "secret" not in capsys.readouterr().out


def test_cli_import_requires_an_explicit_selected_account(tmp_path):
    key = tmp_path / "key"
    write_private(key, Fernet.generate_key())
    with pytest.raises(ValueError, match="selected Copilot CLI account"):
        asyncio.run(import_cli_login(Vault(key, tmp_path / "vault"), tmp_path))


@pytest.mark.parametrize("status,body,expected", [
    (200, {"data": [{"id": "test-model"}]}, None),
    (200, {"data": []}, "provider_protocol_error"),
    (200, [], "provider_protocol_error"),
    (403, {"error": "secret"}, "provider_auth_failed"),
    (302, {}, "provider_auth_failed"),
])
def test_cli_credential_verified_at_fixed_copilot_origin(tmp_path, status, body, expected):
    key = tmp_path / "key"
    write_private(key, Fernet.generate_key())
    vault = Vault(key, tmp_path / "vault")
    vault.save("selected-secret")
    visited = []

    def provider(request):
        visited.append(str(request.url))
        assert request.headers["authorization"] == "Bearer selected-secret"
        assert request.headers["copilot-integration-id"] == "copilot-developer-cli"
        return httpx.Response(status, json=body, headers={"Location": "https://attacker.invalid"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider), follow_redirects=False) as client:
            if expected:
                with pytest.raises(TrialError) as exc:
                    await Copilot(client, vault).verify()
                assert exc.value.code == expected and "secret" not in str(exc.value)
            else:
                await Copilot(client, vault).verify()

    asyncio.run(run())
    assert visited == ["https://api.githubcopilot.com/models"]
