"""Deterministic file protocol for bounded Chemistry Playground work."""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

PROTOCOL_VERSION = 1
PLAYGROUND_RELATIVE_ROOT = Path("research") / "chem_playground"
IDEA_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

WORKING_STATUSES = (
    "speculative",
    "literature_grounded",
    "computationally_probed",
    "reviewer_candidate",
)
TERMINAL_STATUSES = ("promoted", "retained", "falsified", "blocked")
ALL_STATUSES = frozenset((*WORKING_STATUSES, *TERMINAL_STATUSES))
REVIEWER_RECOMMENDATIONS = frozenset({"pending", *TERMINAL_STATUSES})
EVIDENCE_CLASSES = frozenset(
    {
        "retrieved",
        "curated",
        "predicted",
        "computed",
        "simulated",
        "measured",
        "inferred",
        "negative",
        "failed",
    }
)

QUESTION_SECTIONS = (
    "Scientific question",
    "Hypothesis",
    "Explicit assumptions",
    "Competing explanations",
    "Falsifiable predictions",
    "Known evidence",
    "Missing evidence",
    "Allowed probe budget",
    "Safety and authorization",
    "Non-goals",
)
RESULT_SECTIONS = (
    "Summary",
    "Work performed",
    "Evidence ledger",
    "Computational probes",
    "Uncertainty and applicability",
    "Negative and failed results",
    "Competing explanations revisited",
    "Next discriminating test",
    "Reviewer decision basis",
    "Promotion boundary",
    "References",
)

_VALID_TRANSITIONS = {
    "speculative": {
        "literature_grounded",
        "computationally_probed",
        "reviewer_candidate",
    },
    "literature_grounded": {"computationally_probed", "reviewer_candidate"},
    "computationally_probed": {"reviewer_candidate"},
    "reviewer_candidate": set(TERMINAL_STATUSES),
}
_EVIDENCE_LINE = re.compile(
    r"^\s*-\s*\[(?P<class>[a-z_]+)\]\s+(?P<ref>\S+)"
    r"\s+-\s+(?P<claim>.+?)\s*$",
    re.IGNORECASE,
)
_REFERENCE_LINE = re.compile(
    r"^\s*-\s*\[reference\]\s+(?P<ref>\S+)\s+-\s+(?P<purpose>.+?)\s*$",
    re.IGNORECASE,
)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")
_INPUT_EVIDENCE_CLASSES = {"retrieved", "curated", "measured"}
_OUTPUT_EVIDENCE_CLASSES = {
    "predicted",
    "computed",
    "simulated",
    "inferred",
    "negative",
    "failed",
}
_PLACEHOLDER_HOSTS = {
    "example.com",
    "example.org",
    "example.net",
    "localhost",
}
_RESERVED_HOST_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.org",
    ".example.net",
    ".invalid",
    ".test",
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
    ".onion",
)
_PLACEHOLDER_REVIEWERS = {
    "",
    "pending",
    "tbd",
    "none",
    "n/a",
    "unknown",
    "unassigned",
    "placeholder",
    "reviewer",
    "test",
    "example",
    "demo",
}
_PLACEHOLDER_REVIEWER_TOKENS = {
    "",
    "pending",
    "tbd",
    "none",
    "na",
    "unknown",
    "unassigned",
    "placeholder",
    "reviewer",
    "test",
    "example",
    "demo",
}
_INITIAL_MUTABLE_SECTIONS = {
    "Explicit assumptions": (
        "None recorded yet; add them before advancing beyond `speculative`."
    ),
    "Competing explanations": (
        "None recorded yet; add at least one plausible alternative when available."
    ),
    "Falsifiable predictions": (
        "Define an observable outcome that would weaken or falsify the hypothesis."
    ),
    "Known evidence": "None recorded.",
    "Missing evidence": "Identify the evidence needed to discriminate explanations.",
    "Allowed probe budget": (
        "Define compute, wall-time, query, and iteration ceilings before execution."
    ),
    "Summary": "No probe has been completed.",
    "Work performed": "Candidate initialized only.",
    "Computational probes": "None completed.",
    "Uncertainty and applicability": (
        "State uncertainty, assumptions, sensitivity, and the valid domain."
    ),
    "Competing explanations revisited": "Not yet evaluated.",
    "Next discriminating test": "Define the next bounded differentiating probe.",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str = ""

    def to_jsonable(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message}
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass
class ValidationReport:
    candidate: Path
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.issues

    def add(self, code: str, message: str, path: Path | str = "") -> None:
        self.issues.append(
            ValidationIssue(code=code, message=message, path=str(path) if path else "")
        )

    def to_jsonable(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "candidate": str(self.candidate),
            "issues": [issue.to_jsonable() for issue in self.issues],
        }


def validate_idea_id(idea_id: str) -> str:
    normalized = str(idea_id or "").strip()
    if len(normalized) > 64 or IDEA_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "idea-id must be 1-64 lowercase ASCII letters, digits, or single hyphens"
        )
    return normalized


