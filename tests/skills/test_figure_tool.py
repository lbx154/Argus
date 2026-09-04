"""Paper-figure vision review: prompt construction and the review CLI."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from argus_skill.verticals.research import figure_tool

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_review_prompt_requires_submission_ready_geometry() -> None:
    no_rubric = figure_tool._review_prompt(original_prompt="a diagram", rubric="")
    assert "connector penetration" in no_rubric
    assert "node-boundary termination" in no_rubric
    assert "non-overlapping, unclipped" in no_rubric
    assert "semantically correct and submission-ready" in no_rubric

    rubric_text = (
        "Output a JSON object with fields: keep_or_regenerate, confirmed_labels, "
        "findings, prohibited_content_present."
    )
    with_rubric = figure_tool._review_prompt(original_prompt="a diagram", rubric=rubric_text)
    assert "connector penetration" in with_rubric
    assert "element overlap" in with_rubric
    assert "unreadable final-size type" in with_rubric


def test_review_prompt_without_rubric_uses_generic_schema() -> None:
    # When no rubric is supplied, callers still receive the generic structured
    # review schema plus the canonical publication-geometry requirements.
    prompt = figure_tool._review_prompt(original_prompt="a diagram", rubric="")
    assert "score_1_to_5" in prompt
    assert "Return JSON with:" in prompt
    assert "communicates" in prompt


def test_review_prompt_has_no_builtin_venue_literals() -> None:
    # No built-in venues: without a researched profile the personas stay
    # generic instead of borrowing a specific conference's name.
    for rubric in ("", "Output JSON with keep_or_regenerate."):
        prompt = figure_tool._review_prompt(original_prompt="a diagram", rubric=rubric)
        for literal in ("EMNLP", "AAAI", "ACL", "NeurIPS"):
            assert literal not in prompt


def test_review_prompt_uses_researched_venue_persona() -> None:
    from argus_skill.verticals.research.venue_profiles import VenueProfile

    profile = VenueProfile(
        key="NEURIPS",
        display_name="NeurIPS 2026",
        body_page_limit=9,
        conclusion_underfill_page=8,
        conclusion_max_page=9,
        references_min_page=10,
        reviewer_persona="NeurIPS",
        figure_style_persona="NeurIPS",
    )
    prompt = figure_tool._review_prompt(
        original_prompt="a diagram", rubric="", venue_profile=profile
    )
    assert "NeurIPS" in prompt


def test_review_prompt_with_rubric_is_rubric_authoritative() -> None:
    # When a real rubric is supplied it becomes authoritative: the prompt must
    # not force the generic score_1_to_5 schema (which would swamp the rubric's
    # requested fields such as confirmed_labels), and it must tell the model to
    # emit every field the rubric requests plus keep_or_regenerate.
    rubric = (
        "Output a JSON object with fields: keep_or_regenerate, confirmed_labels, "
        "findings, prohibited_content_present."
    )
    prompt = figure_tool._review_prompt(original_prompt="a diagram", rubric=rubric)
    assert "AUTHORITATIVE" in prompt
    assert "keep_or_regenerate" in prompt
    assert "score_1_to_5" not in prompt
    # the caller's rubric text is passed through verbatim
    assert rubric in prompt


def test_review_image_threads_rubric_into_authoritative_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End-to-end: a rubric passed to figure_tool.review_image reaches the
    # model request as an authoritative instruction, not buried under the
    # generic schema, via the generic tools.image_api.review_image call.
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> Any:
        captured["body"] = json.loads(req.data.decode("utf-8"))

        class _FakeResponse:
            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"output_text": '{"keep_or_regenerate": "keep"}'}).encode(
                    "utf-8"
                )

        return _FakeResponse()

    monkeypatch.setattr("argus_skill.tools.image_api._urlopen", fake_urlopen)
    from argus_skill.tools.capability_vault import ModelApiGrant, save_model_api_grant

    vault = tmp_path / "vault.json"
    save_model_api_grant(
        ModelApiGrant(
            api_key="dummy-key",
            base_url="https://example.invalid/openai/v1/",
            image_model="gpt-image-2",
            image_review_model="gpt-5.4",
            vault_path=vault,
        )
    )
    env = {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)}

    result = figure_tool.review_image(
        image=image,
        prompt="hierarchy diagram",
        rubric="Output JSON with keep_or_regenerate and confirmed_labels.",
        out=tmp_path / "review.json",
        env=env,
    )
    sent_text = captured["body"]["input"][0]["content"][0]["text"]
    assert "AUTHORITATIVE" in sent_text
    assert "confirmed_labels" in sent_text
    assert "score_1_to_5" not in sent_text
    # the paper wrapper preserves the "rubric" field the domain-neutral
    # tools.image_api.review_image no longer writes itself.
    assert result["rubric"] == "Output JSON with keep_or_regenerate and confirmed_labels."
    sidecar = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert sidecar["rubric"] == "Output JSON with keep_or_regenerate and confirmed_labels."


def test_review_cli_builds_paper_prompt_and_calls_generic_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> Any:
        captured["body"] = json.loads(req.data.decode("utf-8"))

        class _FakeResponse:
            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"output_text": "score_1_to_5: 5"}).encode("utf-8")

        return _FakeResponse()

    monkeypatch.setattr("argus_skill.tools.image_api._urlopen", fake_urlopen)
    from argus_skill.tools.capability_vault import ModelApiGrant, save_model_api_grant

    vault = tmp_path / "vault.json"
    save_model_api_grant(
        ModelApiGrant(
            api_key="dummy-key",
            base_url="https://example.invalid/openai/v1/",
            image_model="gpt-image-2",
            image_review_model="gpt-5.4",
            vault_path=vault,
        )
    )
    monkeypatch.setenv("ARGUS_SKILL_CAPABILITY_VAULT", str(vault))

    rc = figure_tool.main(
        [
            "review",
            "--image",
            str(image),
            "--prompt",
            "hierarchy diagram",
            "--out",
            str(tmp_path / "review.json"),
            "--project-root",
            str(tmp_path),
        ]
    )
    assert rc == 0
    sent_text = captured["body"]["input"][0]["content"][0]["text"]
    # figure_tool's CLI still builds the paper-oriented review prompt, unlike
    # the domain-neutral tools.image_api CLI.
    assert "academic paper figure" in sent_text
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"] == "score_1_to_5: 5"
