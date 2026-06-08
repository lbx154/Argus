from __future__ import annotations

import base64
import hashlib
import io
import json
from email.message import Message
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError

import pytest

from argus_skill.tools import image_tool
from argus_skill.tools.capability_vault import (
    ModelApiGrant,
    ModelApiRoute,
    save_model_api_grant,
    save_model_api_routes,
)
from argus_skill.tools.project_templates.code import generate_image_2 as image2_template

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


def _env_with_vault(tmp_path: Path) -> dict[str, str]:
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
    return {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)}


def test_render_paper_figure_prompt_uses_figure_studio_template() -> None:
    prompt = image_tool.render_paper_figure_prompt(figure_title="SkillCycle")

    assert "Prompt template: argus-image2-paper-prompt-v1" in prompt
    assert "Prompt source: paper-framework-figure-studio-pro-v3.1.4a" in prompt
    assert "SkillCycle" in prompt
    assert "General style:" in prompt
    assert "Pinned content that must appear exactly:" in prompt
    assert "Layout variant:" in prompt
    assert "Negative prompt / Avoid:" in prompt


def test_render_paper_figure_prompt_with_free_content() -> None:
    content = (
        '- Title: "PairScorer Pipeline"\n'
        '- Show: "Context+Candidate Pairs" -> "BoW Encoder" -> "Candidate Ranking" -> "Auxiliary Op Head" -> "Joint Prediction".\n'
        '- Operation types: "CLICK", "SELECT", "TYPE", "HOVER".\n'
        '- Baselines: "keyword overlap", "random", "no_skill".'
    )
    prompt = image_tool.render_paper_figure_prompt(
        figure_title="PairScorer Pipeline",
        content=content,
        layout_variant="17 nested containers: big containers for Offline and Online; nested subcards inside.",
    )
    assert "PairScorer Pipeline" in prompt
    assert "BoW Encoder" in prompt
    assert "Auxiliary Op Head" in prompt
    assert "keyword overlap" in prompt
    assert "nested containers" in prompt
    # Should NOT contain legacy generic labels
    assert "Literature-grounded inputs" not in prompt
    assert "Reusable agent skill loop" not in prompt
    # Should contain research.md features
    assert "Aspect ratio:" in prompt
    assert "1536x1024 landscape" in prompt
    assert "干净" in prompt  # Chinese style intent


def test_render_paper_figure_prompt_legacy_compat() -> None:
    prompt = image_tool.render_paper_figure_prompt(
        figure_title="TestMethod",
        input_label="Raw Data",
        mechanism_label="Encoder",
        output_label="Predictions",
        benefit_label="Higher F1",
    )
    assert '"Raw Data"' in prompt
    assert '"Encoder"' in prompt


def test_prompt_sha256_matches_raw_file_bytes(tmp_path: Path) -> None:
    """Prompt SHA-256 in manifest/sidecar must match raw file bytes on disk.

    This is the bug that caused the infinite regeneration loop: image_tool
    used stripped-text hash but the validator used raw-file-bytes hash,
    so they never matched.
    """
    import hashlib

    result = image_tool.write_paper_figure_prompt(
        tmp_path / "test.prompt.txt",
        figure_title="HashTest",
        content='- Title: "HashTest"\n- Show: "A" -> "B".',
        force=True,
    )
    # The SHA in the result must match raw file bytes
    raw_bytes = (tmp_path / "test.prompt.txt").read_bytes()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    assert result["prompt_sha256"] == raw_hash, (
        f"prompt_sha256 must match raw file bytes! "
        f"got {result['prompt_sha256'][:16]}... "
        f"expected {raw_hash[:16]}..."
    )


def test_render_paper_figure_prompt_custom_aspect_ratio() -> None:
    prompt = image_tool.render_paper_figure_prompt(
        figure_title="Tall Diagram",
        content='- Title: "Tall Diagram"\n- Show: "A" -> "B" -> "C".',
        aspect_ratio="1024x1536 portrait",
    )
    assert "1024x1536 portrait" in prompt
    assert "1536x1024" not in prompt.split("Aspect ratio:")[1].split("\n")[0]


