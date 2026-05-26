from __future__ import annotations

import base64
import json
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
