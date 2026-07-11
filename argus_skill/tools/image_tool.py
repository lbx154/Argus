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
from argus_skill.skills.venue_profiles import VenueProfile, get_venue_profile

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_DEFAULT_TIMEOUT_SECONDS = 500.0
_DEFAULT_MAX_RETRIES = 4
_TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
_AUTO_SIZE_VALUES = {"", "auto", "adaptive"}
_SIZE_RE = re.compile(r"^(?P<width>[1-9]\d*)x(?P<height>[1-9]\d*)$")
PAPER_FIGURE_PROMPT_TEMPLATE_ID = "argus-image2-paper-prompt-v1"
PAPER_FIGURE_STUDIO_SOURCE_ID = "paper-framework-figure-studio-pro-v3.1.4a"
PAPER_FIGURE_STUDIO_DEFAULT_STAGE = "S5-CANDIDATE-IMAGE"


PAPER_FIGURE_PROMPT_TEMPLATE = """Create one polished EMNLP method figure variant.
Prompt template: {template_id}
Prompt source: {figure_studio_source}
{framing}

General style:
- EMNLP/ACL/NeurIPS/CS paper method figure, full-width two-column landscape.
- Clean block-based Figma style with rounded cards (10-16px radius), neat alignment, soft pastel fills, dark-gray 2px borders, and compact information density.
- Compact, information-rich, suitable for a PDF page-width figure; little wasted space but not crowded.
- Tidy rounded or friendly sans-serif feel; must remain crisp and readable.
- Moderate badge/icon use only when semantically useful; a few simple recognizable icons are fine, not a logo wall.
- No heavy shadows, no gradients, no photorealism, no glassmorphism, no messy Excalidraw look.
- Large readable labels, short phrases, balanced hierarchy, flat vector-like raster rendering on warm white #fbfaf7.
- 干净、密实、模块化、Figma 风，圆角卡片为主，低饱和浅色块，少量 badge/logo，少留白但不拥挤。整体适合 EMNLP/ACL/NeurIPS 论文主图，不要像随手白板，也不要像艺术插画。

Style intent:
- Clean, dense, modular, Figma-like, mostly rounded cards, low-saturation pastel blocks.
- Use small badges/icons sparingly; avoid empty space while preserving alignment.
- It should look like a main figure in an EMNLP/ACL/NeurIPS paper, not a marketing graphic, stock illustration, dashboard screenshot, or casual whiteboard.

Pinned content that must appear exactly:
{content}
- SPELL EXACTLY every quoted label above. Do not invent alternate terminology, code identifiers, raw artifact paths, or extra labels.

Layout variant:
- {layout_variant}
- Keep the visible labels faithful to the pinned content, but use the layout variant to create a polished, dense, paper-native composition with visual hierarchy.
- Prefer grouped modules, phase containers, compact chips, and clear arrows over a sparse chain of identical boxes.

Negative prompt / Avoid:
- no concrete code snippets, raw paths, tiny unreadable text, character-level vertical text, or dense paragraphs
- no excessive logos or brand marks, no watermark
- no photorealistic scenes, stock photos, glassmorphism, heavy gradients, heavy shadows, texture, or arbitrary decorative blobs
- no messy whiteboard / Excalidraw-heavy sketch style
- no large empty areas, overlapping cards, squashed labels, inconsistent terminology, or extra captions that make it look like a dashboard
- no inconsistent terminology between figure and text

Aspect ratio:
- {aspect_ratio}

Figma tokens for camera-ready cleanup:
- Background #fbfaf7; stroke #1f2933 at 2px.
- Corner radius 10-16px; card padding 12-20px; card gap 12-24px.
- Pastels: acquisition #ffe2d1, parsing #fff2bd, memory/wiki #dcecff, agent #e2f7df, domains #eadfff, benchmark #fff1c9.
- Text sizes: title 38-52px, section headers 22-30px, card labels 16-22px, chips 12-16px.
"""


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_prompt_file(path: Path) -> str:
    """Canonical prompt hash: always hash the raw file bytes on disk.

    Never use ``_sha256_text(text.strip())`` for prompt hashes stored in
    manifests, provenance, or sidecars — downstream consumers read the
    file as raw bytes, so stripped-text hashes will silently mismatch and
    block the pipeline forever.
    """
    return _sha256_file(path)