def playground_root(project_root: Path | str) -> Path:
    return Path(project_root).expanduser().resolve() / PLAYGROUND_RELATIVE_ROOT


def candidate_path(project_root: Path | str, idea_id: str) -> Path:
    root = playground_root(project_root)
    candidate = root / validate_idea_id(idea_id)
    if candidate.parent != root:
        raise ValueError("candidate path escapes the Chemistry Playground root")
    return candidate


def _is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_multiply_linked_regular_file(path: Path) -> bool:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1


def _candidate_boundary_paths(project_root: Path | str, candidate: Path) -> tuple[Path, ...]:
    project = Path(project_root).expanduser().resolve()
    root = project / PLAYGROUND_RELATIVE_ROOT
    return (project / "research", root, candidate)


def _iter_candidate_tree(candidate: Path):
    pending = [candidate]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            yield path
            if _is_link_or_reparse_point(path):
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_directory = False
            if is_directory:
                pending.append(path)


def _frontmatter(metadata: dict[str, str]) -> str:
    body = "\n".join(f"{key}: {value}" for key, value in metadata.items())
    return f"---\n{body}\n---\n"


def _question_template(idea_id: str, question: str, hypothesis: str) -> str:
    return (
        _frontmatter(
            {
                "protocol_version": str(PROTOCOL_VERSION),
                "idea_id": idea_id,
                "initial_status": "speculative",
            }
        )
        + f"# Chem Playground Question: {idea_id}\n\n"
        + f"## Scientific question\n\n{question.strip()}\n\n"
        + f"## Hypothesis\n\n{hypothesis.strip()}\n\n"
        + "## Explicit assumptions\n\n"
        + "- None recorded yet; add them before advancing beyond `speculative`.\n\n"
        + "## Competing explanations\n\n"
        + "- None recorded yet; add at least one plausible alternative when available.\n\n"
        + "## Falsifiable predictions\n\n"
        + "- Define an observable outcome that would weaken or falsify the hypothesis.\n\n"
        + "## Known evidence\n\n- None recorded.\n\n"
        + "## Missing evidence\n\n- Identify the evidence needed to discriminate explanations.\n\n"
        + "## Allowed probe budget\n\n"
        + "- Define compute, wall-time, query, and iteration ceilings before execution.\n\n"
        + "## Safety and authorization\n\n"
        + "- This file authorizes no physical action, instrument control, or experiment.\n\n"
        + "## Non-goals\n\n"
        + "- Do not treat Playground work as formal Research evidence or stage completion.\n"
    )


def _result_template(idea_id: str) -> str:
    return (
        _frontmatter(
            {
                "protocol_version": str(PROTOCOL_VERSION),
                "idea_id": idea_id,
                "status": "speculative",
                "status_history": "speculative",
                "reviewer": "pending",
                "reviewer_recommendation": "pending",
            }
        )
        + f"# Chem Playground Result: {idea_id}\n\n"
        + "## Summary\n\n- No probe has been completed.\n\n"
        + "## Work performed\n\n- Candidate initialized only.\n\n"
        + "## Evidence ledger\n\n"
        + "Add entries as `- [evidence_class] relative/path-or-URL - claim supported`.\n\n"
        + "## Computational probes\n\n- None completed.\n\n"
        + "## Uncertainty and applicability\n\n"
        + "- State uncertainty, assumptions, sensitivity, and the valid domain.\n\n"
        + "## Negative and failed results\n\n- None recorded.\n\n"
        + "## Competing explanations revisited\n\n- Not yet evaluated.\n\n"
        + "## Next discriminating test\n\n- Define the next bounded differentiating probe.\n\n"
        + "## Reviewer decision basis\n\n- Pending independent Reviewer assessment.\n\n"
        + "## Promotion boundary\n\n"
        + "- Even `promoted` means eligible for formal Research consideration; it does "
        + "not change `.argus/PIPELINE_STATE.json` or establish a scientific fact.\n\n"
        + "## References\n\n- None recorded.\n"
    )


