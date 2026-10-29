"""Domain-neutral credential redaction for live events and transcripts."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

# A label may be carried by a prefixed name. `\b` does not open after an
# underscore, so `api_key=` redacted while `OPENAI_API_KEY=` — the form the key
# actually takes in an environment dump, a shell trace or a provider error —
# did not. This widens where a known label may start, never which labels count,
# so `FOO_TOKEN=` is read and `FOOTOKEN=` still is not. Redaction stays keyed to
# the label; guessing a secret from the shape of its value remains out of scope.
_LABEL_START = r"(?<![A-Za-z0-9])"

_HIGH_CONFIDENCE_INLINE_SECRET_PATTERN = (
    re.compile(
        r"(?i)" + _LABEL_START
        + r"((?:x[_-]?)?api[_-]?key|client[_-]?secret|private[_-]?key)\b"
        r"(['\"]?)([^\S\r\n]*[=:])"
        r"(?![^\S\r\n]*['\"]?<REDACTED:)"
        r"[^\S\r\n]*['\"]?([^\s'\",;]{8,})['\"]?"
    ),
    r"\1\2\3 <REDACTED:secret>",
)
_AMBIGUOUS_INLINE_SECRET_PATTERN = (
    re.compile(
        r"(?i)" + _LABEL_START
        + r"(secret|token|password|passwd|auth)\b"
        r"(['\"]?)([^\S\r\n]*[=:])"
        r"(?![^\S\r\n]*['\"]?<REDACTED:)"
        r"[^\S\r\n]*['\"]?([^\s'\",;]{8,})['\"]?"
    ),
    r"\1\2\3 <REDACTED:secret>",
)

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
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
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "<REDACTED:github-token>"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "<REDACTED:slack-token>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<REDACTED:aws-key>"),
    (
        # ``\s`` also matched a newline, so this was the one artifact pattern
        # that could span two lines.  The streamed scrub cuts newline-aligned
        # segments, so "bearer\n<token>" was redacted by the in-memory path
        # yet silently missed whenever a segment boundary fell between the two
        # lines.  Restricting to same-line whitespace makes the "every
        # artifact pattern matches within a single line" invariant hold for
        # ALL patterns.  Deliberate semantic change, in-memory path included:
        # a bare "bearer" line followed by a token on the next line is no
        # longer treated as a credential anywhere.
        re.compile(r"(?i)\bbearer[ \t]+[A-Za-z0-9._\-+/=]{16,}"),
        "<REDACTED:token>",
    ),
    _HIGH_CONFIDENCE_INLINE_SECRET_PATTERN,
    _AMBIGUOUS_INLINE_SECRET_PATTERN,
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"),
        r"\1<REDACTED:creds>@",
    ),
)
_SENSITIVE_ENV_NAME = re.compile(
    r"(?i)(?:^|_)(?:api_?key|token|secret|password|passwd)(?:_|$)"
)
_SENSITIVE_RECORD_KEYS = {
    "auth",
    "api_key",
    "apikey",
    "client_secret",
    "clientsecret",
    "authorization",
    "auth_token",
    "authtoken",
    "bearer_token",
    "bearertoken",
    "client_token",
    "clienttoken",
    "cookie",
    "github_token",
    "gitlab_token",
    "hf_token",
    "huggingface_token",
    "id_token",
    "idtoken",
    "oauth_token",
    "oauthtoken",
    "password",
    "passwd",
    "private_key",
    "privatekey",
    "private_token",
    "privatetoken",
    "proxy_authorization",
    "refresh_token",
    "refreshtoken",
    "secret",
    "session_token",
    "sessiontoken",
    "slack_token",
    "set_cookie",
    "token",
    "telegram_token",
    "access_token",
    "x_api_key",
}


@dataclass(frozen=True)
class SecretScrubReport:
    """Always empty; kept with :func:`scrub_recent_text_artifacts` until its caller goes."""

    scanned_files: int
    redacted_paths: tuple[str, ...]
    replacement_count: int
    errors: tuple[str, ...]
    skipped_paths: tuple[tuple[str, int], ...] = ()
    changed = False
    truncated = False


def known_secret_values(
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return high-confidence secret values already present in the process env."""
    source = os.environ if env is None else env
    values = {
        str(value)
        for key, value in source.items()
        if _SENSITIVE_ENV_NAME.search(str(key))
        and len(str(value)) >= 8
        and "\n" not in str(value)
    }
    from .paths import capabilities_root, resolve_runtime_path

    configured_vault = str(source.get("ARGUS_SKILL_CAPABILITY_VAULT") or "").strip()
    configured_root = str(source.get("ARGUS_SKILL_HOME") or "").strip()
    runtime_root = (
        resolve_runtime_path(configured_root, context="ARGUS_SKILL_HOME")
        if configured_root
        else None
    )
    vault_candidates = [capabilities_root(runtime_root) / "model_api.json"]
    if configured_vault:
        vault_candidates.insert(
            0,
            resolve_runtime_path(
                configured_vault,
                context="ARGUS_SKILL_CAPABILITY_VAULT",
            ),
        )
    for path in vault_candidates:
        if not str(path) or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        def collect(obj: Any, key: str = "") -> None:
            if isinstance(obj, dict):
                for name, value in obj.items():
                    collect(value, str(name))
            elif isinstance(obj, list):
                for value in obj:
                    collect(value, key)
            elif (
                isinstance(obj, str)
                and len(obj) >= 8
                # Same filter as the env source above: a multi-line "value"
                # is configuration prose, not a credential.
                and "\n" not in obj
                and _SENSITIVE_ENV_NAME.search(key)
            ):
                values.add(obj)

        collect(payload)
    return tuple(sorted(values, key=len, reverse=True))