def render_paper_figure_prompt(
    *,
    figure_title: str = "Method Overview",
    content: str = "",
    layout_variant: str = (
        "20 polished Figma wireframe: component frames, auto-layout-like spacing, "
        "section tabs, chips, and carefully staggered components."
    ),
    framing: str = "",
    aspect_ratio: str = "1536x1024 landscape",
    venue_profile: VenueProfile | None = None,
    # Legacy parameters — composed into content block if content is empty
    studio_stage: str = PAPER_FIGURE_STUDIO_DEFAULT_STAGE,  # noqa: ARG001 — kept for write_paper_figure_prompt's keyword pass-through
    input_label: str = "",
    mechanism_label: str = "",
    verification_label: str = "",
    state_label: str = "",
    execution_label: str = "",
    output_label: str = "",
    evidence_label: str = "",
    benefit_label: str = "",
    failure_label: str = "",
    caption_plan: str = "",
    legend_plan: str = "",
    body_reference_plan: str = "",
    core_step_visibility_plan: str = "",
    claimed_improvement_anchor: str = "",
    symbol_formula_necessity: str = "",
    semantic_contract: str = "",
) -> str:
    """Render a paper-figure prompt using the 6-section structure.

    Preferred usage: provide ``figure_title``, a free-form ``content`` block
    listing every label that must appear verbatim, a ``layout_variant``,
    and an ``aspect_ratio``.

    If ``content`` is empty, legacy stage-label parameters are composed into
    a default content block for backward compatibility.
    """
    if not content.strip():
        stages = [s for s in [
            input_label, mechanism_label, verification_label,
            state_label, execution_label, output_label, evidence_label,
        ] if s]
        chips = [c for c in [benefit_label, failure_label] if c]
        lines = [f'- Title: "{figure_title}"']
        if stages:
            lines.append('- Show: "' + '" -> "'.join(stages) + '".')
        if chips:
            lines.append('- Components/chips: "' + '", "'.join(chips) + '".')
        content = "\n".join(lines)

    if not framing.strip():
        persona = venue_profile.figure_style_persona if venue_profile is not None else "EMNLP/ACL"
        framing = (
            f"Figma-style technical diagram for an {persona} paper. "
            f"Subject: {figure_title}."
        )

    prompt = PAPER_FIGURE_PROMPT_TEMPLATE.format(
        template_id=PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        figure_studio_source=PAPER_FIGURE_STUDIO_SOURCE_ID,
        framing=framing,
        content=content,
        layout_variant=layout_variant,
        aspect_ratio=aspect_ratio,
    ).strip() + _plan_section(
        caption_plan=caption_plan,
        legend_plan=legend_plan,
        body_reference_plan=body_reference_plan,
        core_step_visibility_plan=core_step_visibility_plan,
        claimed_improvement_anchor=claimed_improvement_anchor,
        symbol_formula_necessity=symbol_formula_necessity,
        semantic_contract=semantic_contract,
    ) + "\n"
    if venue_profile is not None:
        prompt = _apply_venue_persona(prompt, venue_profile)
    return prompt


