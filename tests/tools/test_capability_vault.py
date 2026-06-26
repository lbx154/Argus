from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from argus_skill.tools.capability_vault import (
    ModelApiGrant,
    ModelApiRoute,
    bootstrap_model_api_vault,
    load_model_api_grant,
    load_model_api_route,
    save_model_api_grant,
    save_model_api_routes,
    status_payload,
)


@pytest.fixture(autouse=True)
def _clear_model_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ARGUS_SKILL_CAPABILITY_VAULT",
        "ARGUS_SKILL_MODEL_API_AUTH_JSON",
        "ARGUS_SKILL_CODEX_CONFIG",
        "ARGUS_SKILL_MODEL_API_BASE_URL",
        "ARGUS_SKILL_TEXT_MODELS",
        "ARGUS_SKILL_IMAGE_MODEL",
        "ARGUS_SKILL_IMAGE_REVIEW_MODEL",
        "ARGUS_SKILL_ENGINEER_API_KEY",
        "ARGUS_SKILL_ENGINEER_BASE_URL",
        "ARGUS_SKILL_ENGINEER_MODEL",
        "ARGUS_SKILL_REVIEWER_API_KEY",
        "ARGUS_SKILL_REVIEWER_BASE_URL",
        "ARGUS_SKILL_REVIEWER_MODEL",
        "ARGUS_SKILL_SCIENTIST_API_KEY",
        "ARGUS_SKILL_SCIENTIST_BASE_URL",
        "ARGUS_SKILL_IMAGE_API_KEY",
        "ARGUS_SKILL_IMAGE_BASE_URL",
        "ARGUS_SKILL_IMAGE_WIRE_API",
        "ARGUS_SKILL_IMAGE_REVIEW_API_KEY",
        "ARGUS_SKILL_IMAGE_REVIEW_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_save_and_load_model_api_grant_uses_private_vault(tmp_path: Path) -> None:
    vault = tmp_path / "caps" / "model_api.json"
    grant = ModelApiGrant(
        api_key="dummy-key",
        base_url="https://example.invalid/openai/v1/",
        text_models=("gpt-5.4-mini", "gpt-5.4"),
        image_model="gpt-image-2",
        image_review_model="gpt-5.4",
        vault_path=vault,
    )

    saved = save_model_api_grant(grant)
    loaded = load_model_api_grant({"ARGUS_SKILL_CAPABILITY_VAULT": str(saved)})

    assert loaded is not None
    assert loaded.api_key == "dummy-key"
    assert loaded.base_url == "https://example.invalid/openai/v1/"
    assert loaded.image_model == "gpt-image-2"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_load_model_api_grant_honors_codex_model_provider(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    auth_path = codex_dir / "auth.json"
    auth_path.write_text(json.dumps({"OPENAI_API_KEY": "dummy-key"}), encoding="utf-8")
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        """
model_provider = "azure_proxy"

[model_providers.codex]
base_url = "https://wrong.invalid/v1/"
wire_api = "responses"

[model_providers.azure_proxy]
base_url = "https://right.invalid/openai/v1/"
wire_api = "responses"
""".strip(),
        encoding="utf-8",
    )

    grant = load_model_api_grant(
        {
            "ARGUS_SKILL_CAPABILITY_VAULT": str(tmp_path / "missing-vault.json"),
            "ARGUS_SKILL_MODEL_API_AUTH_JSON": str(auth_path),
            "ARGUS_SKILL_CODEX_CONFIG": str(config_path),
        }
    )

    assert grant is not None
    assert grant.api_key == "dummy-key"
    assert grant.base_url == "https://right.invalid/openai/v1/"
    assert grant.provider == "azure_proxy"
    assert grant.key_source == f"file:{auth_path}"


