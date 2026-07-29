"""Domain-neutral credential redaction for live events and changed artifacts."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

_HIGH_CONFIDENCE_INLINE_SECRET_PATTERN = (
    re.compile(
        r"(?i)\b((?:x[_-]?)?api[_-]?key|client[_-]?secret|private[_-]?key)\b"
        r"(['\"]?)([^\S\r\n]*[=:])"
        r"(?![^\S\r\n]*['\"]?<REDACTED:)"
        r"[^\S\r\n]*['\"]?([^\s'\",;]{8,})['\"]?"
    ),
    r"\1\2\3 <REDACTED:secret>",
)
_AMBIGUOUS_INLINE_SECRET_PATTERN = (
    re.compile(
        r"(?i)\b(secret|token|password|passwd|auth)\b"
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
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "<REDACTED:openai-key>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "<REDACTED:github-token>"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "<REDACTED:slack-token>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<REDACTED:aws-key>"),
    (
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{16,}"),
        "<REDACTED:token>",
    ),
    _HIGH_CONFIDENCE_INLINE_SECRET_PATTERN,
    _AMBIGUOUS_INLINE_SECRET_PATTERN,
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"),
        r"\1<REDACTED:creds>@",
    ),
)
_ARTIFACT_SECRET_PATTERNS = tuple(
    item
    for item in _SECRET_PATTERNS
    if item is not _AMBIGUOUS_INLINE_SECRET_PATTERN
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
# Artifact scrubbing runs after an Engineer turn and therefore sees benchmark
# rows, replay fixtures, and scientific result packets.  Those schemas often
# use generic task-state names such as ``access_token`` or ``password`` for
# synthetic values.  Treating the field name alone as proof of a credential
# corrupts immutable evidence and invalidates its hashes.  Live event payloads
# keep the stricter policy above; on-disk artifact scrubbing only trusts keys
# that identify provider credentials or protocol headers with high confidence.
_HIGH_CONFIDENCE_ARTIFACT_RECORD_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer_token",
    "bearertoken",
    "client_secret",
    "clientsecret",
    "cookie",
    "github_token",
    "gitlab_token",
    "hf_token",
    "huggingface_token",
    "private_key",
    "private_token",
    "privatekey",
    "privatetoken",
    "proxy_authorization",
    "set_cookie",
    "slack_token",
    "telegram_token",
    "x_api_key",
}
_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".argus",
    ".argus_subagents",
    "venv",
    "__pycache__",
    "node_modules",
}
_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cue",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".py",
    ".rs",
    ".sh",
    ".ts",
    ".tsx",
}
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_MAX_SCANNED_FILES = 10_000
# These trees contain immutable upstream bytes rather than artifacts authored
# during an engineer round. Scanning them both wastes the bounded scan budget
# and can corrupt structured upstream data whose schema legitimately uses
# credential-like keys such as ``token``.
_NON_ARTIFACT_TREE_PARTS = {
    ("code", "references"),
    ("experiments", "comparator_worker_env"),
    ("third_party", "reference_sources"),
    ("third_party", "runtime_deps"),
}
_KNOWN_SECRET_ONLY_TREE_PREFIXES = {
    ("models", "huggingface"),
}
_TEXT_ARTIFACT_SUFFIXES = {
    "",
    ".csv",
    ".env",
    ".headers",
    ".http",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".txt",
    ".tsv",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class SecretScrubReport:
    scanned_files: int
    redacted_paths: tuple[str, ...]
    replacement_count: int
    errors: tuple[str, ...]
    truncated: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.redacted_paths)


class ArtifactChangedDuringScrubError(OSError):
    pass


def _git_changed_paths(root: Path) -> set[str] | None:
    """Return Git-visible worktree changes, or ``None`` outside a usable repo."""
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root}",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    records = result.stdout.split(b"\0")
    changed: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        text = record.decode("utf-8", errors="surrogateescape")
        if len(text) < 4:
            continue
        status = text[:2]
        relative = text[3:]
        path = Path(relative)
        if not path.is_absolute() and ".." not in path.parts:
            changed.add(path.as_posix())
        if "R" in status or "C" in status:
            index += 1
    return changed


def _is_non_artifact_tree(parts: tuple[str, ...]) -> bool:
    if parts in _NON_ARTIFACT_TREE_PARTS:
        return True
    return (
        len(parts) >= 5
        and parts[:2] == ("experiments", "runs")
        and parts[-2:] == ("acquisition", "anchors")
    )


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
                and _SENSITIVE_ENV_NAME.search(key)
            ):
                values.add(obj)

        collect(payload)
    return tuple(sorted(values, key=len, reverse=True))


def redact_secrets_text_with_count(
    text: str,
    *,
    known_values: Iterable[str] = (),
    include_patterns: bool = True,
    redact_ambiguous_record_keys: bool = True,
) -> tuple[str, int]:
    if not isinstance(text, str) or not text:
        return text, 0
    stripped = text.strip()
    if include_patterns and stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if parsed is not None:
            redacted_record = redact_secrets_record(
                parsed,
                known_values=known_values,
                redact_ambiguous_record_keys=redact_ambiguous_record_keys,
            )
            if redacted_record != parsed:
                rendered = json.dumps(
                    redacted_record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if text.endswith("\n"):
                    rendered += "\n"
                return rendered, 1
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
                redacted_record = redact_secrets_record(
                    record,
                    known_values=known_values,
                    redact_ambiguous_record_keys=redact_ambiguous_record_keys,
                )
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
                return "".join(rendered_lines), changed_records
    out = text
    replacements = 0
    for value in sorted(
        {str(value) for value in known_values if len(str(value)) >= 8},
        key=len,
        reverse=True,
    ):
        count = out.count(value)
        if count:
            out = out.replace(value, "<REDACTED:known-secret>")
            replacements += count
    if include_patterns:
        patterns = (
            _SECRET_PATTERNS
            if redact_ambiguous_record_keys
            else _ARTIFACT_SECRET_PATTERNS
        )
        for pattern, replacement in patterns:
            out, count = pattern.subn(replacement, out)
            replacements += count
    return out, replacements if out != text else 0


def redact_secrets_text(
    text: str,
    *,
    known_values: Iterable[str] = (),
    redact_ambiguous_record_keys: bool = True,
) -> str:
    return redact_secrets_text_with_count(
        text,
        known_values=known_values,
        redact_ambiguous_record_keys=redact_ambiguous_record_keys,
    )[0]


def redact_secrets_record(
    obj: Any,
    *,
    known_values: Iterable[str] = (),
    redact_ambiguous_record_keys: bool = True,
) -> Any:
    if isinstance(obj, str):
        return redact_secrets_text(
            obj,
            known_values=known_values,
            redact_ambiguous_record_keys=redact_ambiguous_record_keys,
        )
    if isinstance(obj, list):
        return [
            redact_secrets_record(
                value,
                known_values=known_values,
                redact_ambiguous_record_keys=redact_ambiguous_record_keys,
            )
            for value in obj
        ]
    if isinstance(obj, tuple):
        return tuple(
            redact_secrets_record(
                value,
                known_values=known_values,
                redact_ambiguous_record_keys=redact_ambiguous_record_keys,
            )
            for value in obj
        )
    if isinstance(obj, set):
        return [
            redact_secrets_record(
                value,
                known_values=known_values,
                redact_ambiguous_record_keys=redact_ambiguous_record_keys,
            )
            for value in obj
        ]
    if isinstance(obj, dict):
        redacted: dict[Any, Any] = {}
        for key, value in obj.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            strict_sensitive_key = (
                normalized_key in _SENSITIVE_RECORD_KEYS
                or normalized_key.endswith(
                    ("apikey", "api_key", "password", "passwd", "secret")
                )
            )
            artifact_sensitive_key = (
                normalized_key in _HIGH_CONFIDENCE_ARTIFACT_RECORD_KEYS
                or normalized_key.endswith(
                    ("apikey", "api_key", "client_secret", "private_key")
                )
            )
            sensitive_key = (
                strict_sensitive_key
                if redact_ambiguous_record_keys
                else artifact_sensitive_key
            )
            if sensitive_key and isinstance(value, str):
                redacted[key] = (
                    "<REDACTED:secret>" if value else value
                )
            else:
                redacted[key] = redact_secrets_record(
                    value,
                    known_values=known_values,
                    redact_ambiguous_record_keys=redact_ambiguous_record_keys,
                )
        return redacted
    return obj


def _write_redacted(
    path: Path,
    text: str,
    mode: int,
    *,
    expected_raw: bytes,
) -> None:
    tmp = path.with_name(
        f".{path.name}.secret-redact-{os.getpid()}-{time.time_ns()}"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, stat.S_IMODE(mode))
        if path.read_bytes() != expected_raw:
            raise ArtifactChangedDuringScrubError(
                "artifact changed while secret guard was scanning it"
            )
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def scrub_recent_text_artifacts(
    root: Path,
    *,
    modified_since: float,
    known_values: Iterable[str] = (),
) -> SecretScrubReport:
    """Redact secrets from text files changed during the current engineer round."""
    root = Path(root).expanduser().resolve()
    redacted_paths: list[str] = []
    errors: list[str] = []
    replacement_count = 0
    scanned_files = 0
    truncated = False
    git_changed_paths = _git_changed_paths(root)
    def _walk_error(exc: OSError) -> None:
        filename = str(getattr(exc, "filename", "") or ".")
        errors.append(f"{filename}: {type(exc).__name__}")

    walker = os.walk(root, topdown=True, onerror=_walk_error)
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = [name for name in dirnames if name not in _IGNORE_DIRS]
        try:
            rel_dir_parts = Path(dirpath).relative_to(root).parts
        except ValueError:
            rel_dir_parts = ()
        if _is_non_artifact_tree(rel_dir_parts):
            dirnames[:] = []
            continue
        for filename in filenames:
            if scanned_files >= _MAX_SCANNED_FILES:
                truncated = True
                break
            path = Path(dirpath) / filename
            try:
                if path.is_symlink():
                    continue
                relative_path = path.relative_to(root)
                if (
                    git_changed_paths is not None
                    and relative_path.as_posix() not in git_changed_paths
                ):
                    continue
                metadata = path.stat()
                if (
                    git_changed_paths is None
                    and max(metadata.st_mtime, metadata.st_ctime)
                    < modified_since - 1.0
                ):
                    continue
                if metadata.st_size > _MAX_ARTIFACT_BYTES:
                    if (
                        path.suffix.casefold() in _TEXT_ARTIFACT_SUFFIXES
                        or path.suffix.casefold() in _SOURCE_SUFFIXES
                    ):
                        truncated = True
                    continue
                raw = path.read_bytes()
                if b"\0" in raw:
                    continue
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                if (
                    path.suffix.casefold() in _TEXT_ARTIFACT_SUFFIXES
                    or path.suffix.casefold() in _SOURCE_SUFFIXES
                ):
                    errors.append(
                        f"{path.relative_to(root)}: UnicodeDecodeError"
                    )
                continue
            except OSError as exc:
                try:
                    relative = str(relative_path)
                except ValueError:
                    relative = path.name
                errors.append(f"{relative}: {type(exc).__name__}")
                continue
            scanned_files += 1
            known_secret_only = any(
                rel_dir_parts[: len(prefix)] == prefix
                for prefix in _KNOWN_SECRET_ONLY_TREE_PREFIXES
            )
            include_patterns = (
                not known_secret_only
                and path.suffix.casefold() not in _SOURCE_SUFFIXES
            )
            redacted, count = redact_secrets_text_with_count(
                text,
                known_values=known_values,
                include_patterns=include_patterns,
                redact_ambiguous_record_keys=False,
            )
            if not count or redacted == text:
                continue
            relative = str(path.relative_to(root))
            try:
                _write_redacted(
                    path,
                    redacted,
                    metadata.st_mode,
                    expected_raw=raw,
                )
            except OSError as exc:
                errors.append(f"{relative}: {type(exc).__name__}")
                continue
            redacted_paths.append(relative)
            replacement_count += count
        if truncated:
            break
    return SecretScrubReport(
        scanned_files=scanned_files,
        redacted_paths=tuple(redacted_paths),
        replacement_count=replacement_count,
        errors=tuple(errors),
        truncated=truncated,
    )