def test_sync_paper_metadata_writes_manifest_and_provenance(tmp_path: Path) -> None:
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    prompt_path = figures / "method.prompt.txt"
    prompt = image_tool.render_paper_figure_prompt(figure_title="SkillCycle").strip()
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    output_path = figures / "method.png"
    output_path.write_bytes(_PNG_BYTES)
    info = image_tool.inspect_image(output_path)
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    sidecar_path = output_path.with_suffix(output_path.suffix + ".json")
    sidecar_path.write_text(
        json.dumps(
            {
                "model": "gpt-image-2",
                "created_at_unix": 1700000000,
                "prompt": prompt,
                "prompt_path": "paper/figures/method.prompt.txt",
                "prompt_sha256": prompt_sha,
                "output_path": "paper/figures/method.png",
                "output_sha256": info["sha256"],
                "requested_size": "1536x1024",
                "image": info,
                "api": {"endpoint": "/images/generations"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    review_path = output_path.with_suffix(output_path.suffix + ".review.json")
    review_path.write_text(
        json.dumps(
            {
                "image": info,
                "model": "gpt-5.4",
                "endpoint": "/responses",
                "review": "score_1_to_5: 5\nkeep_or_regenerate: keep",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entry = image_tool.sync_paper_metadata(
        project_root=tmp_path,
        image=Path("paper/figures/method.png"),
        prompt_file=Path("paper/figures/method.prompt.txt"),
        figure_id="method-overview",
        figure_type="method",
    )

    provenance = json.loads(
        output_path.with_suffix(output_path.suffix + ".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads((figures / "IMAGE2_FIGURES.json").read_text(encoding="utf-8"))
    manifest_entry = manifest["figures"][0]
    assert entry["prompt_template_id"] == "argus-image2-paper-prompt-v1"
    assert entry["figure_studio_source"] == "paper-framework-figure-studio-pro-v3.1.4a"
    assert manifest_entry["output_sha256"] == info["sha256"]
    assert provenance["output_sha256"] == info["sha256"]
    assert (figures / "method.png.inspect.json").exists()


def test_sync_paper_metadata_accepts_raw_file_prompt_hash_with_stripped_sidecar_prompt(
    tmp_path: Path,
) -> None:
    figures = tmp_path / "paper" / "figures"
    figures.mkdir(parents=True)
    prompt_path = figures / "method.prompt.txt"
    prompt = image_tool.render_paper_figure_prompt(figure_title="SkillCycle").strip()
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    output_path = figures / "method.png"
    output_path.write_bytes(_PNG_BYTES)
    info = image_tool.inspect_image(output_path)
    prompt_sha = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    output_path.with_suffix(output_path.suffix + ".json").write_text(
        json.dumps(
            {
                "model": "gpt-image-2",
                "created_at_unix": 1700000000,
                "prompt": prompt,
                "prompt_path": "paper/figures/method.prompt.txt",
                "prompt_sha256": prompt_sha,
                "output_path": "paper/figures/method.png",
                "output_sha256": info["sha256"],
                "requested_size": "1536x1024",
                "image": info,
                "api": {"endpoint": "/images/generations"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_path.with_suffix(output_path.suffix + ".review.json").write_text(
        json.dumps(
            {
                "image": info,
                "model": "gpt-5.4",
                "endpoint": "/responses",
                "review": "score_1_to_5: 5\nkeep_or_regenerate: keep",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entry = image_tool.sync_paper_metadata(
        project_root=tmp_path,
        image=Path("paper/figures/method.png"),
        prompt_file=Path("paper/figures/method.prompt.txt"),
        figure_id="method-overview",
        figure_type="method",
    )

    assert entry["prompt_sha256"] == prompt_sha


def test_generate_image_writes_artifact_and_secret_free_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        seen.append(req.full_url)
        assert req.get_header("Authorization") == "Bearer dummy-key"
        payload = json.loads(req.data.decode("utf-8"))
        assert "size" not in payload
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_tool, "_urlopen", fake_urlopen)
    out = tmp_path / "figure.png"

    meta = image_tool.generate_image(
        prompt="clean academic hierarchy diagram",
        out=out,
        force=False,
        env=_env_with_vault(tmp_path),
    )

    assert seen == ["https://example.invalid/openai/v1/images/generations"]
    assert out.read_bytes() == _PNG_BYTES
    assert meta["image"]["mime"] == "image/png"
    assert meta["requested_size"] == "auto"
    sidecar_text = (tmp_path / "figure.png.json").read_text(encoding="utf-8")
    assert "dummy-key" not in sidecar_text
    assert "clean academic hierarchy diagram" in sidecar_text


def test_generate_image_retries_transient_overload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        calls.append(req.full_url)
        if len(calls) == 1:
            headers = Message()
            headers["Retry-After"] = "0"
            raise HTTPError(
                req.full_url,
                429,
                "too many requests",
                hdrs=headers,
                fp=io.BytesIO(b'{"error":{"code":"EngineOverloaded"}}'),
            )
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_tool, "_urlopen", fake_urlopen)
    monkeypatch.setattr(image_tool.time, "sleep", lambda seconds: sleeps.append(seconds))

    meta = image_tool.generate_image(
        prompt="clean academic hierarchy diagram",
        out=tmp_path / "figure.png",
        env=_env_with_vault(tmp_path),
    )

    assert calls == [
        "https://example.invalid/openai/v1/images/generations",
        "https://example.invalid/openai/v1/images/generations",
    ]
    assert sleeps == [1.0]
    assert meta["image"]["mime"] == "image/png"


def test_generate_image_keeps_explicit_non_square_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        payloads.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_tool, "_urlopen", fake_urlopen)

    meta = image_tool.generate_image(
        prompt="wide academic hierarchy diagram",
        out=tmp_path / "wide.png",
        size="1536x1024",
        env=_env_with_vault(tmp_path),
    )

    assert payloads[0]["size"] == "1536x1024"
    assert meta["requested_size"] == "1536x1024"


def test_generate_image_records_prompt_and_output_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_tool, "_urlopen", fake_urlopen)
    prompt_file = tmp_path / "figure.prompt.txt"
    prompt_file.write_text("clean academic hierarchy diagram", encoding="utf-8")
    out = tmp_path / "figure.png"

    meta = image_tool.generate_image(
        prompt=prompt_file.read_text(encoding="utf-8"),
        prompt_file=prompt_file,
        out=out,
        env=_env_with_vault(tmp_path),
    )
    sidecar = json.loads((tmp_path / "figure.png.json").read_text(encoding="utf-8"))

    assert meta["prompt_path"] == str(prompt_file)
    assert meta["output_path"] == str(out)
    assert meta["output_sha256"] == meta["image"]["sha256"]
    assert sidecar["prompt_path"] == str(prompt_file)
    assert sidecar["output_path"] == str(out)
    assert sidecar["output_sha256"] == sidecar["image"]["sha256"]


def test_generate_image_normalizes_non_multiple_of_16_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        payloads.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_tool, "_urlopen", fake_urlopen)

    meta = image_tool.generate_image(
        prompt="wide academic hierarchy diagram",
        out=tmp_path / "wide.png",
        size="1920x1080",
        env=_env_with_vault(tmp_path),
    )

    assert payloads[0]["size"] == "1920x1088"
    assert meta["requested_size"] == "1920x1088"
    assert meta["original_requested_size"] == "1920x1080"
    assert meta["size_normalized_to_multiple_of_16"] is True


def test_inspect_image_reports_jpeg_dimensions(tmp_path: Path) -> None:
    jpeg = (
        b"\xff\xd8"
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08\x00\x02\x00\x03\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xd9"
    )
    image = tmp_path / "figure.jpg"
    image.write_bytes(jpeg)

    info = image_tool.inspect_image(image)

    assert info["mime"] == "image/jpeg"
    assert info["width"] == 3
    assert info["height"] == 2


def test_review_image_falls_back_to_chat_completions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    calls: list[str] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        calls.append(req.full_url)
        if req.full_url.endswith("/responses"):
            raise HTTPError(req.full_url, 404, "not found", hdrs=cast(Any, None), fp=None)
        assert req.full_url.endswith("/chat/completions")
        return FakeResponse({"choices": [{"message": {"content": "score_1_to_5: 4"}}]})

    monkeypatch.setattr(image_tool, "_urlopen", fake_urlopen)

    result = image_tool.review_image(
        image=image,
        prompt="hierarchy diagram",
        out=tmp_path / "review.json",
        env=_env_with_vault(tmp_path),
    )

    assert calls == [
        "https://example.invalid/openai/v1/responses",
        "https://example.invalid/openai/v1/chat/completions",
    ]
    assert result["review"] == "score_1_to_5: 4"


def test_image_tool_uses_distinct_image_and_review_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault.json"
    save_model_api_routes(
        [
            ModelApiRoute(
                name="image",
                api_key="image-key",
                base_url="https://image.invalid/openai/v1/",
                model="gpt-image-2",
                wire_api="images",
            ),
            ModelApiRoute(
                name="image_review",
                api_key="review-key",
                base_url="https://review.invalid/openai/v1/",
                model="gpt-5.4",
                wire_api="responses",
            ),
        ],
        vault,
    )
    env = {"ARGUS_SKILL_CAPABILITY_VAULT": str(vault)}
    calls: list[tuple[str, str]] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        calls.append((req.full_url, req.get_header("Authorization")))
        if req.full_url.startswith("https://image.invalid/"):
            return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})
        return FakeResponse({"output_text": "score_1_to_5: 5"})

    monkeypatch.setattr(image_tool, "_urlopen", fake_urlopen)
    out = tmp_path / "figure.png"

    image_tool.generate_image(prompt="agent architecture", out=out, env=env)
    image_tool.review_image(image=out, env=env)

    assert calls == [
        ("https://image.invalid/openai/v1/images/generations", "Bearer image-key"),
        ("https://review.invalid/openai/v1/responses", "Bearer review-key"),
    ]


def test_project_image2_helper_records_normalized_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_path = tmp_path / "paper" / "figures" / "method.prompt.txt"
    output_path = tmp_path / "paper" / "figures" / "method.png"
    manifest_path = tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text(image_tool.render_paper_figure_prompt(), encoding="utf-8")

    def fake_generate_image(
        prompt: str,
        prompt_file: Path,
        out: Path,
        size: str,
        force: bool,
    ) -> dict[str, Any]:
        assert "argus-image2-paper-prompt-v1" in prompt
        assert prompt_file == prompt_path
        out.write_bytes(_PNG_BYTES)
        return {
            "artifact": str(out),
            "sidecar": str(out.with_suffix(out.suffix + ".json")),
            "model": "gpt-image-2",
            "requested_size": "1920x1088",
            "original_requested_size": "1920x1080",
            "size_normalized_to_multiple_of_16": True,
        }

    def fake_inspect_image(image: Path) -> dict[str, Any]:
        return {
            "image": str(image),
            "sha256": "image-sha",
            "mime": "image/png",
            "width": 1920,
            "height": 1088,
        }

    def fake_review_image(image: Path, out: Path, prompt: str) -> dict[str, Any]:
        image2_template.write_json(out, {"review": "ok"})
        return {"review": "ok"}

    monkeypatch.setattr(image2_template, "generate_image", fake_generate_image)
    monkeypatch.setattr(image2_template, "inspect_image", fake_inspect_image)
    monkeypatch.setattr(image2_template, "review_image", fake_review_image)

    entry = image2_template.generate_image2_figure(
        project_root=tmp_path,
        prompt_file=prompt_path,
        output=output_path,
        manifest=manifest_path,
        figure_id="method",
        size="1920x1080",
        force=True,
    )

    provenance = json.loads(
        (tmp_path / "paper" / "figures" / "method.png.provenance.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_entry = json.loads(manifest_path.read_text(encoding="utf-8"))["figures"][0]

    assert entry["requested_size"] == "1920x1088"
    assert entry["original_requested_size"] == "1920x1080"
    assert entry["size_normalized_to_multiple_of_16"] is True
    assert provenance["requested_size"] == "1920x1088"
    assert provenance["original_requested_size"] == "1920x1080"
    assert provenance["size_normalized_to_multiple_of_16"] is True
    assert manifest_entry["requested_size"] == "1920x1088"