def test_runtime_defaults_to_vault_over_codex_sources(tmp_path: Path) -> None:
    vault = tmp_path / "model_api.json"
    save_model_api_grant(
        ModelApiGrant(
            api_key="vault-key",
            base_url="https://vault.invalid/openai/v1/",
            provider="vault-provider",
            vault_path=vault,
        )
    )
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"OPENAI_API_KEY": "codex-key"}), encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[model_providers.codex]
base_url = "https://codex.invalid/openai/v1/"
wire_api = "responses"
""".strip(),
        encoding="utf-8",
    )

    grant = load_model_api_grant(
        {
            "ARGUS_SKILL_CAPABILITY_VAULT": str(vault),
            "ARGUS_SKILL_MODEL_API_AUTH_JSON": str(auth_path),
            "ARGUS_SKILL_CODEX_CONFIG": str(config_path),
        }
    )

    assert grant is not None
    assert grant.api_key == "vault-key"
    assert grant.base_url == "https://vault.invalid/openai/v1/"
    assert grant.provider == "vault-provider"
    assert grant.key_source.startswith(f"vault:{vault}")


def test_bootstrap_imports_codex_sources_into_vault(tmp_path: Path) -> None:
    vault = tmp_path / "caps" / "model_api.json"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"OPENAI_API_KEY": "codex-key"}), encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[model_providers.codex]
base_url = "https://codex.invalid/openai/v1/"
wire_api = "responses"
""".strip(),
        encoding="utf-8",
    )

    saved = bootstrap_model_api_vault(
        {
            "ARGUS_SKILL_CAPABILITY_VAULT": str(vault),
            "ARGUS_SKILL_MODEL_API_AUTH_JSON": str(auth_path),
            "ARGUS_SKILL_CODEX_CONFIG": str(config_path),
        }
    )
    grant = load_model_api_grant({"ARGUS_SKILL_CAPABILITY_VAULT": str(saved)})

    assert saved == vault
    assert grant is not None
    assert grant.api_key == "codex-key"
    assert grant.base_url == "https://codex.invalid/openai/v1/"


def test_status_payload_is_secret_free_and_includes_provider_metadata(tmp_path: Path) -> None:
    vault = tmp_path / "model_api.json"
    save_model_api_grant(
        ModelApiGrant(
            api_key="vault-secret",
            base_url="https://vault.invalid/openai/v1/",
            provider="codex",
            wire_api="responses",
            vault_path=vault,
        )
    )

    status = status_payload({"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)})
    rendered = json.dumps(status, sort_keys=True)

    assert "vault-secret" not in rendered
    assert status["model_api_available"] is True
    assert status["provider"] == "codex"
    assert status["wire_api"] == "responses"
    assert status["base_url"] == "https://vault.invalid/openai/v1/"


def test_v2_routes_can_use_distinct_endpoint_key_and_model(tmp_path: Path) -> None:
    vault = tmp_path / "model_api.json"
    save_model_api_routes(
        [
            ModelApiRoute(
                name="engineer",
                api_key="engineer-key",
                base_url="https://engineer.invalid/v1/",
                model="gpt-5.4-mini",
                provider="engineer-provider",
            ),
            ModelApiRoute(
                name="image",
                api_key="image-key",
                base_url="https://image.invalid/openai/v1/",
                model="gpt-image-2",
                provider="image-provider",
                wire_api="images",
            ),
        ],
        vault,
    )

    engineer = load_model_api_route("engineer", {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)})
    image = load_model_api_route("image", {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)})
    reviewer = load_model_api_route("reviewer", {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)})

    assert engineer is not None
    assert engineer.api_key == "engineer-key"
    assert engineer.base_url == "https://engineer.invalid/v1/"
    assert image is not None
    assert image.api_key == "image-key"
    assert image.base_url == "https://image.invalid/openai/v1/"
    assert image.wire_api == "images"
    assert reviewer is None


def test_bootstrap_honors_route_specific_environment(tmp_path: Path) -> None:
    vault = tmp_path / "model_api.json"
    saved = bootstrap_model_api_vault(
        {
            "ARGUS_SKILL_CAPABILITY_VAULT": str(vault),
            "ARGUS_SKILL_ENGINEER_API_KEY": "engineer-key",
            "ARGUS_SKILL_ENGINEER_BASE_URL": "https://engineer.invalid/v1/",
            "ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.4-mini",
            "ARGUS_SKILL_IMAGE_API_KEY": "image-key",
            "ARGUS_SKILL_IMAGE_BASE_URL": "https://image.invalid/openai/v1/",
            "ARGUS_SKILL_IMAGE_MODEL": "gpt-image-2",
        }
    )

    engineer = load_model_api_route("engineer", {"ARGUS_SKILL_CAPABILITY_VAULT": str(saved)})
    image = load_model_api_route("image", {"ARGUS_SKILL_CAPABILITY_VAULT": str(saved)})

    assert engineer is not None
    assert engineer.api_key == "engineer-key"
    assert engineer.base_url == "https://engineer.invalid/v1/"
    assert image is not None
    assert image.api_key == "image-key"
    assert image.base_url == "https://image.invalid/openai/v1/"