def _apply_venue_persona(prompt: str, venue_profile: VenueProfile) -> str:
    """Rewrite the venue persona baked into the static prompt template.

    ``PAPER_FIGURE_PROMPT_TEMPLATE`` hardcodes the EMNLP/ACL/NeurIPS family in
    a few places (``EMNLP method figure``, the ``EMNLP/ACL/NeurIPS ... paper``
    style clauses, the Chinese "适合 EMNLP/ACL/NeurIPS 论文主图" note). When a
    ``venue_profile`` is supplied, swap those literals for the profile's
    ``figure_style_persona`` (family clauses) / ``reviewer_persona`` (the short
    "<venue> method figure" label) so an AAAI figure reads as an AAAI figure.
    This is a true no-op for the EMNLP profile (figure_style_persona ==
    "EMNLP/ACL/NeurIPS", reviewer_persona == "EMNLP") and is never called when
    ``venue_profile`` is None, so legacy prompts are byte-identical.
    """
    persona = venue_profile.figure_style_persona
    replaced = prompt.replace("EMNLP/ACL/NeurIPS", persona)
    replaced = replaced.replace(
        "EMNLP method figure", f"{venue_profile.reviewer_persona} method figure"
    )
    return replaced


def _plan_section(
    *,
    caption_plan: str = "",
    legend_plan: str = "",
    body_reference_plan: str = "",
    core_step_visibility_plan: str = "",
    claimed_improvement_anchor: str = "",
    symbol_formula_necessity: str = "",
    semantic_contract: str = "",
) -> str:
    """Render the optional figure-plan directives as a labelled block.

    These were previously accepted by ``render_paper_figure_prompt`` /
    ``write_paper_figure_prompt`` and wired from real CLI flags + the
    paper-illustration skill, but silently DROPPED — so an agent following the
    documented workflow passed ``--caption-plan`` / ``--semantic-contract`` and
    they never reached the prompt. This wires them through. Additive: when every
    plan is empty (all legacy usage) it returns ``""`` so the base prompt is
    byte-for-byte unchanged; a non-empty plan is appended as an explicit
    "must honor" constraint the image model can act on.
    """
    directives = [
        ("Caption plan", caption_plan),
        ("Legend plan", legend_plan),
        ("Body reference", body_reference_plan),
        ("Core steps that must stay visible", core_step_visibility_plan),
        ("Claimed-improvement anchor", claimed_improvement_anchor),
        ("Symbol/formula necessity", symbol_formula_necessity),
        ("Semantic contract", semantic_contract),
    ]
    lines = [f"- {label}: {val.strip()}" for label, val in directives if val and val.strip()]
    if not lines:
        return ""
    return "\n\nFigure plan (must honor):\n" + "\n".join(lines)


def write_paper_figure_prompt(
    prompt_file: Path,
    *,
    figure_title: str = "Method Overview",
    content: str = "",
    layout_variant: str = (
        "20 polished Figma wireframe: component frames, auto-layout-like spacing, "
        "section tabs, chips, and carefully staggered components."
    ),
    framing: str = "",
    aspect_ratio: str = "1536x1024 landscape",
    venue_profile: VenueProfile | None = None,
    force: bool = False,
    # Legacy parameters — passed through for backward compat
    studio_stage: str = PAPER_FIGURE_STUDIO_DEFAULT_STAGE,
    input_label: str = "",
    mechanism_label: str = "",
    verification_label: str = "",
    state_label: str = "",
    execution_label: str = "",
    output_label: str = "",
    evidence_label: str = "",
    benefit_label: str = "",
    failure_label: str = "",
    caption_plan: str = "",
    legend_plan: str = "",
    body_reference_plan: str = "",
    core_step_visibility_plan: str = "",
    claimed_improvement_anchor: str = "",
    symbol_formula_necessity: str = "",
    semantic_contract: str = "",
) -> dict[str, Any]:
    if prompt_file.exists() and not force:
        raise ImageToolError(f"{prompt_file} already exists; pass --force to overwrite")
    prompt = render_paper_figure_prompt(
        figure_title=figure_title,
        content=content,
        layout_variant=layout_variant,
        framing=framing,
        aspect_ratio=aspect_ratio,
        venue_profile=venue_profile,
        studio_stage=studio_stage,
        input_label=input_label,
        mechanism_label=mechanism_label,
        verification_label=verification_label,
        state_label=state_label,
        execution_label=execution_label,
        output_label=output_label,
        evidence_label=evidence_label,
        benefit_label=benefit_label,
        failure_label=failure_label,
        caption_plan=caption_plan,
        legend_plan=legend_plan,
        body_reference_plan=body_reference_plan,
        core_step_visibility_plan=core_step_visibility_plan,
        claimed_improvement_anchor=claimed_improvement_anchor,
        symbol_formula_necessity=symbol_formula_necessity,
        semantic_contract=semantic_contract,
    )
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")
    return {
        "prompt_path": str(prompt_file),
        "prompt_sha256": _sha256_prompt_file(prompt_file),
        "template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "bytes": len(prompt.encode("utf-8")),
    }


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