def initialize_candidate(
    project_root: Path | str,
    idea_id: str,
    *,
    question: str,
    hypothesis: str,
) -> Path:
    if not str(question or "").strip():
        raise ValueError("question must not be empty")
    if not str(hypothesis or "").strip():
        raise ValueError("hypothesis must not be empty")
    candidate = candidate_path(project_root, idea_id)
    for boundary in _candidate_boundary_paths(project_root, candidate):
        if _is_link_or_reparse_point(boundary):
            raise ValueError(
                f"Chemistry Playground paths must not use links or junctions: {boundary}"
            )
    if candidate.exists():
        raise FileExistsError(f"candidate already exists: {candidate}")

    candidate.mkdir(parents=True, exist_ok=False)
    for relative in (
        Path("work") / "scripts",
        Path("work") / "notebooks",
        Path("evidence") / "inputs",
        Path("evidence") / "outputs",
    ):
        (candidate / relative).mkdir(parents=True, exist_ok=False)
    (candidate / "QUESTION.md").write_text(
        _question_template(validate_idea_id(idea_id), question, hypothesis),
        encoding="utf-8",
    )
    (candidate / "RESULT.md").write_text(
        _result_template(validate_idea_id(idea_id)),
        encoding="utf-8",
    )
    return candidate


def _parse_document(path: Path, report: ValidationReport) -> tuple[dict[str, str], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        report.add("missing_file", f"required file is missing: {path.name}", path)
        return {}, ""
    except (OSError, UnicodeError) as exc:
        report.add("unreadable_file", f"cannot read {path.name}: {exc}", path)
        return {}, ""
    if not text.startswith("---\n"):
        report.add("missing_frontmatter", f"{path.name} must start with frontmatter", path)
        return {}, text
    frontmatter, separator, body = text[4:].partition("\n---\n")
    if not separator:
        report.add("invalid_frontmatter", f"{path.name} frontmatter is not closed", path)
        return {}, text
    metadata: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            report.add("invalid_frontmatter", f"invalid metadata line: {line!r}", path)
            continue
        metadata[key.strip()] = value.strip()
    return metadata, body


def _section_data(body: str) -> tuple[dict[str, str], set[str]]:
    found: dict[str, list[str]] = {}
    duplicates: set[str] = set()
    current = ""
    original_lines = body.splitlines()
    structural_lines = _strip_markdown_code(body).splitlines()
    for index, line in enumerate(original_lines):
        structural = (
            structural_lines[index]
            if index < len(structural_lines)
            else ""
        )
        if structural.startswith("## "):
            current = structural[3:].strip()
            if current in found:
                duplicates.add(current)
            else:
                found[current] = []
        elif current and structural.strip():
            found[current].append(line)
    return (
        {name: "\n".join(lines).strip() for name, lines in found.items()},
        duplicates,
    )


def _sections(body: str) -> dict[str, str]:
    return _section_data(body)[0]


def _validate_required_sections(
    path: Path,
    body: str,
    required: tuple[str, ...],
    report: ValidationReport,
) -> dict[str, str]:
    sections, duplicates = _section_data(body)
    for name in sorted(duplicates):
        report.add(
            "duplicate_section",
            f"{path.name} contains duplicate section {name!r}",
            path,
        )
    for name in required:
        if name not in sections:
            report.add("missing_section", f"{path.name} is missing section {name!r}", path)
        elif not sections[name]:
            report.add("empty_section", f"{path.name} section {name!r} is empty", path)
    return sections


def _parse_status_history(raw: str) -> list[str]:
    return [status.strip() for status in raw.split("->") if status.strip()]


def _canonical_visible_text(content: str) -> str:
    text = _strip_hidden_html(str(content or ""))
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Cf"
    )
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!])", r"\1", text)
    text = re.sub(r"[`*_~>#]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_list_marker(content: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(content or "")))
    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Cf"
    )
    return re.sub(
        r"^\s*(?:(?:[-*+])|(?:\d{1,9}[.)]))\s+(?:\[[ xX]\]\s*)?",
        "",
        text,
    )


