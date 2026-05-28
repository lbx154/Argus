"""Generate and review paper figures with the pre-approved image model."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from .capability_vault import ModelApiGrant, ModelApiRoute, load_model_api_route

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_DEFAULT_TIMEOUT_SECONDS = 500.0
_DEFAULT_MAX_RETRIES = 4
_TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
_AUTO_SIZE_VALUES = {"", "auto", "adaptive"}
_SIZE_RE = re.compile(r"^(?P<width>[1-9]\d*)x(?P<height>[1-9]\d*)$")


class ImageToolError(RuntimeError):
    pass


class ApiError(ImageToolError):
    def __init__(self, *, status: int, endpoint: str, body: str) -> None:
        self.status = status
        self.endpoint = endpoint
        self.body = body
        super().__init__(f"API request failed ({status}) at {endpoint}: {body[:500]}")


def _urlopen(req: urllib.request.Request | str, timeout: float):  # noqa: ANN001
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - configured operator endpoint


def _redact(text: str, grant: ModelApiGrant | ModelApiRoute | None = None) -> str:
    redacted = str(text or "")
    if grant is not None and grant.api_key:
        redacted = redacted.replace(grant.api_key, "<redacted-api-key>")
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", redacted)
    redacted = re.sub(r"(?i)(api[-_]?key=)[^&\s]+", r"\1<redacted>", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9]{12,}", "sk-<redacted>", redacted)
    return redacted


def _endpoint_url(base_url: str, endpoint: str) -> str:
    base = base_url.rstrip("/")
    if not base:
        raise ImageToolError("missing model API base URL")
    return f"{base}/{endpoint.lstrip('/')}"


def _retry_delay_seconds(exc: BaseException, attempt_index: int) -> float | None:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code not in _TRANSIENT_HTTP_STATUS_CODES:
            return None
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass
    elif not isinstance(exc, urllib.error.URLError):
        return None
    return min(45.0, 3.0 * (2**attempt_index))


def _json_request(
    grant: ModelApiGrant | ModelApiRoute,
    endpoint: str,
    payload: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    url = _endpoint_url(grant.base_url, endpoint)
    body = json.dumps(payload).encode("utf-8")
    raw = ""
    attempts = max(1, int(max_retries))
    for attempt_index in range(attempts):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {grant.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with _urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            delay = _retry_delay_seconds(exc, attempt_index)
            if delay is not None and attempt_index < attempts - 1:
                time.sleep(delay)
                continue
            raise ApiError(
                status=exc.code,
                endpoint=endpoint,
                body=_redact(raw, grant),
            ) from exc
        except urllib.error.URLError as exc:
            delay = _retry_delay_seconds(exc, attempt_index)
            if delay is not None and attempt_index < attempts - 1:
                time.sleep(delay)
                continue
            raise ImageToolError(_redact(str(exc), grant)) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImageToolError(f"non-JSON response from {endpoint}: {_redact(raw[:500], grant)}") from exc
    if not isinstance(data, dict):
        raise ImageToolError(f"unexpected response from {endpoint}: {type(data).__name__}")
    return data


def _read_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt_file is not None:
        text = prompt_file.read_text(encoding="utf-8")
    else:
        text = prompt or ""
    text = text.strip()
    if not text:
        raise ImageToolError("missing prompt; pass --prompt-file or --prompt")
    if "\x00" in text:
        raise ImageToolError("prompt contains NUL byte")
    return text


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _infer_mime(data: bytes) -> str:
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    return "application/octet-stream"


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if not data.startswith(_PNG_MAGIC) or len(data) < 24:
        return None, None
    return struct.unpack(">II", data[16:24])


def _image_dimensions(path: Path, data: bytes) -> tuple[int | None, int | None]:
    if data.startswith(_PNG_MAGIC):
        return _png_dimensions(data)
    if data.startswith(_JPEG_MAGIC):
        return _jpeg_dimensions(data)
    return None, None


def _jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None, None
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None, None
        if 0xC0 <= marker <= 0xC3 and segment_length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    return None, None


def inspect_image(image: Path) -> dict[str, Any]:
    data = image.read_bytes()
    width, height = _image_dimensions(image, data)
    return {
        "image": str(image),
        "exists": True,
        "bytes": len(data),
        "sha256": _sha256_bytes(data),
        "mime": _infer_mime(data),
        "width": width,
        "height": height,
    }


def _extract_image_bytes(
    data: dict[str, Any],
    *,
    timeout: float,
) -> bytes:
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        raise ImageToolError("image response missing data[0]")
    first = rows[0]
    if not isinstance(first, dict):
        raise ImageToolError("image response data[0] is not an object")
    b64 = first.get("b64_json") or first.get("image_base64")
    if isinstance(b64, str) and b64.strip():
        try:
            return base64.b64decode(b64, validate=True)
        except ValueError as exc:
            raise ImageToolError("image response contained invalid base64") from exc
    url = first.get("url")
    if isinstance(url, str) and url.strip():
        try:
            with _urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.URLError as exc:
            raise ImageToolError(f"failed to fetch generated image URL: {exc}") from exc
    raise ImageToolError("image response missing b64_json or url")


def _atomic_write(path: Path, data: bytes, *, force: bool) -> None:
    if path.exists() and not force:
        raise ImageToolError(f"{path} already exists; pass --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: dict[str, Any], *, force: bool = True) -> None:
    if path.exists() and not force:
        raise ImageToolError(f"{path} already exists; pass --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sidecar_path(out: Path) -> Path:
    suffix = out.suffix or ".image"
    return out.with_suffix(suffix + ".json")


def _round_up_to_multiple(value: int, multiple: int) -> int:
    return ((value + multiple - 1) // multiple) * multiple


def _normalize_requested_size(size: str) -> tuple[str, str | None]:
    requested = (size or "auto").strip().lower()
    if requested in _AUTO_SIZE_VALUES:
        return requested or "auto", None
    match = _SIZE_RE.fullmatch(requested)
    if not match:
        raise ImageToolError(
            f"invalid image size {size!r}; use 'auto' or WIDTHxHEIGHT, "
            "for example 1536x1024 or 1920x1088"
        )
    width = int(match.group("width"))
    height = int(match.group("height"))
    normalized_width = _round_up_to_multiple(width, 16)
    normalized_height = _round_up_to_multiple(height, 16)
    normalized = f"{normalized_width}x{normalized_height}"
    return normalized, requested if normalized != requested else None


def _require_route(route_name: str, env: Mapping[str, str] | None = None) -> ModelApiRoute:
    route = load_model_api_route(route_name, env)
    if route is None or not route.usable:
        raise ImageToolError(
            f"model API route {route_name!r} unavailable; initialize the vault "
            "or configure that route with api_key, base_url, and model"
        )
    return route


def generate_image(
    *,
    prompt: str,
    out: Path,
    prompt_file: Path | None = None,
    size: str = "auto",
    force: bool = False,
    env: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    grant = _require_route("image", env)
    requested_size, original_requested_size = _normalize_requested_size(size)
    payload = {
        "model": grant.model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }
    if requested_size not in _AUTO_SIZE_VALUES:
        payload["size"] = requested_size
    started = time.time()
    try:
        response = _json_request(
            grant,
            "/images/generations",
            payload,
            timeout=timeout,
            max_retries=max_retries,
        )
    except ApiError as exc:
        if exc.status == 400 and "response_format" in exc.body:
            payload.pop("response_format", None)
            response = _json_request(
                grant,
                "/images/generations",
                payload,
                timeout=timeout,
                max_retries=max_retries,
            )
        else:
            raise
    image_bytes = _extract_image_bytes(response, timeout=timeout)
    mime = _infer_mime(image_bytes)
    if mime == "application/octet-stream":
        raise ImageToolError("generated bytes are not a recognized PNG/JPEG image")
    _atomic_write(out, image_bytes, force=force)
    info = inspect_image(out)
    meta = {
        "artifact": str(out),
        "sidecar": str(_sidecar_path(out)),
        "created_at_unix": started,
        "duration_seconds": round(time.time() - started, 3),
        "model": grant.model,
        "output_path": str(out),
        "output_sha256": str(info.get("sha256") or ""),
        "requested_size": requested_size or "auto",
        "prompt": prompt,
        "prompt_sha256": _sha256_text(prompt),
        "image": info,
        "api": {
            "provider": grant.provider,
            "wire_api": grant.wire_api,
            "endpoint": "/images/generations",
            "base_url_source": grant.base_url_source,
            "key_source": grant.key_source,
        },
    }
    if prompt_file is not None:
        meta["prompt_path"] = str(prompt_file)
    if original_requested_size is not None:
        meta["original_requested_size"] = original_requested_size
        meta["size_normalized_to_multiple_of_16"] = True
    _atomic_write_json(_sidecar_path(out), meta)
    return meta


def _data_url(path: Path) -> str:
    data = path.read_bytes()
    mime = _infer_mime(data)
    if mime == "application/octet-stream":
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _review_prompt(*, original_prompt: str, rubric: str) -> str:
    return (
        "You are reviewing an academic paper figure generated for an EMNLP paper. "
        "Judge visual quality, scientific clarity, text legibility, and faithfulness "
        "to the requested prompt. Return concise JSON-compatible prose with: "
        "score_1_to_5, major_issues, concrete_revision_prompt, and keep_or_regenerate.\n\n"
        f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
        f"Rubric:\n{rubric or 'Prefer clean vector-like academic style, readable labels, and no fabricated numbers.'}"
    )


def _parse_responses_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
    return "\n".join(chunks).strip()


def _parse_chat_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = [
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict) and str(part.get("text") or "").strip()
        ]
        return "\n".join(chunks)
    return ""


def _load_sidecar_prompt(image: Path) -> str:
    sidecar = _sidecar_path(image)
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    prompt = data.get("prompt") if isinstance(data, dict) else ""
    return str(prompt or "").strip()


def review_image(
    *,
    image: Path,
    out: Path | None = None,
    prompt: str = "",
    rubric: str = "",
    env: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    grant = _require_route("image_review", env)
    original_prompt = prompt.strip() or _load_sidecar_prompt(image)
    text = _review_prompt(original_prompt=original_prompt, rubric=rubric)
    image_url = _data_url(image)
    payload = {
        "model": grant.model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": text},
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ],
            }
        ],
    }
    endpoint = "/responses"
    try:
        data = _json_request(
            grant,
            endpoint,
            payload,
            timeout=timeout,
            max_retries=max_retries,
        )
        review_text = _parse_responses_text(data)
    except ApiError as exc:
        if exc.status not in (400, 404):
            raise
        endpoint = "/chat/completions"
        chat_payload = {
            "model": grant.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                    ],
                }
            ],
        }
        data = _json_request(
            grant,
            endpoint,
            chat_payload,
            timeout=timeout,
            max_retries=max_retries,
        )
        review_text = _parse_chat_text(data)
    if not review_text:
        raise ImageToolError("review model returned no text")
    info = inspect_image(image)
    result = {
        "image": info,
        "model": grant.model,
        "endpoint": endpoint,
        "prompt": original_prompt,
        "rubric": rubric,
        "review": review_text,
    }
    target = out or image.with_suffix(image.suffix + ".review.json")
    _atomic_write_json(target, result)
    return result


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.tools.image_tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="generate an image artifact")
    gen.add_argument("--prompt")
    gen.add_argument("--prompt-file", type=Path)
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--size", default="auto")
    gen.add_argument("--force", action="store_true")
    gen.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    gen.add_argument("--max-retries", type=int, default=_DEFAULT_MAX_RETRIES)

    ins = sub.add_parser("inspect", help="inspect a local image without a model call")
    ins.add_argument("--image", type=Path, required=True)

    rev = sub.add_parser("review", help="review a local image with the vision-capable text model")
    rev.add_argument("--image", type=Path, required=True)
    rev.add_argument("--out", type=Path)
    rev.add_argument("--prompt")
    rev.add_argument("--prompt-file", type=Path)
    rev.add_argument("--rubric", default="")
    rev.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    rev.add_argument("--max-retries", type=int, default=_DEFAULT_MAX_RETRIES)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "generate":
            prompt = _read_prompt(args.prompt, args.prompt_file)
            _print_json(generate_image(
                prompt=prompt,
                out=args.out,
                prompt_file=args.prompt_file,
                size=args.size,
                force=bool(args.force),
                timeout=float(args.timeout),
                max_retries=int(args.max_retries),
            ))
            return 0
        if args.cmd == "inspect":
            _print_json(inspect_image(args.image))
            return 0
        if args.cmd == "review":
            prompt = _read_prompt(args.prompt, args.prompt_file) if (args.prompt or args.prompt_file) else ""
            _print_json(review_image(
                image=args.image,
                out=args.out,
                prompt=prompt,
                rubric=args.rubric,
                timeout=float(args.timeout),
                max_retries=int(args.max_retries),
            ))
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus-skill image-tool: {_redact(str(exc))}\n")
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
