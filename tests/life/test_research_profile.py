from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.life.research_profile import (
    ensure_research_api_environment,
    ensure_shared_model_cache_environment,
    load_research_profile,
    render_research_profile_context,
)
from argus_skill.tools.capability_vault import ModelApiGrant, save_model_api_grant


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
        "ARGUS_SKILL_SHARED_MODEL_CACHE_ROOT",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_DATASETS_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "XDG_CACHE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_research_profile_inactive_by_default() -> None:
    assert load_research_profile({}) is None
    assert render_research_profile_context({}) == ""


def test_emnlp2026_profile_contains_research_guardrails() -> None:
    ctx = render_research_profile_context(
        {"ARGUS_SKILL_RESEARCH_PROFILE": "emnlp2026-tierharness"}
    )

    assert "EMNLP 2026 TierHarness" in ctx
    assert "SLM -> LLM -> HUMAN" in ctx
    assert "/logs/verifier/reward.txt" in ctx
    assert "Do not write or summarize any benchmark number as fact" in ctx
    assert "experiments/<run_id>/manifest.json" in ctx
    assert "human turns after assignment" in ctx
    assert "Granted capability layer" in ctx
    assert "image_model_allowed: gpt-image-2" in ctx
    assert "Shared model/data cache layer" in ctx
    default_cache_root = Path.home() / ".cache"
    assert f"HF_HOME: {default_cache_root / 'huggingface'}" in ctx
    assert f"HUGGINGFACE_HUB_CACHE: {default_cache_root / 'huggingface' / 'hub'}" in ctx
    assert "Permission model: the human has pre-approved these capabilities" in ctx
    assert "profile_sha256" in ctx
    assert "final_submission" in ctx
    assert "validate-full-emnlp --project-root ." in ctx
    assert "Passing `validate-pipeline`" in ctx
    assert "paper_contribution" in ctx
    assert "We propose X. We show X improves Y by Z because W." in ctx
    assert "negative-result paper" in ctx
    assert "scope: bounded" in ctx


def test_research_profile_reports_pregranted_model_api(tmp_path: Path) -> None:
    vault_path = save_model_api_grant(
        ModelApiGrant(
            api_key="secret",
            base_url="https://example.invalid/openai/v1",
            text_models=("gpt-5.4-mini", "gpt-5.4"),
            image_model="gpt-image-2",
            vault_path=tmp_path / "model_api.json",
        )
    )

    ctx = render_research_profile_context(
        {
            "ARGUS_SKILL_RESEARCH_PROFILE": "emnlp2026-tierharness",
            "ARGUS_SKILL_CAPABILITY_VAULT": str(vault_path),
        }
    )

    assert "model_api_available: yes" in ctx
    assert f"model_api_key_source: vault:{vault_path}" in ctx
    assert f"model_api_base_url_source: vault:{vault_path}" in ctx
    assert "text_models_allowed: gpt-5.4-mini,gpt-5.4" in ctx
    assert "image_model_allowed: gpt-image-2" in ctx
    assert "image_tool_generate" in ctx
    assert "secret" not in ctx


def test_research_api_environment_loads_key_without_printing_secret(tmp_path: Path) -> None:
    vault_path = save_model_api_grant(
        ModelApiGrant(
            api_key="secret",
            base_url="https://example.invalid/openai/v1",
            vault_path=tmp_path / "model_api.json",
        )
    )
    env = {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault_path)}

    ensure_research_api_environment(env)

    assert env["OPENAI_API_KEY"] == "secret"
    assert env["OPENAI_BASE_URL"] == "https://example.invalid/openai/v1"
    assert env["HF_HOME"].endswith("/.cache/huggingface")
    assert env["HUGGINGFACE_HUB_CACHE"].endswith("/.cache/huggingface/hub")
    assert env["HF_DATASETS_CACHE"].endswith("/.cache/huggingface/datasets")
    assert env["TRANSFORMERS_CACHE"].endswith("/.cache/huggingface/hub")
    assert env["TORCH_HOME"].endswith("/.cache/torch")
    assert env["XDG_CACHE_HOME"].endswith("/.cache")


def test_shared_model_cache_environment_uses_one_host_root() -> None:
    env = {"ARGUS_SKILL_SHARED_MODEL_CACHE_ROOT": "/tmp/argus-cache"}

    ensure_shared_model_cache_environment(env)

    assert env["XDG_CACHE_HOME"] == "/tmp/argus-cache"
    assert env["HF_HOME"] == "/tmp/argus-cache/huggingface"
    assert env["HUGGINGFACE_HUB_CACHE"] == "/tmp/argus-cache/huggingface/hub"
    assert env["HF_DATASETS_CACHE"] == "/tmp/argus-cache/huggingface/datasets"
    assert env["TRANSFORMERS_CACHE"] == "/tmp/argus-cache/huggingface/hub"
    assert env["TORCH_HOME"] == "/tmp/argus-cache/torch"


def test_shared_model_cache_environment_preserves_operator_overrides() -> None:
    env = {
        "ARGUS_SKILL_SHARED_MODEL_CACHE_ROOT": "/tmp/argus-cache",
        "HF_HOME": "/custom/hf",
    }

    ensure_shared_model_cache_environment(env)

    assert env["HF_HOME"] == "/custom/hf"
    assert env["HUGGINGFACE_HUB_CACHE"] == "/tmp/argus-cache/huggingface/hub"


def test_research_profile_can_be_loaded_from_file(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.md"
    profile_path.write_text("## Custom\n- use paper evidence only\n", encoding="utf-8")

    profile = load_research_profile(
        {
            "ARGUS_SKILL_RESEARCH_PROFILE": "paper-custom",
            "ARGUS_SKILL_RESEARCH_PROFILE_PATH": str(profile_path),
        }
    )

    assert profile is not None
    assert profile.name == "paper-custom"
    assert "paper evidence" in profile.text
