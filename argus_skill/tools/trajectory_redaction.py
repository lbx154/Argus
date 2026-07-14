"""Trajectory redaction — scrub secrets/PII before research data leaves the box.

EN: The B-line product (sellable research trajectories) must not carry API keys,
credentials, absolute user paths, or emails. Today only telemetry command-lines
are scrubbed (``life/telemetry._redact_arg``); this is the reusable, general
pass over ARBITRARY trajectory text / JSON records the sellable export needs.
Fail-soft everywhere — redaction must never break a run or an export.

中文：B 线产品（可售研究轨迹）绝不能带 API key、凭证、绝对用户路径、邮箱。目前只有
telemetry 命令行被脱敏（``life/telemetry._redact_arg``）；这是可售导出所需的、对任意
轨迹文本 / JSON 记录通用的可复用脱敏。全程失败即原样返回——脱敏绝不能弄坏一次运行或导出。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.secret_guard import redact_secrets_text as _redact_live_secrets_text

# High-risk secret patterns → a TYPED placeholder, so a downstream reader still
# sees "a token was here" without the value. Ordered specific → generic.
# 高危密钥模式 → 带类型的占位符；读者知道"这里有个 token"但看不到值；由具体到通用。
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?im)^([^\S\r\n]*(?:authorization|proxy-authorization)"
            r"[^\S\r\n]*:)(?![^\S\r\n]*<REDACTED:)[^\r\n]+(\r?)$"
        ),
        r"\1 <REDACTED:token>\2",
    ),
    (
        re.compile(
            r"(?im)^([^\S\r\n]*(?:x-api-key|api-key|cookie|set-cookie)"
            r"[^\S\r\n]*:)(?![^\S\r\n]*<REDACTED:)[^\r\n]+(\r?)$"
        ),
        r"\1 <REDACTED:secret>\2",
    ),
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "<REDACTED:openai-key>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "<REDACTED:github-token>"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "<REDACTED:slack-token>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<REDACTED:aws-key>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer <REDACTED:token>"),
    # `key=VALUE` / `"api_key": "VALUE"` assignments (needs a = or : separator,
    # so prose like "the token was invalid" is NOT matched). / 赋值式，需 =/:
    # 分隔，故散文里的 "token" 不会被误伤。
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|auth)\b"
            r"(['\"]?)([^\S\r\n]*[=:])"
            r"(?![^\S\r\n]*['\"]?<REDACTED:)"
            r"[^\S\r\n]*['\"]?([A-Za-z0-9._\-]{8,})['\"]?"
        ),
        r"\1\2\3 <REDACTED:secret>",
    ),
    # URL credentials: scheme://user:pass@host → strip the user:pass.
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"),
        r"\1<REDACTED:creds>@",
    ),
]

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def redact_text(text: str, *, home: str | None = None) -> str:
    """Scrub secrets / PII / the absolute home path from one string.

    EN: Fail-soft — on any error (or non-str input) returns the input unchanged;
    never raises. ``home`` (default ``Path.home()``) is collapsed to ``~`` so the
    export is not a machine/user fingerprint.
    中文：脱敏单个字符串（密钥/隐私/绝对 home 路径）；失败或非字符串原样返回、绝不抛异常；
    ``home``（默认 ``Path.home()``）折叠成 ``~``，避免导出成为机器/用户指纹。
    """
    if not isinstance(text, str) or not text:
        return text
    try:
        out = _redact_live_secrets_text(text)
        out = _EMAIL.sub("<REDACTED:email>", out)
        h = home if home is not None else str(Path.home())
        if h and h != "/":
            out = out.replace(h, "~")
        return out
    except Exception:  # noqa: BLE001 — redaction must never break a run/export
        return text


def redact_record(obj: Any, *, home: str | None = None) -> Any:
    """Recursively redact every string VALUE in a JSON-like record.

    EN: Preserves structure; dict KEYS are left intact (they are field names, not
    secrets). Fail-soft via ``redact_text``.
    中文：递归脱敏 JSON 记录里的每个字符串"值"，保结构；dict 的 key 不动（字段名非密钥）。
    """
    if isinstance(obj, str):
        return redact_text(obj, home=home)
    if isinstance(obj, list):
        return [redact_record(v, home=home) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_record(v, home=home) for k, v in obj.items()}
    return obj