def _is_placeholder_section(content: str) -> bool:
    rows = []
    for raw in str(content or "").splitlines():
        row = _strip_list_marker(raw)
        row = _canonical_visible_text(row).casefold().rstrip(".")
        if row:
            rows.append(row)
    if not rows:
        return True
    reserved = {
        "none",
        "none recorded",
        "tbd",
        "pending",
        "placeholder",
        "not yet evaluated",
        "pending independent reviewer assessment",
    }
    return all(
        row in reserved
        or row.startswith(("tbd ", "placeholder ", "pending ", "define the next "))
        for row in rows
    )


def _has_substantive_section(content: str) -> bool:
    visible_content = _strip_markdown_code(content)
    return not _is_placeholder_section(visible_content)


def _canonical_section_text(content: str) -> str:
    rows = []
    for raw in str(content or "").splitlines():
        row = _strip_list_marker(raw)
        visible = _canonical_visible_text(row).casefold().rstrip(".")
        if visible:
            rows.append(visible)
    return " ".join(rows)


def _validate_progress_content(
    metadata: dict[str, str],
    question_sections: dict[str, str],
    result_sections: dict[str, str],
    report: ValidationReport,
    question_path: Path,
    result_path: Path,
) -> None:
    status = metadata.get("status", "")
    if status == "speculative" or status not in ALL_STATUSES:
        return

    required_question = {
        "Explicit assumptions",
        "Competing explanations",
        "Falsifiable predictions",
        "Known evidence",
        "Missing evidence",
        "Allowed probe budget",
    }
    required_result = {"Summary", "Work performed"}
    history = set(_parse_status_history(metadata.get("status_history", "")))
    if "computationally_probed" in history or status == "promoted":
        required_result.add("Computational probes")
    if status == "reviewer_candidate" or status in TERMINAL_STATUSES:
        required_result.update(
            {
                "Uncertainty and applicability",
                "Competing explanations revisited",
                "Next discriminating test",
            }
        )

    for sections, names, path in (
        (question_sections, required_question, question_path),
        (result_sections, required_result, result_path),
    ):
        for name in sorted(names):
            content = sections.get(name, "")
            initial = _INITIAL_MUTABLE_SECTIONS.get(name, "")
            if (
                _canonical_section_text(content)
                == _canonical_section_text(initial)
                or not _has_substantive_section(content)
            ):
                report.add(
                    "stale_template_section",
                    f"{path.name} section {name!r} must contain substantive "
                    "candidate-specific content before advancing",
                    path,
                )


def _validate_status(
    metadata: dict[str, str],
    sections: dict[str, str],
    report: ValidationReport,
    result_path: Path,
) -> None:
    status = metadata.get("status", "")
    history = _parse_status_history(metadata.get("status_history", ""))
    recommendation = metadata.get("reviewer_recommendation", "")
    reviewer = metadata.get("reviewer", "")
    reviewer_normalized = _canonical_visible_text(reviewer).casefold()
    reviewer_has_identifier = any(
        character.isalnum() for character in reviewer_normalized
    )
    reviewer_token = "".join(
        character
        for character in reviewer_normalized
        if character.isascii() and character.isalnum()
    )

    if status not in ALL_STATUSES:
        report.add("invalid_status", f"unknown Playground status: {status!r}", result_path)
    if not history or history[0] != "speculative":
        report.add(
            "invalid_status_history",
            "status_history must start with speculative",
            result_path,
        )
    if history and history[-1] != status:
        report.add(
            "invalid_status_history",
            "status must equal the final status_history entry",
            result_path,
        )
    for previous, current in zip(history, history[1:]):
        if current not in _VALID_TRANSITIONS.get(previous, set()):
            report.add(
                "illegal_status_transition",
                f"illegal Playground transition: {previous} -> {current}",
                result_path,
            )
    if recommendation not in REVIEWER_RECOMMENDATIONS:
        report.add(
            "invalid_reviewer_recommendation",
            f"unknown reviewer recommendation: {recommendation!r}",
            result_path,
        )
    if status in TERMINAL_STATUSES:
        if "reviewer_candidate" not in history:
            report.add(
                "reviewer_gate_missing",
                "terminal status requires reviewer_candidate in status_history",
                result_path,
            )
        if (
            not reviewer_has_identifier
            or reviewer_normalized in _PLACEHOLDER_REVIEWERS
            or (
                bool(reviewer_token)
                and reviewer_token in _PLACEHOLDER_REVIEWER_TOKENS
            )
            or "placeholder" in reviewer_token
            or "engineer" in reviewer_token
            or "selfreview" in reviewer_token
        ):
            report.add(
                "reviewer_gate_missing",
                "terminal status requires a non-pending reviewer identifier",
                result_path,
            )
        if recommendation != status:
            report.add(
                "reviewer_gate_mismatch",
                "terminal status must match reviewer_recommendation",
                result_path,
            )
        if not _has_substantive_section(
            sections.get("Reviewer decision basis", "")
        ):
            report.add(
                "missing_reviewer_decision_basis",
                "terminal status requires a substantive non-template Reviewer decision basis",
                result_path,
            )
    elif recommendation != "pending":
        report.add(
            "premature_reviewer_recommendation",
            "non-terminal status must keep reviewer_recommendation=pending",
            result_path,
        )
    if status == "promoted" and "computationally_probed" not in history:
        report.add(
            "promotion_without_probe",
            "promoted status requires computationally_probed in status_history",
            result_path,
        )
    if status == "falsified":
        negative = sections.get("Negative and failed results", "")
        if not _has_substantive_section(negative):
            report.add(
                "missing_falsification_evidence",
                "falsified status requires retained negative or failed results",
                result_path,
            )
    if status == "blocked" and not _has_substantive_section(
        sections.get("Reviewer decision basis", "")
    ):
        report.add(
            "missing_blocker_basis",
            "blocked status requires a substantive blocker and why work cannot continue",
            result_path,
        )