def _image_dimensions(data: bytes) -> tuple[int | None, int | None]:
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
    width, height = _image_dimensions(data)
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
        "prompt_sha256": _sha256_prompt_file(prompt_file) if prompt_file is not None else _sha256_text(prompt),
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


def _review_prompt(
    *,
    original_prompt: str,
    rubric: str,
    venue_profile: VenueProfile | None = None,
) -> str:
    figure_persona = (
        venue_profile.figure_style_persona if venue_profile is not None else "EMNLP/ACL"
    )
    reviewer_persona = (
        venue_profile.reviewer_persona if venue_profile is not None else "EMNLP"
    )
    return (
        f"You are reviewing an academic paper figure for an {figure_persona} submission. "
        "Your ONLY job is to judge whether the figure effectively communicates "
        "the paper's method to a reader. Do NOT nitpick pixel-level prompt "
        "compliance, chip placement, badge count, or exact visual hierarchy — "
        "those are style preferences, not quality issues.\n\n"
        "Focus on these questions:\n"
        "1. Does the figure faithfully represent the paper's method/architecture?\n"
        "2. Is the core contribution module visible (not an empty box)?\n"
        "3. Are labels readable and correctly spelled?\n"
        "4. Is the data flow / reader path clear?\n"
        f"5. Would an {reviewer_persona} reviewer understand the method from this figure + its caption?\n\n"
        "Return JSON with:\n"
        "- score_1_to_5: 4+ means acceptable for submission, 3 means needs one more pass, "
        "1-2 means fundamentally wrong (wrong modules, misleading flow, unreadable)\n"
        "- major_issues: ONLY issues that would mislead a reader or misrepresent the method. "
        "Do NOT list cosmetic preferences as major issues.\n"
        "- concrete_revision_prompt: if score < 4, provide a SPECIFIC revision to the prompt "
        "that fixes the actual problem. The prompt must still use the standard template "
        "(General style, Pinned content, Layout variant, Negative prompt, Aspect ratio, "
        "Figma tokens sections).\n"
        "- keep_or_regenerate: 'keep' if score >= 4, 'regenerate' only if the figure "
        "would actively mislead readers about the method.\n\n"
        f"Original figure prompt:\n{original_prompt or '(not provided)'}\n\n"
        f"Rubric:\n{rubric or f'Does this figure effectively communicate the paper method to an {reviewer_persona} reviewer?'}"
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
    venue_profile: VenueProfile | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    grant = _require_route("image_review", env)
    original_prompt = prompt.strip() or _load_sidecar_prompt(image)
    text = _review_prompt(
        original_prompt=original_prompt,
        rubric=rubric,
        venue_profile=venue_profile,
    )
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


def _project_path(project_root: Path, path: Path | str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else project_root / value


def _project_relative(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImageToolError(f"{path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ImageToolError(f"{path} must contain a JSON object")
    return payload


def _load_image2_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"figures": []}
    payload = _read_json_object(path)
    figures = payload.get("figures")
    if not isinstance(figures, list):
        payload["figures"] = []
    return payload


def _upsert_image2_manifest_entry(manifest_path: Path, entry: dict[str, Any]) -> None:
    payload = _load_image2_manifest(manifest_path)
    figures = payload.setdefault("figures", [])
    figure_id = str(entry.get("figure_id") or "")
    replaced = False
    for index, existing in enumerate(figures):
        if isinstance(existing, dict) and str(existing.get("figure_id") or "") == figure_id:
            figures[index] = entry
            replaced = True
            break
    if not replaced:
        figures.append(entry)
    _atomic_write_json(manifest_path, payload)


def _prompt_hash_variants(prompt_file: Path) -> set[str]:
    """Return all plausible SHA-256 hashes for a prompt file.

    Accepts raw-file hash (canonical), stripped-text hash, and
    as-is-text hash so that sidecars written by older versions
    still pass validation.
    """
    raw_file_hash = _sha256_file(prompt_file)
    text = prompt_file.read_text(encoding="utf-8", errors="replace")
    return {raw_file_hash, _sha256_text(text), _sha256_text(text.strip())}


def _require_matching_prompt(
    *,
    prompt_file: Path,
    sidecar: dict[str, Any],
    allow_noncanonical_prompt: bool,
) -> tuple[str, str]:
    prompt_text = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise ImageToolError(f"prompt file is empty: {prompt_file}")
    if not allow_noncanonical_prompt:
        missing: list[str] = []
        if PAPER_FIGURE_PROMPT_TEMPLATE_ID not in prompt_text:
            missing.append(PAPER_FIGURE_PROMPT_TEMPLATE_ID)
        if PAPER_FIGURE_STUDIO_SOURCE_ID not in prompt_text:
            missing.append(PAPER_FIGURE_STUDIO_SOURCE_ID)
        if missing:
            raise ImageToolError(
                "paper image-2 prompts must be derived from the built-in Argus "
                f"figure-studio prompt; missing {', '.join(missing)}. "
                "Run `python -m argus_skill.tools.image_tool paper-prompt ...`."
            )

    prompt_sha = str(sidecar.get("prompt_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", prompt_sha):
        raise ImageToolError("generation sidecar must contain a lowercase prompt_sha256")
    if prompt_sha not in _prompt_hash_variants(prompt_file):
        raise ImageToolError(
            "generation sidecar prompt_sha256 does not match prompt_path; "
            "regenerate through image-2 or restore the matching prompt file"
        )
    raw_prompt = sidecar.get("prompt")
    if not isinstance(raw_prompt, str) or not raw_prompt.strip():
        raise ImageToolError("generation sidecar must preserve the exact prompt text")
    raw_prompt_hashes = {
        _sha256_text(raw_prompt),
        _sha256_text(raw_prompt.strip()),
        _sha256_text(raw_prompt.rstrip("\n")),
    }
    if prompt_sha not in raw_prompt_hashes and not (
        raw_prompt_hashes & _prompt_hash_variants(prompt_file)
    ):
        raise ImageToolError("generation sidecar prompt text hash does not match prompt_sha256")
    return prompt_text, prompt_sha


def _sidecar_output_sha(sidecar: dict[str, Any]) -> str:
    for field in ("output_sha256", "sha256"):
        value = str(sidecar.get(field) or "").strip().lower()
        if value:
            return value
    image = sidecar.get("image")
    if isinstance(image, dict):
        for field in ("output_sha256", "sha256"):
            value = str(image.get(field) or "").strip().lower()
            if value:
                return value
    return ""


def _review_image_sha(review: dict[str, Any]) -> str:
    image = review.get("image")
    if isinstance(image, dict):
        for field in ("output_sha256", "sha256"):
            value = str(image.get(field) or "").strip().lower()
            if value:
                return value
    return ""


def _recorded_prompt_path(project_root: Path, prompt_file: Path, sidecar: dict[str, Any]) -> str:
    raw_prompt_path = sidecar.get("prompt_path")
    if isinstance(raw_prompt_path, str) and raw_prompt_path.strip():
        candidate = _project_path(project_root, raw_prompt_path)
        if candidate.resolve() == prompt_file.resolve():
            return _project_relative(project_root, candidate)
    return _project_relative(project_root, prompt_file)


def sync_paper_metadata(
    *,
    project_root: Path,
    image: Path,
    figure_id: str,
    figure_type: str = "method",
    manifest: Path = Path("paper/figures/IMAGE2_FIGURES.json"),
    prompt_file: Path | None = None,
    sidecar: Path | None = None,
    inspect_path: Path | None = None,
    review_path: Path | None = None,
    provenance_path: Path | None = None,
    figure_studio_stage: str = PAPER_FIGURE_STUDIO_DEFAULT_STAGE,
    allow_noncanonical_prompt: bool = False,
) -> dict[str, Any]:
    """Synchronize image-2 manifest/provenance from the real raster and sidecars."""

    if not figure_id.strip():
        raise ImageToolError("missing --figure-id")
    project_root = project_root.resolve()
    image_path = _project_path(project_root, image)
    if not image_path.is_file():
        raise ImageToolError(f"generated image does not exist: {image_path}")

    sidecar_path = _project_path(project_root, sidecar) if sidecar is not None else _sidecar_path(image_path)
    if not sidecar_path.is_file():
        raise ImageToolError(f"generation sidecar does not exist: {sidecar_path}")
    sidecar_payload = _read_json_object(sidecar_path)

    if prompt_file is None:
        raw_prompt_path = sidecar_payload.get("prompt_path")
        if not isinstance(raw_prompt_path, str) or not raw_prompt_path.strip():
            raise ImageToolError("pass --prompt-file; generation sidecar has no prompt_path")
        prompt_path = _project_path(project_root, raw_prompt_path)
    else:
        prompt_path = _project_path(project_root, prompt_file)
    if not prompt_path.is_file():
        raise ImageToolError(f"prompt file does not exist: {prompt_path}")
    _prompt_text, _sidecar_prompt_sha = _require_matching_prompt(
        prompt_file=prompt_path,
        sidecar=sidecar_payload,
        allow_noncanonical_prompt=allow_noncanonical_prompt,
    )
    # Always use the canonical raw-file hash for downstream artifacts,
    # regardless of what the sidecar recorded (it may use an older
    # stripped-text hash convention).
    prompt_sha = _sha256_prompt_file(prompt_path)

    image_info = inspect_image(image_path)
    output_sha = str(image_info.get("sha256") or "").strip().lower()
    sidecar_output_sha = _sidecar_output_sha(sidecar_payload)
    if sidecar_output_sha != output_sha:
        raise ImageToolError(
            "generation sidecar output SHA-256 does not match the current raster; "
            "do not patch only metadata hashes"
        )

    inspect_sidecar_path = (
        _project_path(project_root, inspect_path)
        if inspect_path is not None
        else image_path.with_suffix(image_path.suffix + ".inspect.json")
    )
    _atomic_write_json(inspect_sidecar_path, image_info)

    review_sidecar_path = (
        _project_path(project_root, review_path)
        if review_path is not None
        else image_path.with_suffix(image_path.suffix + ".review.json")
    )
    if not review_sidecar_path.is_file():
        raise ImageToolError(f"review sidecar does not exist: {review_sidecar_path}")
    review_payload = _read_json_object(review_sidecar_path)
    review_sha = _review_image_sha(review_payload)
    if review_sha and review_sha != output_sha:
        raise ImageToolError("review sidecar image SHA-256 does not match the current raster")

    provenance_sidecar_path = (
        _project_path(project_root, provenance_path)
        if provenance_path is not None
        else image_path.with_suffix(image_path.suffix + ".provenance.json")
    )
    manifest_path = _project_path(project_root, manifest)

    model = str(sidecar_payload.get("model") or "gpt-image-2")
    requested_size = str(
        sidecar_payload.get("requested_size")
        or f"{image_info.get('width') or 0}x{image_info.get('height') or 0}"
    )
    prompt_rel = _recorded_prompt_path(project_root, prompt_path, sidecar_payload)
    output_rel = _project_relative(project_root, image_path)
    sidecar_rel = _project_relative(project_root, sidecar_path)
    inspect_rel = _project_relative(project_root, inspect_sidecar_path)
    review_rel = _project_relative(project_root, review_sidecar_path)
    provenance_rel = _project_relative(project_root, provenance_sidecar_path)
    review_file_sha = _sha256_file(review_sidecar_path)

    provenance = {
        "figure_id": figure_id,
        "figure_type": figure_type,
        "generator": "codex-image2",
        "model": model,
        "generator_model": model,
        "tool": "argus_skill.tools.image_tool",
        "prompt_template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "figure_studio_source": PAPER_FIGURE_STUDIO_SOURCE_ID,
        "figure_studio_stage": figure_studio_stage,
        "prompt_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "output_path": output_rel,
        "output_sha256": output_sha,
        "sidecar_path": sidecar_rel,
        "inspect_path": inspect_rel,
        "review_path": review_rel,
        "review_sha256": review_file_sha,
        "requested_size": requested_size,
        "width": image_info.get("width"),
        "height": image_info.get("height"),
    }
    original_requested_size = sidecar_payload.get("original_requested_size")
    if isinstance(original_requested_size, str) and original_requested_size:
        provenance["original_requested_size"] = original_requested_size
    if sidecar_payload.get("size_normalized_to_multiple_of_16") is True:
        provenance["size_normalized_to_multiple_of_16"] = True
    _atomic_write_json(provenance_sidecar_path, provenance)

    entry = {
        "figure_id": figure_id,
        "figure_type": figure_type,
        "source": "raster",
        "generator": "codex-image2",
        "model": model,
        "generator_model": model,
        "prompt_template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "figure_studio_source": PAPER_FIGURE_STUDIO_SOURCE_ID,
        "figure_studio_stage": figure_studio_stage,
        "prompt_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "output_path": output_rel,
        "output_sha256": output_sha,
        "sidecar_path": sidecar_rel,
        "inspect_path": inspect_rel,
        "review_path": review_rel,
        "review_sha256": review_file_sha,
        "generation_provenance_path": provenance_rel,
        "requested_size": requested_size,
        "width": image_info.get("width"),
        "height": image_info.get("height"),
    }
    if isinstance(original_requested_size, str) and original_requested_size:
        entry["original_requested_size"] = original_requested_size
    if sidecar_payload.get("size_normalized_to_multiple_of_16") is True:
        entry["size_normalized_to_multiple_of_16"] = True
    _upsert_image2_manifest_entry(manifest_path, entry)
    return entry


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.tools.image_tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    paper = sub.add_parser("paper-prompt", help="write the canonical Argus paper figure prompt")
    paper.add_argument("--out", type=Path, required=True)
    paper.add_argument("--force", action="store_true")
    paper.add_argument("--studio-stage", default=PAPER_FIGURE_STUDIO_DEFAULT_STAGE)
    paper.add_argument("--figure-title", default="Method Overview")
    paper.add_argument("--input-label", default="Literature-grounded inputs")
    paper.add_argument("--mechanism-label", default="Reusable agent skill loop")
    paper.add_argument("--verification-label", default="Evidence gate")
    paper.add_argument("--state-label", default="Reusable state/library")
    paper.add_argument("--execution-label", default="Agent execution")
    paper.add_argument("--output-label", default="Submission-ready paper")
    paper.add_argument("--evidence-label", default="Full-scale evidence")
    paper.add_argument("--benefit-label", default="Better grounded claims")
    paper.add_argument("--failure-label", default="Overclaiming avoided")
    paper.add_argument("--caption-plan", default=None)
    paper.add_argument("--legend-plan", default=None)
    paper.add_argument("--body-reference-plan", default=None)
    paper.add_argument("--core-step-visibility-plan", default=None)
    paper.add_argument("--claimed-improvement-anchor", default=None)
    paper.add_argument("--symbol-formula-necessity", default=None)
    paper.add_argument("--semantic-contract", default=None)
    paper.add_argument("--layout-variant", default=None)
    paper.add_argument("--venue", default=None, help="venue key (e.g. AAAI, EMNLP) for the figure style persona")

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
    rev.add_argument("--venue", default=None, help="venue key (e.g. AAAI, EMNLP) for the reviewer persona")
    rev.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    rev.add_argument("--max-retries", type=int, default=_DEFAULT_MAX_RETRIES)

    sync = sub.add_parser(
        "sync-paper-metadata",
        help="synchronize IMAGE2_FIGURES.json and provenance from image-2 sidecars",
    )
    sync.add_argument("--project-root", type=Path, default=Path("."))
    sync.add_argument("--image", type=Path, required=True)
    sync.add_argument("--figure-id", required=True)
    sync.add_argument("--figure-type", default="method")
    sync.add_argument("--manifest", type=Path, default=Path("paper/figures/IMAGE2_FIGURES.json"))
    sync.add_argument("--prompt-file", type=Path)
    sync.add_argument("--sidecar", type=Path)
    sync.add_argument("--inspect-path", type=Path)
    sync.add_argument("--review-path", type=Path)
    sync.add_argument("--provenance-path", type=Path)
    sync.add_argument("--figure-studio-stage", default=PAPER_FIGURE_STUDIO_DEFAULT_STAGE)
    sync.add_argument("--allow-noncanonical-prompt", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "paper-prompt":
            kwargs: dict[str, Any] = {
                "prompt_file": args.out,
                "studio_stage": args.studio_stage,
                "figure_title": args.figure_title,
                "input_label": args.input_label,
                "mechanism_label": args.mechanism_label,
                "verification_label": args.verification_label,
                "state_label": args.state_label,
                "execution_label": args.execution_label,
                "output_label": args.output_label,
                "evidence_label": args.evidence_label,
                "benefit_label": args.benefit_label,
                "failure_label": args.failure_label,
                "force": bool(args.force),
            }
            for cli_name, helper_name in (
                ("caption_plan", "caption_plan"),
                ("legend_plan", "legend_plan"),
                ("body_reference_plan", "body_reference_plan"),
                ("core_step_visibility_plan", "core_step_visibility_plan"),
                ("claimed_improvement_anchor", "claimed_improvement_anchor"),
                ("symbol_formula_necessity", "symbol_formula_necessity"),
                ("semantic_contract", "semantic_contract"),
                ("layout_variant", "layout_variant"),
            ):
                value = getattr(args, cli_name)
                if value is not None:
                    kwargs[helper_name] = value
            if args.venue is not None:
                kwargs["venue_profile"] = get_venue_profile(args.venue)
            _print_json(write_paper_figure_prompt(**kwargs))
            return 0
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
                venue_profile=get_venue_profile(args.venue) if args.venue is not None else None,
                timeout=float(args.timeout),
                max_retries=int(args.max_retries),
            ))
            return 0
        if args.cmd == "sync-paper-metadata":
            _print_json(sync_paper_metadata(
                project_root=args.project_root,
                image=args.image,
                figure_id=args.figure_id,
                figure_type=args.figure_type,
                manifest=args.manifest,
                prompt_file=args.prompt_file,
                sidecar=args.sidecar,
                inspect_path=args.inspect_path,
                review_path=args.review_path,
                provenance_path=args.provenance_path,
                figure_studio_stage=args.figure_studio_stage,
                allow_noncanonical_prompt=bool(args.allow_noncanonical_prompt),
            ))
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        sys.stderr.write(f"argus-skill image-tool: {_redact(str(exc))}\n")
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
