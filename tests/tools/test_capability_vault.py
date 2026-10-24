from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.tools.capability_vault import (
    ModelApiRoute,
    bootstrap_model_api_vault,
    format_api_context,
    load_model_api_grant,
    load_model_api_route,
    save_model_api_routes,
)


@pytest.fixture(autouse=True)
def _clear_model_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ARGUS_SKILL_CAPABILITY_VAULT",
        "ARGUS_SKILL_MODEL_API_AUTH_JSON",
        "ARGUS_SKILL_CODEX_CONFIG",
        "CODEX_HOME",
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


def test_api_context_exposes_capabilities_without_vault_access_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {
        "text": ModelApiRoute(
            name="text",
            api_key="vault-secret",
            base_url="https://text.invalid/v1/",
            model="gpt-test",
        ),
        "image": None,
        "image_review": None,
    }
    monkeypatch.setattr(
        "argus.tools.capability_vault.load_model_api_route",
        lambda name: routes[name],
    )

    rendered = format_api_context()

    assert "gpt-test" in rendered
    assert "load_model_api_route" in rendered
    assert "vault-secret" not in rendered
    assert "model_api.json" not in rendered
    assert "json.load" not in rendered
    assert "Never open the vault directly" in rendered


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