def _is_external_reference(target: str) -> bool:
    normalized = target.strip().casefold()
    return normalized.startswith(("http://", "https://", "doi:", "mailto:", "#"))


def _is_valid_external_evidence_reference(target: str) -> bool:
    normalized = target.strip()
    try:
        parsed = urlparse(normalized)
    except ValueError:
        return False
    if parsed.scheme.casefold() in {"http", "https"}:
        if re.search(r"[\s\\|<>\"`]", normalized):
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        try:
            _ = parsed.port
        except ValueError:
            return False
        raw_hostname = str(parsed.hostname or "").casefold()
        try:
            hostname = raw_hostname.encode("idna").decode("ascii").rstrip(".")
        except UnicodeError:
            return False
        if (
            not hostname
            or hostname in _PLACEHOLDER_HOSTS
            or len(hostname) > 253
            or hostname.endswith(_RESERVED_HOST_SUFFIXES)
        ):
            return False
        if ":" in hostname or re.fullmatch(r"[0-9.]+", hostname):
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError:
                return False
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            return address.is_global and not address.is_multicast
        if "." not in hostname:
            return False
        return (
            re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                hostname,
            )
            is not None
        )
    return (
        re.fullmatch(
            r"doi:10\.\d{4,9}/[^\s\\|<>\"`]+",
            normalized,
            re.IGNORECASE,
        )
        is not None
    )


def _resolve_local_reference(candidate: Path, target: str) -> Path:
    clean = unquote(target.strip().strip("<>")).split("#", 1)[0]
    if not clean or Path(clean).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", clean):
        raise ValueError("reference must be candidate-relative")
    relative = Path(*clean.replace("\\", "/").split("/"))
    resolved = (candidate / relative).resolve()
    if resolved != candidate and candidate not in resolved.parents:
        raise ValueError("reference escapes the candidate directory")
    return resolved