REDACT_SECRETS_KNOB = "ARGUS_SKILL_REDACT_SECRETS"
_REDACTION_CACHE: dict[str, Any] = {}


def secret_redaction_enabled() -> bool:
    """Whether Argus rewrites credential-shaped text anywhere. Off by default.

    Masked text is unobservable: a Reviewer shown ``<REDACTED:secret>`` in place
    of ``os.environ.get(`` cannot verify the line, and one task spent five extra
    rounds and most of its cost disputing code neither role could read. So
    Argus leaves text as written unless an operator opts in. Detection
    (:func:`contains_secret`) is unaffected by this switch.

    Called on every event, so the persisted value is re-read only when the knob
    store file changes; the environment is checked on every call.
    """
    from .knobs import _TRUE_VALUES

    explicit = str(os.environ.get(REDACT_SECRETS_KNOB, "") or "").strip().lower()
    if explicit:
        return explicit in _TRUE_VALUES
    from .knob_store import KnobStoreCorruptError, config_path, read_persisted_knobs

    try:
        path = config_path()
        try:
            info = path.stat()
            stamp: Any = (info.st_mtime_ns, info.st_size, info.st_ino)
        except OSError:
            stamp = None
        cached = _REDACTION_CACHE.get(str(path))
        if cached is not None and cached[0] == stamp:
            return cached[1]
        saved = str(read_persisted_knobs().get(REDACT_SECRETS_KNOB, "") or "")
        enabled = saved.strip().lower() in _TRUE_VALUES
        _REDACTION_CACHE[str(path)] = (stamp, enabled)
        return enabled
    except KnobStoreCorruptError:  # an unreadable store keeps the default
        return False


def _redact_text(text: str, *, known_values: Iterable[str] = ()) -> str:
    if not isinstance(text, str) or not text:
        return text
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if parsed is not None:
            redacted_record = _redact_record(parsed, known_values=known_values)
            if redacted_record != parsed:
                rendered = json.dumps(
                    redacted_record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if text.endswith("\n"):
                    rendered += "\n"
                return rendered
        elif "\n" in text:
            rendered_lines: list[str] = []
            changed_records = 0
            jsonl_valid = True
            for line in text.splitlines(keepends=True):
                content = line.rstrip("\r\n")
                ending = line[len(content):]
                if not content.strip():
                    rendered_lines.append(line)
                    continue
                try:
                    record = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    jsonl_valid = False
                    break
                redacted_record = _redact_record(record, known_values=known_values)
                if redacted_record != record:
                    changed_records += 1
                rendered_lines.append(
                    json.dumps(
                        redacted_record,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + ending
                )
            if jsonl_valid and changed_records:
                return "".join(rendered_lines)
    out = text
    for value in sorted(
        {str(value) for value in known_values if len(str(value)) >= 8},
        key=len,
        reverse=True,
    ):
        out = out.replace(value, "<REDACTED:known-secret>")
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _redact_record(obj: Any, *, known_values: Iterable[str] = ()) -> Any:
    if isinstance(obj, str):
        return _redact_text(obj, known_values=known_values)
    if isinstance(obj, (list, set)):
        return [_redact_record(value, known_values=known_values) for value in obj]
    if isinstance(obj, tuple):
        return tuple(_redact_record(value, known_values=known_values) for value in obj)
    if isinstance(obj, dict):
        redacted: dict[Any, Any] = {}
        for key, value in obj.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            sensitive_key = (
                normalized_key in _SENSITIVE_RECORD_KEYS
                or normalized_key.endswith(
                    ("apikey", "api_key", "password", "passwd", "secret")
                )
            )
            if sensitive_key and isinstance(value, str):
                redacted[key] = (
                    "<REDACTED:secret>" if value else value
                )
            else:
                redacted[key] = _redact_record(value, known_values=known_values)
        return redacted
    return obj


def scrub_recent_text_artifacts(
    root: Path,  # noqa: ARG001
    *,
    modified_since: float,  # noqa: ARG001
    known_values: Iterable[str] = (),  # noqa: ARG001
) -> SecretScrubReport:
    """Retired: workspace files are no longer rewritten. Kept until callers go."""
    return SecretScrubReport(scanned_files=0, redacted_paths=(), replacement_count=0, errors=())


def redact_secrets_text(text: str, *, known_values: Iterable[str] = ()) -> str:
    """Masked copy of ``text``; ``text`` itself while redaction is off."""
    if not secret_redaction_enabled():
        return text
    return _redact_text(text, known_values=known_values)


def redact_secrets_record(obj: Any, *, known_values: Iterable[str] = ()) -> Any:
    """Masked copy of a JSON-like record; ``obj`` itself while redaction is off."""
    if not secret_redaction_enabled():
        return obj
    return _redact_record(obj, known_values=known_values)


def contains_secret(obj: Any, *, known_values: Iterable[str] = ()) -> bool:
    """Whether ``obj`` holds credential-shaped content, whatever the switch says.

    Callers that must refuse or flag such content (training capture, for
    instance) use this instead of comparing a redacted copy with the original,
    so turning redaction off never turns their check off with it.
    """
    return _redact_record(obj, known_values=known_values) != obj
