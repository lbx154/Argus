"""Paper-figure vision review: prompt construction and the review CLI."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from argus.verticals.research import figure_tool

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_review_prompt_has_no_builtin_venue_literals() -> None:
    # No built-in venues: without a researched profile the personas stay
    # generic instead of borrowing a specific conference's name.
    for rubric in ("", "Output JSON with keep_or_regenerate."):
        prompt = figure_tool._review_prompt(original_prompt="a diagram", rubric=rubric)
        for literal in ("EMNLP", "AAAI", "ACL", "NeurIPS"):
            assert literal not in prompt


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

    monkeypatch.setattr("argus.tools.image_api._urlopen", fake_urlopen)
    from argus.tools.capability_vault import ModelApiGrant, save_model_api_grant

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