_HTML_TOKEN = re.compile(
    r"(?is)<!--.*?-->|"
    r"<(?P<close>/)?\s*(?P<tag>[a-z][\w:-]*)\b"
    r"(?P<attrs>(?:\"[^\"]*\"|'[^']*'|[^'\">])*)>"
)
_HTML_ALWAYS_HIDDEN = {"pre", "code", "script", "style", "template"}
_HTML_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def _strip_hidden_html(text: str) -> str:
    value = str(text or "")
    visible: list[str] = []
    stack: list[tuple[str, bool]] = []
    hidden_depth = 0
    cursor = 0

    def append_segment(segment: str, hidden: bool) -> None:
        if hidden:
            visible.append("\n" * segment.count("\n"))
        else:
            visible.append(segment)

    for match in _HTML_TOKEN.finditer(value):
        append_segment(value[cursor:match.start()], hidden_depth > 0)
        token = match.group(0)
        if token.startswith("<!--"):
            append_segment(token, True)
            cursor = match.end()
            continue
        tag = str(match.group("tag") or "").casefold()
        attrs = str(match.group("attrs") or "")
        closing = bool(match.group("close"))
        hidden_before = hidden_depth > 0
        if closing:
            matching_index = next(
                (
                    index
                    for index in range(len(stack) - 1, -1, -1)
                    if stack[index][0] == tag
                ),
                -1,
            )
            if matching_index >= 0:
                for _, hides in stack[matching_index:]:
                    if hides:
                        hidden_depth -= 1
                del stack[matching_index:]
            append_segment(token, hidden_before)
            cursor = match.end()
            continue
        normalized_attrs = html.unescape(attrs).casefold()
        hides = (
            tag in _HTML_ALWAYS_HIDDEN
            or bool(re.search(r"(?:^|\s)hidden(?:\s|=|/|$)", normalized_attrs))
            or bool(
                re.search(
                    r"(?:^|\s)aria-hidden\s*=\s*(?:['\"]?true['\"]?)",
                    normalized_attrs,
                )
            )
            or bool(
                re.search(
                    r"(?:^|\s)style\s*=\s*(?:['\"][^'\"]*"
                    r"(?:display\s*:\s*none|visibility\s*:\s*hidden)"
                    r"[^'\"]*['\"]|[^\s>]*"
                    r"(?:display\s*:\s*none|visibility\s*:\s*hidden)"
                    r"[^\s>]*)",
                    normalized_attrs,
                )
            )
        )
        append_segment(token, hidden_before or hides)
        self_closing = normalized_attrs.rstrip().endswith("/") or tag in _HTML_VOID
        if not self_closing:
            stack.append((tag, hides))
            if hides:
                hidden_depth += 1
        cursor = match.end()
    append_segment(value[cursor:], hidden_depth > 0)
    return "".join(visible)


def _strip_markdown_code(text: str) -> str:
    value = _strip_hidden_html(str(text or ""))

    def strip_inline(value: str) -> str:
        visible: list[str] = []
        index = 0
        while index < len(value):
            if value[index] != "`":
                visible.append(value[index])
                index += 1
                continue
            end = index
            while end < len(value) and value[end] == "`":
                end += 1
            marker_length = end - index
            search = end
            closing = -1
            closing_end = -1
            while search < len(value):
                candidate = value.find("`", search)
                if candidate < 0:
                    break
                candidate_end = candidate
                while candidate_end < len(value) and value[candidate_end] == "`":
                    candidate_end += 1
                if candidate_end - candidate == marker_length:
                    closing = candidate
                    closing_end = candidate_end
                    break
                search = candidate_end
            if closing < 0:
                visible.append(value[index:end])
                index = end
                continue
            hidden = value[end:closing]
            visible.append(" ")
            visible.extend("\n" for _ in range(hidden.count("\n")))
            index = closing_end
        return "".join(visible)

    visible_lines: list[str] = []
    fence_character = ""
    fence_length = 0
    previous_visible_nonblank = False
    for line in value.splitlines():
        container = re.match(r"^\s{0,3}(?:>\s?)+", line)
        structural_line = line[container.end():] if container is not None else line
        list_container = re.match(
            r"^\s{0,3}(?:(?:[-+*])|(?:\d{1,9}[.)]))\s+",
            structural_line,
        )
        if list_container is not None:
            structural_line = structural_line[list_container.end():]
        if fence_character:
            closing = re.match(
                rf"^\s{{0,3}}{re.escape(fence_character)}"
                rf"{{{fence_length},}}\s*$",
                structural_line,
            )
            if closing is not None:
                fence_character = ""
                fence_length = 0
            visible_lines.append("")
            previous_visible_nonblank = False
            continue
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", structural_line)
        if match:
            marker = match.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            visible_lines.append("")
            previous_visible_nonblank = False
            continue
        if line.startswith("\t") or line.startswith("    "):
            if previous_visible_nonblank:
                visible_lines.append(line)
            else:
                visible_lines.append("")
            continue
        visible_lines.append(line)
        previous_visible_nonblank = bool(line.strip())
    return strip_inline("\n".join(visible_lines))


