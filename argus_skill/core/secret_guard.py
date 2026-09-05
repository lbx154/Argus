"""Domain-neutral credential redaction for live events and changed artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
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
    "app_secret",
    "appsecret",
    "feishu_app_secret",
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
# ``_MAX_ARTIFACT_BYTES`` is not a coverage cap: artifacts at or below it are
# read whole into memory, larger ones are scanned in newline-aligned streamed
# chunks.  ``_HARD_MAX_ARTIFACT_BYTES`` is the absolute guardrail; text
# artifacts above it are skipped but always surfaced via
# ``SecretScrubReport.skipped_paths`` — never silently.
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_HARD_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
_STREAM_CHUNK_BYTES = 8 * 1024 * 1024
# Cap on a single line accumulated by the streamed scan.  Without it, a huge
# artifact with no newline at all (packed JSON, minified bundles) forced the
# whole file into memory as one "line".  A file whose line exceeds this is
# skipped and surfaced via ``skipped_paths`` — never silently.
_MAX_STREAM_LINE_BYTES = 64 * 1024 * 1024
# Wall-clock budget for ALL streamed (oversized-artifact) scans within one
# scrub call, i.e. one engineer round.  Streamed artifacts are multi-GiB in
# production workspaces; without a budget a single round could stall for the
# whole scan.  Once the accumulated streaming time exceeds this, remaining
# oversized artifacts are surfaced via ``skipped_paths`` instead of scanned.
_STREAMING_SCAN_TIME_BUDGET_SECONDS = 60.0
# When the file-count budget is exhausted, this many remaining candidate
# files are still enumerated into ``skipped_paths`` individually; the rest
# collapse into one "+N more files" summary entry so the report stays bounded.
_BUDGET_SKIP_ENUMERATION_LIMIT = 50
# Only the head of an oversized artifact is sniffed for NUL bytes to decide
# text-vs-binary; suffix whitelists missed real text formats (.ipynb, .html)
# and skipped them with zero trace.
_TEXT_SNIFF_BYTES = 8192
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
    # ``(relative_path, size_bytes)`` for every text artifact the scrub did
    # NOT scan (hard size guardrail, file-count budget, or content that turned
    # binary/undecodable mid-stream).  A skip is never silent.
    skipped_paths: tuple[tuple[str, int], ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.redacted_paths)

    @property
    def truncated(self) -> bool:
        """Backwards-compatible alias: coverage was incomplete."""
        return bool(self.skipped_paths)


class ArtifactChangedDuringScrubError(OSError):
    pass


class _BinaryContentError(ValueError):
    """A streamed artifact revealed NUL bytes after passing the head sniff."""


class _OversizedLineError(ValueError):
    """A streamed artifact holds a single line above ``_MAX_STREAM_LINE_BYTES``."""


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


def _is_hf_content_cache_tree(parts: tuple[str, ...]) -> bool:
    """True inside a HuggingFace content-addressed cache tree.

    These trees hold immutable upstream bytes (run-08's workspace carried
    4.5 GiB of datasets blobs under ``results/*/cache/huggingface/*/blobs/*``)
    that a round never authored, so scanning them burns the file and time
    budgets for nothing.  Unlike ``_KNOWN_SECRET_ONLY_TREE_PREFIXES`` — a
    root-anchored prefix table — these caches appear at arbitrary depths, so
    this matches path SEGMENTS anywhere in the relative directory path:
    either an adjacent ``cache/huggingface`` pair, or a content-addressed
    ``blobs`` directory directly under a hub-layout repo directory
    (``models--*``/``datasets--*``/``spaces--*``).
    """
    for index in range(len(parts) - 1):
        if parts[index] == "cache" and parts[index + 1] == "huggingface":
            return True
    for index in range(1, len(parts)):
        if parts[index] == "blobs" and parts[index - 1].startswith(
            ("models--", "datasets--", "spaces--")
        ):
            return True
    return False


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
                # is configuration prose, not a credential, and replacing it
                # would rewrite line structure — the streamed scrub's
                # newline-aligned coverage proof assumes known secret values
                # never contain a newline.
                and "\n" not in obj
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
        # ``text`` may already contain CRLF from a Windows artifact.  The
        # default text writer translates every ``\n`` again on Windows, which
        # turns an existing ``\r\n`` into ``\r\r\n`` and grows a blank line on
        # every scrub pass.  The scrubber must preserve the source newline
        # bytes while changing only the secret value.
        tmp.write_text(text, encoding="utf-8", newline="")
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


def _sniff_is_binary(path: Path) -> bool:
    """NUL-sniff the head of a file; NUL bytes mark a binary artifact."""
    with path.open("rb") as handle:
        return b"\0" in handle.read(_TEXT_SNIFF_BYTES)


def _iter_stream_segments(source: Any, name: str) -> Any:
    """Yield newline-aligned byte segments that exactly partition ``source``.

    Every artifact pattern matches within a single line and known secret
    values never contain a newline, so cutting each chunk at its last newline
    and carrying the partial tail line into the next chunk scans every
    possible match without overlap windows.  The carry lives in one
    ``bytearray`` grown with ``extend`` — rebuilding ``carry + chunk`` bytes
    objects re-copied the whole carry on every chunk, which made one huge
    line O(n^2) in memcpy and doubled peak memory.  A single line longer than
    ``_MAX_STREAM_LINE_BYTES`` aborts with :class:`_OversizedLineError`
    instead of accumulating without bound.
    """
    buffer = bytearray()
    while True:
        chunk = source.read(_STREAM_CHUNK_BYTES)
        if b"\0" in chunk:
            raise _BinaryContentError(name)
        buffer.extend(chunk)
        if chunk:
            boundary = buffer.rfind(b"\n") + 1
            if not boundary:
                if len(buffer) > _MAX_STREAM_LINE_BYTES:
                    raise _OversizedLineError(name)
                continue
            segment = bytes(buffer[:boundary])
            # In-place delete keeps only the partial tail line (< one chunk),
            # so the carry never re-copies already-emitted bytes.
            del buffer[:boundary]
        else:
            segment = bytes(buffer)
            buffer.clear()
        if segment:
            yield segment
        if not chunk:
            return


def _scrub_streaming(
    path: Path,
    mode: int,
    *,
    known_values: Iterable[str] = (),
    include_patterns: bool = True,
) -> int:
    """Scrub an oversized artifact in newline-aligned streamed chunks.

    Two passes: the first only scans and counts, and a clean artifact — the
    overwhelmingly common case — costs one read and zero writes.  Only when
    the first pass found a hit does the second pass rewrite into a temp file.
    A blake2b digest of the scanned bytes guards the gap: the rewrite pass
    must re-read exactly the bytes the scan graded (compared after the
    rewrite and re-checked after ``chmod``), otherwise the artifact changed
    concurrently and the scrub refuses to replace it.
    Returns the replacement count; 0 leaves the artifact untouched.
    """
    scan_digest = hashlib.blake2b()
    hit_count = 0
    with path.open("rb") as source:
        for segment in _iter_stream_segments(source, path.name):
            scan_digest.update(segment)
            _, count = redact_secrets_text_with_count(
                segment.decode("utf-8"),
                known_values=known_values,
                include_patterns=include_patterns,
                redact_ambiguous_record_keys=False,
            )
            hit_count += count
    if not hit_count:
        return 0
    tmp = path.with_name(
        f".{path.name}.secret-redact-{os.getpid()}-{time.time_ns()}"
    )
    replacement_count = 0
    rewrite_digest = hashlib.blake2b()
    try:
        with path.open("rb") as source, tmp.open("wb") as sink:
            for segment in _iter_stream_segments(source, path.name):
                rewrite_digest.update(segment)
                text = segment.decode("utf-8")
                redacted, count = redact_secrets_text_with_count(
                    text,
                    known_values=known_values,
                    include_patterns=include_patterns,
                    redact_ambiguous_record_keys=False,
                )
                if count:
                    replacement_count += count
                    if (
                        text.endswith("\r\n")
                        and redacted.endswith("\n")
                        and not redacted.endswith("\r\n")
                    ):
                        # A segment that is exactly one complete JSON document
                        # is re-rendered by the JSON path, which restores only
                        # a bare "\n"; put the source CRLF back so the scrub
                        # changes secret values and nothing else.
                        redacted = redacted[:-1] + "\r\n"
                    # Binary-level write keeps the source newline bytes
                    # (CRLF stays CRLF) exactly like ``_write_redacted``.
                    sink.write(redacted.encode("utf-8"))
                else:
                    sink.write(segment)
        if rewrite_digest.digest() != scan_digest.digest():
            raise ArtifactChangedDuringScrubError(
                "artifact changed while secret guard was scanning it"
            )
        os.chmod(tmp, stat.S_IMODE(mode))
        recheck_digest = hashlib.blake2b()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(_STREAM_CHUNK_BYTES), b""):
                recheck_digest.update(chunk)
        if recheck_digest.digest() != scan_digest.digest():
            raise ArtifactChangedDuringScrubError(
                "artifact changed while secret guard was scanning it"
            )
        os.replace(tmp, path)
        return replacement_count
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
    skipped_paths: list[tuple[str, int]] = []
    file_budget_exhausted = False
    budget_skip_count = 0
    streaming_seconds_spent = 0.0
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
        if _is_hf_content_cache_tree(rel_dir_parts):
            dirnames[:] = []
            continue
        for filename in filenames:
            if scanned_files >= _MAX_SCANNED_FILES:
                file_budget_exhausted = True
            path = Path(dirpath) / filename
            if file_budget_exhausted:
                # Budget exhausted: keep walking so EVERY remaining candidate
                # is attributable rather than only the file we stopped at.
                # The same cheap stat-level candidate filters apply as on the
                # scan path, so the report names files that would actually
                # have been scanned.  Enumeration is capped; the overflow is
                # rolled into one "+N more files" summary entry below.
                try:
                    if path.is_symlink():
                        continue
                    unscanned_relative = path.relative_to(root).as_posix()
                    if (
                        git_changed_paths is not None
                        and unscanned_relative not in git_changed_paths
                    ):
                        continue
                    unscanned_stat = path.stat()
                    if (
                        git_changed_paths is None
                        and max(unscanned_stat.st_mtime, unscanned_stat.st_ctime)
                        < modified_since - 1.0
                    ):
                        continue
                    record = (unscanned_relative, unscanned_stat.st_size)
                except (OSError, ValueError):
                    record = (path.name, 0)
                if budget_skip_count < _BUDGET_SKIP_ENUMERATION_LIMIT:
                    skipped_paths.append(record)
                budget_skip_count += 1
                continue
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
                known_secret_only = any(
                    rel_dir_parts[: len(prefix)] == prefix
                    for prefix in _KNOWN_SECRET_ONLY_TREE_PREFIXES
                )
                include_patterns = (
                    not known_secret_only
                    and path.suffix.casefold() not in _SOURCE_SUFFIXES
                )
                if metadata.st_size > _MAX_ARTIFACT_BYTES:
                    relative = relative_path.as_posix()
                    if _sniff_is_binary(path):
                        # Binary artifacts (weights, archives) stay out of
                        # the text scrub, mirroring the whole-file NUL check
                        # taken by the in-memory path below.
                        continue
                    if metadata.st_size > _HARD_MAX_ARTIFACT_BYTES:
                        skipped_paths.append((relative, metadata.st_size))
                        continue
                    if streaming_seconds_spent > _STREAMING_SCAN_TIME_BUDGET_SECONDS:
                        # The round's streaming time budget is spent: surface
                        # the remaining oversized artifact instead of scanning.
                        skipped_paths.append((relative, metadata.st_size))
                        continue
                    stream_started = time.monotonic()
                    try:
                        count = _scrub_streaming(
                            path,
                            metadata.st_mode,
                            known_values=known_values,
                            include_patterns=include_patterns,
                        )
                    except _BinaryContentError:
                        # Passed the head sniff but revealed NUL bytes later:
                        # too risky to rewrite, too text-like to skip quietly.
                        skipped_paths.append((relative, metadata.st_size))
                        continue
                    except _OversizedLineError:
                        # A single line above the carry cap cannot be scanned
                        # newline-aligned; accumulating it whole is exactly
                        # the OOM path the cap exists to prevent.  The reason
                        # rides in the path entry so the operator-facing note
                        # says why coverage stopped here.
                        skipped_paths.append(
                            (f"{relative} (oversized line)", metadata.st_size)
                        )
                        continue
                    except UnicodeDecodeError:
                        errors.append(f"{relative}: UnicodeDecodeError")
                        continue
                    finally:
                        streaming_seconds_spent += time.monotonic() - stream_started
                    scanned_files += 1
                    if count:
                        redacted_paths.append(relative)
                        replacement_count += count
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
                        f"{path.relative_to(root).as_posix()}: UnicodeDecodeError"
                    )
                continue
            except OSError as exc:
                try:
                    relative = relative_path.as_posix()
                except ValueError:
                    relative = path.name
                errors.append(f"{relative}: {type(exc).__name__}")
                continue
            scanned_files += 1
            redacted, count = redact_secrets_text_with_count(
                text,
                known_values=known_values,
                include_patterns=include_patterns,
                redact_ambiguous_record_keys=False,
            )
            if not count or redacted == text:
                continue
            relative = path.relative_to(root).as_posix()
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
    if budget_skip_count > _BUDGET_SKIP_ENUMERATION_LIMIT:
        # Candidates beyond the enumeration cap collapse into one summary
        # entry (size 0: it names a count, not a file) so exhaustion of the
        # file budget can never itself produce an unbounded report.
        skipped_paths.append(
            (f"+{budget_skip_count - _BUDGET_SKIP_ENUMERATION_LIMIT} more files", 0)
        )
    return SecretScrubReport(
        scanned_files=scanned_files,
        redacted_paths=tuple(redacted_paths),
        replacement_count=replacement_count,
        errors=tuple(errors),
        skipped_paths=tuple(skipped_paths),
    )
