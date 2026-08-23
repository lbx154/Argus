from __future__ import annotations

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from urllib.error import HTTPError

import pytest

from argus_skill.tools import image_api
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


def test_project_path_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(image_api.ImageToolError, match="escapes project root"):
        image_api._project_path(tmp_path, Path("../outside.json"))


def test_atomic_json_writes_use_distinct_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "review.json"
    barrier = Barrier(2)
    temporary_paths: list[Path] = []
    real_replace = image_api.os.replace

    def synchronized_replace(source: str | Path, destination: str | Path) -> None:
        temporary_paths.append(Path(source))
        barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(image_api.os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda value: image_api._atomic_write_json(target, {"value": value}),
                (1, 2),
            )
        )

    assert len(set(temporary_paths)) == 2
    assert json.loads(target.read_text(encoding="utf-8"))["value"] in {1, 2}


def test_generate_image_writes_artifact_and_secret_free_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        seen.append(req.full_url)
        assert req.get_header("Authorization") == "******"
        payload = json.loads(req.data.decode("utf-8"))
        assert "size" not in payload
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)
    out = tmp_path / "figure.png"

    meta = image_api.generate_image(
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

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)
    monkeypatch.setattr(image_api.time, "sleep", lambda seconds: sleeps.append(seconds))

    meta = image_api.generate_image(
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


def test_generate_image_caps_retry_after_delay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            headers = Message()
            headers["Retry-After"] = "3600"
            raise HTTPError(
                req.full_url,
                429,
                "too many requests",
                hdrs=headers,
                fp=io.BytesIO(b'{"error":{"code":"rate_limit"}}'),
            )
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)
    monkeypatch.setattr(image_api.time, "sleep", lambda seconds: sleeps.append(seconds))

    image_api.generate_image(
        prompt="clean academic hierarchy diagram",
        out=tmp_path / "figure.png",
        env=_env_with_vault(tmp_path),
    )

    assert sleeps == [45.0]


def test_generate_image_keeps_explicit_non_square_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        payloads.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse({"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)

    meta = image_api.generate_image(
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

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)
    prompt_file = tmp_path / "figure.prompt.txt"
    prompt_file.write_text("clean academic hierarchy diagram", encoding="utf-8")
    out = tmp_path / "figure.png"

    meta = image_api.generate_image(
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

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)

    meta = image_api.generate_image(
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

    info = image_api.inspect_image(image)

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
            raise HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=cast(Any, None),
                fp=io.BytesIO(b"not found"),
            )
        assert req.full_url.endswith("/chat/completions")
        return FakeResponse({"choices": [{"message": {"content": "score_1_to_5: 4"}}]})

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)

    result = image_api.review_image(
        image=image,
        review_instruction="Judge whether this hierarchy diagram is clear.",
        out=tmp_path / "review.json",
        env=_env_with_vault(tmp_path),
    )

    assert calls == [
        "https://example.invalid/openai/v1/responses",
        "https://example.invalid/openai/v1/chat/completions",
    ]
    assert result["review"] == "score_1_to_5: 4"


def test_review_image_requires_caller_authored_instruction(tmp_path: Path) -> None:
    # image_api is domain-neutral: it must not invent a generic evaluation
    # rubric of its own. Callers (e.g. figure_tool) must always render and
    # pass their own instruction text.
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    with pytest.raises(image_api.ImageToolError, match="review_instruction"):
        image_api.review_image(
            image=image,
            review_instruction="   ",
            env=_env_with_vault(tmp_path),
        )


def test_review_image_sends_review_instruction_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End-to-end: the caller-authored review_instruction reaches the model
    # request unchanged, with no venue/paper/figure/method/submission
    # language injected by image_api itself.
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse({"output_text": '{"keep_or_regenerate": "keep"}'})

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)
    instruction = "Output JSON with keep_or_regenerate and confirmed_labels."
    image_api.review_image(
        image=image,
        review_instruction=instruction,
        prompt="hierarchy diagram",
        out=tmp_path / "review.json",
        env=_env_with_vault(tmp_path),
    )
    sent_text = captured["body"]["input"][0]["content"][0]["text"]
    assert sent_text == instruction


def test_image_api_uses_distinct_image_and_review_routes(
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

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)
    out = tmp_path / "figure.png"

    image_api.generate_image(prompt="agent architecture", out=out, env=env)
    image_api.review_image(image=out, review_instruction="Judge this diagram.", env=env)

    assert calls == [
        ("https://image.invalid/openai/v1/images/generations", "******"),
        ("https://review.invalid/openai/v1/responses", "******"),
    ]


def test_review_cli_requires_instruction_or_instruction_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The generic review CLI must fail closed rather than invent a rubric when
    # the caller forgets to author one.
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    rc = image_api.main(["review", "--image", str(image)])
    assert rc == 1
    assert "instruction" in capsys.readouterr().err


def test_review_cli_sends_instruction_file_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(_PNG_BYTES)
    instruction_file = tmp_path / "instruction.txt"
    instruction_file.write_text("Judge this diagram for clarity.", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse({"output_text": "looks fine"})

    monkeypatch.setattr(image_api, "_urlopen", fake_urlopen)
    for key, value in _env_with_vault(tmp_path).items():
        monkeypatch.setenv(key, value)

    rc = image_api.main(
        [
            "review",
            "--image",
            str(image),
            "--instruction-file",
            str(instruction_file),
            "--out",
            str(tmp_path / "review.json"),
        ]
    )
    assert rc == 0
    sent_text = captured["body"]["input"][0]["content"][0]["text"]
    assert sent_text == "Judge this diagram for clarity."
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"] == "looks fine"