def _validate_references(
    candidate: Path,
    documents: tuple[tuple[Path, str], ...],
    report: ValidationReport,
) -> None:
    refs: list[tuple[Path, str]] = []
    for source, text in documents:
        visible_text = _strip_markdown_code(text)
        refs.extend(
            (source, match.group("target"))
            for match in _MARKDOWN_LINK.finditer(visible_text)
        )
        references = _sections(text).get("References", "")
        for raw_line in references.splitlines():
            line = raw_line.strip()
            if not line or _is_placeholder_section(line):
                continue
            match = _REFERENCE_LINE.match(line)
            if match is None:
                report.add(
                    "invalid_reference_entry",
                    "References entries must use '- [reference] path-or-URL - purpose'",
                    source,
                )
                continue
            refs.append((source, match.group("ref")))

    for source, target in refs:
        if _is_external_reference(target):
            if target.strip().startswith("#"):
                continue
            if not _is_valid_external_evidence_reference(target):
                report.add(
                    "invalid_external_reference",
                    f"{target!r} is not a valid HTTP(S) or DOI reference",
                    source,
                )
            continue
        try:
            resolved = _resolve_local_reference(candidate, target)
        except ValueError as exc:
            report.add("unsafe_reference", f"{target!r}: {exc}", source)
            continue
        if not resolved.is_file():
            report.add(
                "missing_reference",
                f"referenced path is not a regular file: {target}",
                source,
            )


def _validate_evidence(
    candidate: Path,
    metadata: dict[str, str],
    result_sections: dict[str, str],
    result_path: Path,
    report: ValidationReport,
) -> list[tuple[str, str]]:
    evidence_lines: list[tuple[str, str]] = []
    valid_classes: set[str] = set()
    local_output_classes: set[str] = set()
    evidence_root = (candidate / "evidence").resolve()
    input_root = (candidate / "evidence" / "inputs").resolve()
    output_root = (candidate / "evidence" / "outputs").resolve()
    ledger = result_sections.get("Evidence ledger", "")
    for line in ledger.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == (
            "Add entries as `- [evidence_class] relative/path-or-URL "
            "- claim supported`."
        ) or _is_placeholder_section(stripped):
            continue
        match = _EVIDENCE_LINE.match(line)
        if match is None:
            report.add(
                "invalid_evidence_entry",
                "Evidence ledger entries must use "
                "'- [evidence_class] path-or-URL - claim supported'",
                result_path,
            )
            continue
        claim = match.group("claim")
        canonical_claim = _canonical_visible_text(claim).casefold().rstrip(".")
        if (
            _is_placeholder_section(claim)
            or canonical_claim == "claim supported"
            or sum(character.isalnum() for character in canonical_claim) < 8
        ):
            report.add(
                "invalid_evidence_claim",
                "Evidence ledger claims must be substantive and non-placeholder",
                result_path,
            )
            continue
        evidence_class = match.group("class").casefold()
        target = match.group("ref")
        if evidence_class not in EVIDENCE_CLASSES:
            report.add(
                "invalid_evidence_class",
                f"unsupported evidence class: {evidence_class}",
                result_path,
            )
            continue
        evidence_lines.append((evidence_class, target))
        if _is_external_reference(target):
            if (
                evidence_class in {"retrieved", "curated"}
                and _is_valid_external_evidence_reference(target)
            ):
                valid_classes.add(evidence_class)
            else:
                report.add(
                    "invalid_external_evidence",
                    f"{target!r} is not a valid HTTP(S) or DOI evidence reference",
                    result_path,
                )
            continue
        try:
            resolved = _resolve_local_reference(candidate, target)
        except ValueError as exc:
            report.add("unsafe_reference", f"{target!r}: {exc}", result_path)
            continue
        if not resolved.is_file():
            report.add(
                "invalid_evidence_file",
                f"evidence reference is not a regular file: {target}",
                result_path,
            )
            continue
        try:
            if resolved.stat().st_size <= 0:
                raise OSError("file is empty")
            with resolved.open("rb") as evidence_file:
                if not evidence_file.read(1):
                    raise OSError("file is empty")
        except OSError as exc:
            report.add(
                "invalid_evidence_file",
                f"evidence file is unreadable or empty: {target}: {exc}",
                result_path,
            )
            continue
        if evidence_root not in resolved.parents:
            report.add(
                "invalid_evidence_location",
                f"local evidence must be retained below evidence/: {target}",
                result_path,
            )
            continue
        expected_root = (
            input_root
            if evidence_class in _INPUT_EVIDENCE_CLASSES
            else output_root
            if evidence_class in _OUTPUT_EVIDENCE_CLASSES
            else None
        )
        if expected_root is not None and expected_root not in resolved.parents:
            report.add(
                "evidence_class_location_mismatch",
                f"{evidence_class} evidence must be retained below "
                f"{expected_root.relative_to(candidate)}: {target}",
                result_path,
            )
            continue
        valid_classes.add(evidence_class)
        if output_root in resolved.parents:
            local_output_classes.add(evidence_class)

    status = metadata.get("status", "")
    history = set(_parse_status_history(metadata.get("status_history", "")))
    grounding_claimed = "literature_grounded" in history or status == "promoted"
    computation_claimed = "computationally_probed" in history or status == "promoted"
    if grounding_claimed and not valid_classes.intersection({"retrieved", "curated"}):
        report.add(
            "missing_grounding_evidence",
            "status history claims literature grounding without retrieved or curated evidence",
            result_path,
        )
    if computation_claimed and not local_output_classes.intersection(
        {"predicted", "computed", "simulated"}
    ):
        report.add(
            "missing_computational_evidence",
            "status history claims a computational probe without retained primary evidence",
            result_path,
        )
    if status == "falsified" and not valid_classes.intersection(
        {"negative", "failed"}
    ):
        report.add(
            "missing_falsification_evidence",
            "falsified status requires retained negative or failed evidence",
            result_path,
        )
    return evidence_lines


