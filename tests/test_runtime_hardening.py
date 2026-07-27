from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.core.secret_guard import known_secret_values, redact_secrets_text
from argus_skill.tools import image_api
from argus_skill.tools.capability_vault import ModelApiRoute


def test_capability_vault_keys_are_known_secrets(tmp_path: Path) -> None:
    vault = tmp_path / "model_api.json"
    key = "azure-secret-value-123456789"
    vault.write_text(
        json.dumps({"capabilities": {"model_api": {"routes": {"image": {"api_key": key}}}}})
    )
    values = known_secret_values({"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)})
    assert key in values
    assert key not in redact_secrets_text(f"api_key={key}", known_values=values)


def test_azure_image_calls_are_metered_and_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "image_usage.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_IMAGE_USAGE_LEDGER", str(ledger))
    monkeypatch.setenv("ARGUS_SKILL_IMAGE_DAILY_CALL_CAP", "2")
    route = ModelApiRoute(
        name="image",
        api_key="secret-key-value",
        base_url="https://example.invalid",
        model="gpt-image-2",
        provider="azure_openai",
        wire_api="images",
    )
    payload = {"prompt": "draw a system", "model": "gpt-image-2"}
    image_api._reserve_image_call(route, payload, attempt_index=0)
    image_api._reserve_image_call(route, payload, attempt_index=1)
    with pytest.raises(image_api.ImageToolError, match="daily image API call cap"):
        image_api._reserve_image_call(route, payload, attempt_index=2)
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 2
    assert all("prompt" not in row and "api_key" not in row for row in rows)


def test_image_generation_model_cannot_be_used_as_review_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = ModelApiRoute(
        name="image_review",
        api_key="secret-key-value",
        base_url="https://example.invalid",
        model="gpt-image-2",
        provider="azure_openai",
        wire_api="responses",
    )
    monkeypatch.setattr(image_api, "_require_route", lambda *_a, **_k: route)
    image = tmp_path / "figure.png"
    image.write_bytes(image_api._PNG_MAGIC + b"fake")
    with pytest.raises(image_api.ImageToolError, match="image-generation model"):
        image_api.review_image(
            image=image,
            review_instruction="Check whether this figure is publication-ready.",
        )