def validate_candidate(project_root: Path | str, idea_id: str) -> ValidationReport:
    candidate = candidate_path(project_root, idea_id)
    report = ValidationReport(candidate=candidate)
    unsafe_boundaries = [
        path
        for path in _candidate_boundary_paths(project_root, candidate)
        if _is_link_or_reparse_point(path)
    ]
    for path in unsafe_boundaries:
        report.add(
            "symlink_not_allowed",
            "Playground boundaries must not contain symlinks or junctions",
            path,
        )
    if unsafe_boundaries:
        return report
    if not candidate.is_dir():
        report.add("missing_candidate", "candidate directory does not exist", candidate)
        return report

    for path in _iter_candidate_tree(candidate):
        if _is_link_or_reparse_point(path):
            report.add(
                "symlink_not_allowed",
                "candidate tree must not contain symlinks or junctions",
                path,
            )
        elif _is_multiply_linked_regular_file(path):
            report.add(
                "hardlink_not_allowed",
                "candidate tree must not contain multiply linked files",
                path,
            )

    required_directories = (
        Path("work") / "scripts",
        Path("work") / "notebooks",
        Path("evidence") / "inputs",
        Path("evidence") / "outputs",
    )
    for relative in required_directories:
        if not (candidate / relative).is_dir():
            report.add("missing_directory", f"required directory is missing: {relative}", relative)

    question_path = candidate / "QUESTION.md"
    result_path = candidate / "RESULT.md"
    question_meta, question_body = _parse_document(question_path, report)
    result_meta, result_body = _parse_document(result_path, report)
    question_sections = _validate_required_sections(
        question_path, question_body, QUESTION_SECTIONS, report
    )
    result_sections = _validate_required_sections(
        result_path, result_body, RESULT_SECTIONS, report
    )

    expected_id = validate_idea_id(idea_id)
    for path, metadata in ((question_path, question_meta), (result_path, result_meta)):
        if metadata.get("idea_id") != expected_id:
            report.add("idea_id_mismatch", f"{path.name} idea_id does not match directory", path)
        if metadata.get("protocol_version") != str(PROTOCOL_VERSION):
            report.add(
                "protocol_version_mismatch",
                f"{path.name} must use protocol_version {PROTOCOL_VERSION}",
                path,
            )
    if question_meta.get("initial_status") != "speculative":
        report.add(
            "invalid_initial_status",
            "QUESTION.md initial_status must be speculative",
            question_path,
        )

    _validate_status(result_meta, result_sections, report, result_path)
    _validate_progress_content(
        result_meta,
        question_sections,
        result_sections,
        report,
        question_path,
        result_path,
    )
    _validate_evidence(
        candidate,
        result_meta,
        result_sections,
        result_path,
        report,
    )
    _validate_references(
        candidate,
        ((question_path, question_body), (result_path, result_body)),
        report,
    )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create one new candidate")
    init_parser.add_argument("--project-root", required=True)
    init_parser.add_argument("--idea-id", required=True)
    init_parser.add_argument("--question", required=True)
    init_parser.add_argument("--hypothesis", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate one candidate")
    validate_parser.add_argument("--project-root", required=True)
    validate_parser.add_argument("--idea-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "init":
            created = initialize_candidate(
                args.project_root,
                args.idea_id,
                question=args.question,
                hypothesis=args.hypothesis,
            )
            print(json.dumps({"created": str(created)}, ensure_ascii=True))
            return 0
        report = validate_candidate(args.project_root, args.idea_id)
    except (FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(report.to_jsonable(), ensure_ascii=True, indent=2))
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
