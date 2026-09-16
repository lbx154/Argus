"""Derive the volatile half of the method card from code, tests, configs and git.

``METHOD.md`` at the project root is written by the Engineer once: what the
method is, component by component with quotes from the selected route, the
protocol, and what would falsify the claim. Everything that changes from
round to round is *derived* here at zero model cost and shown next to the
hand-written card in the Atlas web UI and to the Reviewer:

* per-component test status, from the host-run ``tests/spec`` results
  (``.argus/round-checks/latest.json``) joined with the component markers the
  tests carry (``.argus/spec_components.json``, written by the spec conftest);
* reused code, from an ``ast`` import scan of the project's own Python files
  mapped to ``third_party/`` clones (pinned revision, remote) and installed
  packages (version);
* hyperparameters, from the YAML/JSON/TOML config files the runs use, with the
  ``# why: ...`` comment the Engineer leaves beside a value and a diff against
  the previous snapshot;
* a change log, from ``git log``;
* code anchors, from ``# @component <name>``, ``# @simplified <name>: <reason>``
  and ``# @reuses <what> <note>`` comments the Engineer leaves in the code, so
  a card row points at the def that implements it.

Nothing here gates a stage or admits a task. Everything is fail-soft: a
missing piece yields an empty value, never an exception.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

METHOD_FILENAME = "METHOD.md"
MARKDOWN_LIMIT = 64 * 1024

SPEC_COMPONENTS_PATH = Path(".argus") / "spec_components.json"
LATEST_CHECKS_PATH = Path(".argus") / "round-checks" / "latest.json"
HYPERPARAMETER_SNAPSHOT_PATH = Path(".argus") / "method-card" / "hyperparameters.json"

COMPONENTS_HEADING = "components"
PROTOCOL_HEADING = "protocol"
FALSIFIERS_HEADING = "what would falsify the claim"

#: Component statuses, in the order the Reviewer should worry about them.
STATUSES: tuple[str, ...] = ("contradicted", "partial", "untested", "unchecked", "proven")

PROJECT_SCAN_ROOTS = ("src", "scripts")
SKIPPED_DIR_NAMES = frozenset({"tests", "test", "third_party", "node_modules", "__pycache__"})
MAX_PROJECT_FILES = 400
MAX_CONFIG_FILES = 20
MAX_HYPERPARAMETERS = 400
CONFIG_SUFFIXES = (".yaml", ".yml", ".json", ".toml")
CHANGE_LOG_ENTRIES = 15
CHANGE_LOG_PATHS = ("METHOD.md", "src", "configs", "tests/spec", "third_party")
GIT_TIMEOUT_S = 5.0
#: Hard cap on the review packet ``render_for_reviewer`` returns.
REVIEWER_MAX_LINES = 120
ANCHOR_EXCERPT_LINES = 30
REVIEWER_EXCERPT_LINES = 20
REVIEWER_CHANGED_FILES = 20
MAX_ANCHORS = 200

_COLUMN_ALIASES = {
    "component": "component",
    "the idea prescribes": "prescribes",
    "idea prescribes": "prescribes",
    "prescribes": "prescribes",
    "prescribed": "prescribes",
    "notes": "notes",
    "note": "notes",
}
_YAML_KEY_LINE = re.compile(r"^(?P<indent>\s*)(?P<key>[^\s#\-][^:#]*?):(?P<rest>.*)$")
_WHY_COMMENT = re.compile(r"#\s*why:\s*(?P<why>.+?)\s*$", re.IGNORECASE)
_CONFIG_REFERENCE = re.compile(r"[\w][\w./-]*\.(?:ya?ml|json|toml)\b")
_CHANGE_LOG_HEAD = re.compile(r"^(?P<when>\d{4}-\d{2}-\d{2})\t(?P<summary>.*)$")
_ANCHOR_COMPONENT = re.compile(r"#\s*@component\s+(?P<component>.+?)\s*$")
_ANCHOR_SIMPLIFIED = re.compile(r"#\s*@simplified\s+(?P<component>[^:]+):\s*(?P<reason>.+?)\s*$")
_ANCHOR_REUSES = re.compile(r"#\s*@reuses\s+(?P<what>\S+)\s+(?P<note>.+?)\s*$")
_DEF_OR_CLASS = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(?P<name>\w+)")
_PORCELAIN_LINE = re.compile(r"^(?P<status>[ MADRCU?!]{2}) (?P<path>.+)$")


# --------------------------------------------------------------------------
# Markdown parsing
# --------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def method_title(markdown: str) -> str:
    """First H1 heading text, or '' when the document has none."""
    for line in markdown.splitlines():
        match = re.match(r"^\s{0,3}#\s+(.*?)\s*#*\s*$", line)
        if match:
            return match.group(1).strip()
    return ""


def _sections(markdown: str) -> tuple[str, dict[str, str]]:
    """Split on ``##`` headings; return (text before the first ``##``, {heading: body}).

    Headings inside fenced code blocks are ignored. The first occurrence of a
    heading wins; heading keys are lower-cased and stripped.
    """
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        if not in_fence:
            match = re.match(r"^\s{0,3}##\s+(.*?)\s*#*\s*$", line)
            if match:
                key = match.group(1).strip().lower()
                current = sections.setdefault(key, [])
                continue
        if current is None:
            preamble.append(line)
        else:
            current.append(line)
    return "\n".join(preamble), {key: "\n".join(body).strip() for key, body in sections.items()}


def method_statement(markdown: str) -> str:
    """The first non-empty paragraph after the H1 (and before any ``##``)."""
    preamble, _sections_by_heading = _sections(markdown)
    lines = preamble.splitlines()
    seen_h1 = False
    paragraph: list[str] = []
    for line in lines:
        if not seen_h1:
            if re.match(r"^\s{0,3}#\s+", line):
                seen_h1 = True
            continue
        if line.strip():
            if line.strip().startswith(("#", "|", "```", "~~~")):
                if paragraph:
                    break
                continue
            paragraph.append(line.strip())
        elif paragraph:
            break
    return " ".join(paragraph).strip()


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", cell or "-") for cell in cells)


def _strip_markup(text: str) -> str:
    return text.strip().strip("`*_\"'“”").strip()


def normalize_component_name(name: str) -> str:
    """Case- and whitespace-insensitive key for joining card rows with markers."""
    return re.sub(r"\s+", " ", _strip_markup(str(name or ""))).casefold()


def method_components(markdown: str) -> list[dict[str, str]]:
    """Rows of the ``## Components`` table as {component, prescribes, notes}.

    The header decides the columns; extra columns (the old five-column shape
    with Implemented in / Status / Proven by) are ignored. Without a
    recognisable header the first three columns are taken positionally.
    """
    _preamble, sections = _sections(markdown)
    body = sections.get(COMPONENTS_HEADING, "")
    lines = body.splitlines()
    rows: list[dict[str, str]] = []
    index = 0
    while index < len(lines) - 1:
        header_line, separator_line = lines[index], lines[index + 1]
        if "|" in header_line and _is_separator(separator_line):
            header = [cell.lower() for cell in _split_row(header_line)]
            mapping: dict[int, str] = {}
            for position, cell in enumerate(header):
                field = _COLUMN_ALIASES.get(cell)
                if field and field not in mapping.values():
                    mapping[position] = field
            if "component" not in mapping.values():
                mapping = {0: "component", 1: "prescribes", 2: "notes"}
            for row_line in lines[index + 2 :]:
                if "|" not in row_line:
                    if row_line.strip():
                        break
                    continue
                cells = _split_row(row_line)
                row = {"component": "", "prescribes": "", "notes": ""}
                for position, field in mapping.items():
                    if position < len(cells):
                        row[field] = cells[position]
                row["component"] = _strip_markup(row["component"])
                if row["component"]:
                    rows.append(row)
            break
        index += 1
    return rows


# --------------------------------------------------------------------------
# JSON fixtures written by the spec conftest and the host-run checks
# --------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def load_spec_components(workdir: Path) -> dict[str, dict[str, str]]:
    """``{nodeid: {component, kind}}`` from ``.argus/spec_components.json``."""
    payload = _load_json(Path(workdir) / SPEC_COMPONENTS_PATH)
    items = payload.get("items") if payload else None
    result: dict[str, dict[str, str]] = {}
    if not isinstance(items, dict):
        return result
    for nodeid, entry in items.items():
        if not isinstance(entry, dict):
            continue
        component = str(entry.get("component") or "").strip()
        if not component:
            continue
        result[str(nodeid)] = {
            "component": component,
            "kind": str(entry.get("kind") or "invariant"),
        }
    return result


def load_latest_checks(workdir: Path) -> dict[str, Any] | None:
    """The most recent host-run check JSON, or ``None``."""
    return _load_json(Path(workdir) / LATEST_CHECKS_PATH)


def component_status(tests: list[dict[str, Any]]) -> str:
    """Contradicted if any test failed; proven if all passed and a knockout or
    differential passed; unchecked when no test has an outcome yet; else partial."""
    outcomes = [str(test.get("outcome") or "").upper() for test in tests]
    if not tests:
        return "untested"
    if not any(outcomes):
        return "unchecked"
    if any(outcome in {"FAILED", "ERROR"} for outcome in outcomes):
        return "contradicted"
    if all(outcome == "PASSED" for outcome in outcomes) and any(
        str(test.get("kind")) in {"knockout", "differential"} for test in tests
    ):
        return "proven"
    return "partial"


def _join_components(
    card_rows: list[dict[str, str]],
    markers: dict[str, dict[str, str]],
    latest: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outcomes = latest.get("outcomes") if isinstance(latest, dict) else None
    outcomes = outcomes if isinstance(outcomes, dict) else {}
    host_components = latest.get("components") if isinstance(latest, dict) else None
    host_components = host_components if isinstance(host_components, dict) else {}
    host_by_key = {normalize_component_name(name): entry for name, entry in host_components.items()}

    tests_by_key: dict[str, list[dict[str, Any]]] = {}
    display_name: dict[str, str] = {}
    for nodeid, entry in sorted(markers.items()):
        key = normalize_component_name(entry["component"])
        display_name.setdefault(key, entry["component"])
        tests_by_key.setdefault(key, []).append(
            {
                "id": nodeid,
                "kind": entry["kind"],
                "outcome": str(outcomes.get(nodeid) or "") if latest else "",
            }
        )

    def _entry(key: str) -> tuple[list[dict[str, Any]], str]:
        tests = tests_by_key.get(key, [])
        if latest is None:
            return tests, ("unchecked" if tests else "untested")
        host = host_by_key.get(key)
        if isinstance(host, dict) and isinstance(host.get("tests"), list):
            host_tests = [
                {
                    "id": str(test.get("id") or ""),
                    "kind": str(test.get("kind") or "invariant"),
                    "outcome": str(test.get("outcome") or ""),
                }
                for test in host["tests"]
                if isinstance(test, dict)
            ]
            # Markers added after the last run stay visible, without an outcome.
            known = {test["id"] for test in host_tests}
            host_tests.extend(test for test in tests if test["id"] not in known)
            status = str(host.get("status") or "") or component_status(host_tests)
            return host_tests, status
        return tests, component_status(tests)

    components: list[dict[str, Any]] = []
    named: set[str] = set()
    for row in card_rows:
        key = normalize_component_name(row["component"])
        named.add(key)
        tests, status = _entry(key)
        components.append(
            {
                "component": row["component"],
                "prescribes": row.get("prescribes", ""),
                "notes": row.get("notes", ""),
                "status": status,
                "tests": tests,
            }
        )
    unlisted: list[dict[str, Any]] = []
    for key in sorted(set(tests_by_key) | set(host_by_key)):
        if key in named:
            continue
        tests, status = _entry(key)
        unlisted.append(
            {
                "component": display_name.get(key) or key,
                "status": status,
                "tests": tests,
            }
        )
    return components, unlisted


# --------------------------------------------------------------------------
# Reused code: import scan
# --------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _iter_project_files(workdir: Path) -> list[Path]:
    roots: list[Path] = []
    for name in PROJECT_SCAN_ROOTS:
        candidate = workdir / name
        if candidate.is_dir():
            roots.append(candidate)
    try:
        for child in sorted(workdir.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith(".")
                and child.name not in SKIPPED_DIR_NAMES
                and child.name not in PROJECT_SCAN_ROOTS
                and (child / "__init__.py").is_file()
            ):
                roots.append(child)
    except OSError:
        pass
    files: list[Path] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not name.startswith(".") and name not in SKIPPED_DIR_NAMES
            )
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    files.append(Path(dirpath) / filename)
                    if len(files) >= MAX_PROJECT_FILES:
                        return files
    return files


def _project_module_roots(workdir: Path, files: list[Path]) -> set[str]:
    """Names an ``import`` could refer to inside the project itself."""
    roots: set[str] = set()
    for base in (workdir, workdir / "src", workdir / "scripts"):
        try:
            for child in base.iterdir():
                if child.name.startswith("."):
                    continue
                if child.is_dir() and (child / "__init__.py").is_file():
                    roots.add(child.name)
                elif child.suffix == ".py":
                    roots.add(child.stem)
        except OSError:
            continue
    for path in files:
        roots.add(path.stem)
    return roots


def _imports_of(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names if alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.append(node.module)
    return modules


def _third_party_dirs(workdir: Path) -> list[Path]:
    base = workdir / "third_party"
    try:
        return sorted(child for child in base.iterdir() if child.is_dir() and not child.name.startswith("."))
    except OSError:
        return []


def _declared_name(clone: Path) -> str:
    for filename in ("pyproject.toml", "setup.cfg", "setup.py"):
        text = _read_text(clone / filename)
        if not text:
            continue
        match = re.search(r"^\s*name\s*=\s*[\"']?([A-Za-z0-9_.\-]+)[\"']?", text, re.MULTILINE)
        if match:
            return match.group(1)
        match = re.search(r"name\s*=\s*[\"']([A-Za-z0-9_.\-]+)[\"']", text)
        if match:
            return match.group(1)
    return ""


def _clone_for_root(root: str, clones: list[Path]) -> Path | None:
    wanted = root.replace("-", "_").casefold()
    for clone in clones:
        if clone.name.replace("-", "_").casefold() == wanted:
            return clone
        for base in (clone, clone / "src"):
            if (base / root).is_dir() or (base / f"{root}.py").is_file():
                return clone
        if _declared_name(clone).replace("-", "_").casefold() == wanted:
            return clone
    return None


def _package_version(root: str) -> str:
    try:
        distributions = metadata.packages_distributions().get(root) or []
        for distribution in distributions:
            return metadata.version(distribution)
        return metadata.version(root)
    except Exception:  # noqa: BLE001 - fail-soft on any metadata oddity
        return ""


def reused_code(workdir: Path) -> list[dict[str, Any]]:
    """Third-party clones and installed packages the project's own code imports."""
    workdir = Path(workdir)
    files = _iter_project_files(workdir)
    if not files:
        return []
    own = _project_module_roots(workdir, files)
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    clones = _third_party_dirs(workdir)
    entries: dict[tuple[str, str], dict[str, Any]] = {}

    def _record(kind: str, name: str, module: str, source: Path) -> None:
        entry = entries.setdefault(
            (kind, name),
            {
                "name": name,
                "kind": kind,
                "revision_or_version": "",
                "remote": "",
                "modules": set(),
                "imported_from": set(),
            },
        )
        entry["modules"].add(module)
        try:
            entry["imported_from"].add(source.relative_to(workdir).as_posix())
        except ValueError:
            entry["imported_from"].add(source.as_posix())

    for path in files:
        for module in _imports_of(path):
            parts = module.split(".")
            root = parts[0]
            if root == "third_party":
                if len(parts) > 1:
                    _record("third_party", parts[1], module, path)
                continue
            if root in stdlib or root in own or root == "__future__":
                continue
            clone = _clone_for_root(root, clones)
            if clone is not None:
                _record("third_party", clone.name, module, path)
            else:
                _record("package", root, module, path)

    result: list[dict[str, Any]] = []
    for (kind, name), entry in sorted(entries.items(), key=lambda item: (item[0][0] != "third_party", item[0][1])):
        if kind == "third_party":
            clone = workdir / "third_party" / name
            entry["revision_or_version"] = _git(["rev-parse", "--short", "HEAD"], clone)
            entry["remote"] = _git(["config", "--get", "remote.origin.url"], clone)
        else:
            entry["revision_or_version"] = _package_version(name)
        entry["modules"] = sorted(entry["modules"])
        entry["imported_from"] = sorted(entry["imported_from"])
        result.append(entry)
    return result


# --------------------------------------------------------------------------
# Code anchors: ``# @component``, ``# @simplified``, ``# @reuses`` comments
# --------------------------------------------------------------------------


def _symbol_after(lines: list[str], index: int) -> str:
    """Name of the def/class the anchor at ``index`` sits above, or ''."""
    for line in lines[index + 1 : index + 12]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "@")):
            continue
        match = _DEF_OR_CLASS.match(line)
        return match.group("name") if match else ""
    return ""


def _anchor_excerpt(lines: list[str], index: int) -> str:
    """Up to ``ANCHOR_EXCERPT_LINES`` lines from the anchor, stopping before the
    next component's anchor once this one's code has begun."""
    chosen: list[str] = []
    in_code = False
    for line in lines[index : index + ANCHOR_EXCERPT_LINES]:
        stripped = line.strip()
        is_anchor = bool(_ANCHOR_COMPONENT.search(line) or _ANCHOR_SIMPLIFIED.search(line))
        if in_code and is_anchor:
            break
        if stripped and not stripped.startswith("#"):
            in_code = True
        chosen.append(line.rstrip())
    while chosen and not chosen[-1].strip():
        chosen.pop()
    return "\n".join(chosen)


def code_anchors(workdir: Path) -> dict[str, list[dict[str, Any]]]:
    """Anchor comments in the project's own Python files, grouped by kind.

    ``{"component": [...], "simplified": [...], "reuses": [...]}``; each entry
    carries ``file`` (project-relative), ``line`` (1-based) and, for component
    and simplified anchors, ``component``, ``symbol`` and ``excerpt`` (up to
    ``ANCHOR_EXCERPT_LINES`` lines from the anchor). Fail-soft: an unreadable
    file contributes nothing.
    """
    workdir = Path(workdir)
    found: dict[str, list[dict[str, Any]]] = {"component": [], "simplified": [], "reuses": []}
    total = 0
    for path in _iter_project_files(workdir):
        text = _read_text(path)
        if "@component" not in text and "@simplified" not in text and "@reuses" not in text:
            continue
        try:
            relative = path.relative_to(workdir).as_posix()
        except ValueError:
            relative = path.as_posix()
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if "#" not in line:
                continue
            component = _ANCHOR_COMPONENT.search(line)
            simplified = _ANCHOR_SIMPLIFIED.search(line)
            reuses = _ANCHOR_REUSES.search(line)
            if component is None and simplified is None and reuses is None:
                continue
            total += 1
            if total > MAX_ANCHORS:
                return found
            base = {"file": relative, "line": index + 1}
            if reuses is not None:
                found["reuses"].append({**base, "what": reuses.group("what"), "note": reuses.group("note")})
                continue
            match = component if component is not None else simplified
            assert match is not None
            entry = {
                **base,
                "component": _strip_markup(match.group("component")),
                "symbol": _symbol_after(lines, index),
                "excerpt": _anchor_excerpt(lines, index),
            }
            if simplified is not None:
                entry["reason"] = simplified.group("reason").strip()
                found["simplified"].append(entry)
            else:
                found["component"].append(entry)
    return found


def _attach_anchors(
    components: list[dict[str, Any]],
    unlisted: list[dict[str, Any]],
    anchors: dict[str, list[dict[str, Any]]],
) -> None:
    """Add ``anchors``/``simplified`` to every row and list anchor-only components."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    simplified_by_key: dict[str, list[dict[str, Any]]] = {}
    display: dict[str, str] = {}
    for entry in anchors.get("component", []):
        key = normalize_component_name(entry["component"])
        display.setdefault(key, entry["component"])
        by_key.setdefault(key, []).append(
            {k: entry[k] for k in ("file", "line", "symbol", "excerpt")}
        )
    for entry in anchors.get("simplified", []):
        key = normalize_component_name(entry["component"])
        display.setdefault(key, entry["component"])
        simplified_by_key.setdefault(key, []).append(
            {"reason": entry["reason"], "file": entry["file"], "line": entry["line"]}
        )
    seen: set[str] = set()
    for row in [*components, *unlisted]:
        key = normalize_component_name(row["component"])
        seen.add(key)
        row["anchors"] = by_key.get(key, [])
        row["simplified"] = simplified_by_key.get(key, [])
        if row["simplified"] and "(simplified)" not in row["status"]:
            row["status"] = f"{row['status']} (simplified)"
    for key in sorted(set(by_key) | set(simplified_by_key)):
        if key in seen:
            continue
        status = "untested"
        if simplified_by_key.get(key):
            status += " (simplified)"
        unlisted.append(
            {
                "component": display.get(key) or key,
                "status": status,
                "tests": [],
                "anchors": by_key.get(key, []),
                "simplified": simplified_by_key.get(key, []),
            }
        )


# --------------------------------------------------------------------------
# Hyperparameters: config files with ``# why`` comments
# --------------------------------------------------------------------------


def _config_files(workdir: Path, protocol: str) -> list[Path]:
    seen: list[Path] = []
    configs = workdir / "configs"
    if configs.is_dir():
        for path in sorted(configs.rglob("*")):
            if path.is_file() and path.suffix.lower() in CONFIG_SUFFIXES and not any(
                part.startswith(".") for part in path.relative_to(workdir).parts
            ):
                seen.append(path)
    for reference in _CONFIG_REFERENCE.findall(protocol or ""):
        candidate = (workdir / reference.lstrip("./")).resolve()
        try:
            candidate.relative_to(workdir.resolve())
        except ValueError:
            continue
        if candidate.is_file() and candidate not in seen:
            seen.append(candidate)
    return seen[:MAX_CONFIG_FILES]


def _parse_config(path: Path) -> Any:
    text = _read_text(path)
    if not text.strip():
        return None
    suffix = path.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            import yaml  # type: ignore[import-untyped]

            return yaml.safe_load(text)
        if suffix == ".json":
            return json.loads(text)
        if suffix == ".toml":
            import tomllib

            return tomllib.loads(text)
    except Exception:  # noqa: BLE001 - malformed config is not our failure
        return None
    return None


def _scalar_text(value: Any) -> str | None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return json.dumps(value) if isinstance(value, bool) or value is None else str(value)
    if isinstance(value, (list, tuple)) and all(
        item is None or isinstance(item, (bool, int, float, str)) for item in value
    ):
        return json.dumps(list(value), ensure_ascii=False)
    return None


def _flatten(value: Any, prefix: str, out: dict[str, str]) -> None:
    if len(out) >= MAX_HYPERPARAMETERS:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            _flatten(child, dotted, out)
        return
    text = _scalar_text(value)
    if text is not None and prefix:
        out[prefix] = text


def yaml_why_comments(text: str) -> dict[str, str]:
    """``{dotted.key: why}`` for ``# why: ...`` comments beside or above a value.

    Block-style YAML only; a flow-style or multi-document file yields what its
    plain ``key:`` lines allow. Fail-soft by construction.
    """
    whys: dict[str, str] = {}
    stack: list[tuple[int, str]] = []
    pending_why = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            pending_why = ""
            continue
        if stripped.startswith("#"):
            match = _WHY_COMMENT.search(stripped)
            pending_why = match.group("why") if match else ""
            continue
        match = _YAML_KEY_LINE.match(raw)
        if not match:
            pending_why = ""
            continue
        indent = len(match.group("indent").expandtabs(2))
        key = match.group("key").strip().strip("\"'")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = ".".join([*(name for _level, name in stack), key])
        stack.append((indent, key))
        inline = _WHY_COMMENT.search(match.group("rest"))
        why = inline.group("why") if inline else pending_why
        if why:
            whys[dotted] = why
        pending_why = ""
    return whys


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=".tmp-", suffix=".json", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(handle.name, path)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def hyperparameters(workdir: Path, protocol: str = "") -> list[dict[str, Any]]:
    """Flattened scalar config values with ``why`` and a diff against the previous snapshot.

    The snapshot under ``.argus/method-card/hyperparameters.json`` keeps the
    current values and the last *different* values, so repeated derivations
    (Atlas polling, the Reviewer's context) report the same ``changed`` flags
    until a value actually moves again.
    """
    workdir = Path(workdir)
    current: dict[str, dict[str, Any]] = {}
    for path in _config_files(workdir, protocol):
        parsed = _parse_config(path)
        if not isinstance(parsed, dict):
            continue
        flat: dict[str, str] = {}
        _flatten(parsed, "", flat)
        whys = yaml_why_comments(_read_text(path)) if path.suffix.lower() in {".yaml", ".yml"} else {}
        try:
            relative = path.relative_to(workdir).as_posix()
        except ValueError:
            relative = path.as_posix()
        for key, value in flat.items():
            if len(current) >= MAX_HYPERPARAMETERS:
                break
            current[f"{relative}::{key}"] = {
                "key": key,
                "value": value,
                "file": relative,
                "why": whys.get(key, ""),
            }

    snapshot_path = workdir / HYPERPARAMETER_SNAPSHOT_PATH
    snapshot = _load_json(snapshot_path) or {}
    stored = snapshot.get("values") if isinstance(snapshot.get("values"), dict) else {}
    previous = snapshot.get("previous") if isinstance(snapshot.get("previous"), dict) else {}
    current_values = {ident: entry["value"] for ident, entry in current.items()}
    if not snapshot:
        previous = {}
    elif current_values != stored:
        previous = dict(stored)
    if not snapshot or current_values != stored:
        try:
            _write_json_atomic(
                snapshot_path,
                {"generated_at": time.time(), "values": current_values, "previous": previous},
            )
        except Exception:  # noqa: BLE001 - the snapshot is a convenience
            log.debug("method card: hyperparameter snapshot not written", exc_info=True)

    result: list[dict[str, Any]] = []
    for ident, entry in current.items():
        before = previous.get(ident) if previous else None
        changed = bool(previous) and before != entry["value"]
        result.append(
            {
                "key": entry["key"],
                "value": entry["value"],
                "file": entry["file"],
                "why": entry["why"],
                "changed": changed,
                "previous": str(before) if changed and before is not None else None,
            }
        )
    return result


# --------------------------------------------------------------------------
# Change log: git history
# --------------------------------------------------------------------------


def change_log(workdir: Path) -> list[dict[str, Any]]:
    """Recent commits touching the card, the code, the configs, the spec or the clones."""
    output = _git(
        [
            "log",
            f"-n{CHANGE_LOG_ENTRIES}",
            "--date=short",
            "--format=%ad%x09%s",
            "--name-only",
            "--",
            *CHANGE_LOG_PATHS,
        ],
        Path(workdir),
    )
    if not output:
        return []
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in output.splitlines():
        head = _CHANGE_LOG_HEAD.match(line)
        if head:
            current = {"when": head.group("when"), "summary": head.group("summary").strip(), "files": []}
            entries.append(current)
        elif line.strip() and current is not None:
            current["files"].append(line.strip())
    return entries


def changed_files(workdir: Path) -> list[dict[str, str]]:
    """``git status --porcelain`` as ``[{status, path}]``; empty outside a repository."""
    # Not ``_git``: that strips the output, and the first porcelain line's
    # status column may begin with the space that marks an unstaged change.
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        match = _PORCELAIN_LINE.match(line)
        if match is None:
            continue
        status, path = match.group("status").strip() or "M", match.group("path").strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            entries.append({"status": status, "path": path})
    return entries


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def _empty_card() -> dict[str, Any]:
    return {
        "exists": False,
        "path": METHOD_FILENAME,
        "updated_at": None,
        "title": "",
        "statement": "",
        "markdown": "",
        "truncated": False,
        "components": [],
        "unlisted_components": [],
        "protocol": "",
        "falsifiers": "",
        "reused_code": [],
        "reuse_anchors": [],
        "hyperparameters": [],
        "change_log": [],
        "checks": None,
    }


def _checks_summary(latest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(latest, dict):
        return None
    counts = latest.get("counts")
    return {
        "round_index": latest.get("round_index"),
        "ran_at": latest.get("ran_at"),
        "exit_code": latest.get("exit_code"),
        "counts": dict(counts) if isinstance(counts, dict) else {},
    }


def derive_method_card(workdir: Path) -> dict[str, Any]:
    """Hand-written ``METHOD.md`` plus everything derived from the project, JSON-ready.

    Never raises: a project without a card yields ``exists=False`` and empty
    fields; a failing sub-derivation yields its empty value.
    """
    workdir = Path(workdir)
    card = _empty_card()
    path = workdir / METHOD_FILENAME
    if not path.is_file():
        return card
    markdown = _read_text(path)
    card["exists"] = True
    try:
        card["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        card["updated_at"] = None
    card["title"] = method_title(markdown)
    card["statement"] = method_statement(markdown)
    card["truncated"] = len(markdown.encode("utf-8")) > MARKDOWN_LIMIT
    card["markdown"] = (
        markdown.encode("utf-8")[:MARKDOWN_LIMIT].decode("utf-8", errors="ignore")
        if card["truncated"]
        else markdown
    )
    _preamble, sections = _sections(markdown)
    card["protocol"] = sections.get(PROTOCOL_HEADING, "")
    card["falsifiers"] = sections.get(FALSIFIERS_HEADING, "")

    latest = load_latest_checks(workdir)
    try:
        components, unlisted = _join_components(
            method_components(markdown), load_spec_components(workdir), latest
        )
        card["components"], card["unlisted_components"] = components, unlisted
    except Exception:  # noqa: BLE001
        log.debug("method card: component join failed", exc_info=True)
    try:
        anchors = code_anchors(workdir)
        _attach_anchors(card["components"], card["unlisted_components"], anchors)
        card["reuse_anchors"] = anchors.get("reuses", [])
    except Exception:  # noqa: BLE001
        log.debug("method card: anchor scan failed", exc_info=True)
        for row in [*card["components"], *card["unlisted_components"]]:
            row.setdefault("anchors", [])
            row.setdefault("simplified", [])
    card["checks"] = _checks_summary(latest)
    for key, derive in (
        ("reused_code", lambda: reused_code(workdir)),
        ("hyperparameters", lambda: hyperparameters(workdir, card["protocol"])),
        ("change_log", lambda: change_log(workdir)),
    ):
        try:
            card[key] = derive()
        except Exception:  # noqa: BLE001
            log.debug("method card: %s derivation failed", key, exc_info=True)
    return card


def _count_outcomes(tests: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for test in tests:
        outcome = str(test.get("outcome") or "no run").lower()
        counts[outcome] = counts.get(outcome, 0) + 1
    return ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))


def anchor_location(entry: dict[str, Any]) -> str:
    """``file:line (symbol)`` of a row's first anchor, or '' when it has none."""
    anchors = entry.get("anchors") or []
    if not anchors:
        return ""
    first = anchors[0]
    symbol = f" ({first['symbol']})" if first.get("symbol") else ""
    more = f" +{len(anchors) - 1} more" if len(anchors) > 1 else ""
    return f"{first['file']}:{first['line']}{symbol}{more}"


def failing_test_ids(entry: dict[str, Any]) -> list[str]:
    return [
        str(test.get("id") or "")
        for test in entry.get("tests") or []
        if str(test.get("outcome") or "").upper() in {"FAILED", "ERROR"}
    ]


def _excerpt_lines(entry: dict[str, Any], budget: int) -> list[str]:
    anchors = entry.get("anchors") or []
    if not anchors or budget <= 0:
        return []
    raw = str(anchors[0].get("excerpt") or "").splitlines()
    shown = raw[: min(budget, REVIEWER_EXCERPT_LINES)]
    lines = [f"    {line.rstrip()}" for line in shown]
    if len(raw) > len(shown):
        lines.append("    ...")
    return lines


def render_for_reviewer(workdir: Path) -> str:
    """The review packet: at most ``REVIEWER_MAX_LINES`` lines beside ``METHOD.md``.

    Per component its status, anchor and failing tests, then the code excerpt
    at the anchor (trimmed to fit), then what changed this round: files from
    ``git status``, hyperparameters against the previous snapshot, anchors
    without a card row and card rows without an anchor. '' without a card.
    """
    try:
        card = derive_method_card(Path(workdir))
    except Exception:  # noqa: BLE001
        return ""
    if not card.get("exists"):
        return ""
    lines: list[str] = ["## Method card, derived by the host (evidence, not a gate)"]
    components = card["components"]
    excerpt_slots: list[tuple[int, dict[str, Any]]] = []
    if components:
        lines.append(
            "Component status from tests/spec markers joined with the host-run checks; "
            "the anchor is the `# @component` comment in the code:"
        )
        for entry in components[:12]:
            tests = entry["tests"]
            detail = f" ({len(tests)} tests: {_count_outcomes(tests)})" if tests else ""
            where = anchor_location(entry)
            lines.append(
                f"- {entry['component']}: {entry['status']}{detail}"
                + (f" — {where}" if where else " — no code anchor")
            )
            for note in (entry.get("simplified") or [])[:2]:
                lines.append(f"  simplified: {note['reason']} ({note['file']}:{note['line']})")
            failing = failing_test_ids(entry)
            if failing:
                lines.append("  failing: " + ", ".join(failing[:5]))
            if entry.get("anchors"):
                excerpt_slots.append((len(lines), entry))
        if len(components) > 12:
            lines.append(f"- ... {len(components) - 12} more components in METHOD.md")
    else:
        lines.append("METHOD.md has no Components table.")
    untested = [
        entry["component"] for entry in components if entry["status"].startswith("untested")
    ]
    if untested:
        lines.append("Named in the card, no test carries its marker: " + ", ".join(untested[:10]))
    without_anchor = [entry["component"] for entry in components if not entry.get("anchors")]
    if without_anchor:
        lines.append("Named in the card, no `# @component` anchor in the code: " + ", ".join(without_anchor[:10]))
    unlisted_markers = [
        entry["component"] for entry in card["unlisted_components"] if entry.get("tests")
    ]
    if unlisted_markers:
        lines.append("Markers without a card row: " + ", ".join(unlisted_markers[:10]))
    unlisted_anchors = [
        f"{entry['component']} ({anchor_location(entry)})"
        for entry in card["unlisted_components"]
        if entry.get("anchors") and not entry.get("tests")
    ]
    if unlisted_anchors:
        lines.append("Anchors without a card row: " + ", ".join(unlisted_anchors[:10]))
    changed_now = changed_files(Path(workdir))
    if changed_now:
        lines.append(f"Files changed this round ({len(changed_now)} in git status):")
        lines.extend(f"- {entry['status']} {entry['path']}" for entry in changed_now[:REVIEWER_CHANGED_FILES])
        if len(changed_now) > REVIEWER_CHANGED_FILES:
            lines.append(f"- ... {len(changed_now) - REVIEWER_CHANGED_FILES} more")
    reused = card["reused_code"]
    if reused:
        lines.append("Reused code (import scan of the project's own files):")
        for entry in reused[:8]:
            if entry["kind"] == "third_party":
                where = f"third_party/{entry['name']}"
                pin = entry["revision_or_version"] or "unpinned"
                remote = f" from {entry['remote']}" if entry["remote"] else ""
            else:
                where = entry["name"]
                pin = entry["revision_or_version"] or "version unknown"
                remote = ""
            modules = ", ".join(entry["modules"][:4])
            lines.append(f"- {where} @ {pin}{remote}: {modules}")
    changed = [entry for entry in card["hyperparameters"] if entry["changed"]]
    if changed:
        lines.append("Hyperparameters changed since the previous snapshot:")
        for entry in changed[:8]:
            why = f" (why: {entry['why']})" if entry["why"] else " (no '# why' comment)"
            lines.append(
                f"- {entry['file']}:{entry['key']} {entry['previous']} -> {entry['value']}{why}"
            )
    checks = card["checks"]
    if checks:
        counts = ", ".join(f"{n} {outcome.lower()}" for outcome, n in sorted(checks["counts"].items()))
        lines.append(
            f"Latest host-run check: round {checks['round_index']}, exit {checks['exit_code']}"
            + (f", {counts}" if counts else "")
        )
    else:
        lines.append("No host-run check recorded yet.")
    lines = lines[:REVIEWER_MAX_LINES]
    # Code excerpts fill what the cap leaves, contradicted components first,
    # inserted under their own status line (later slots first so indices hold).
    remaining = REVIEWER_MAX_LINES - len(lines)
    ordered = sorted(
        excerpt_slots,
        key=lambda slot: (STATUSES.index(slot[1]["status"].split(" ")[0])
                          if slot[1]["status"].split(" ")[0] in STATUSES else len(STATUSES)),
    )
    granted: list[tuple[int, list[str]]] = []
    for position, entry in ordered:
        if remaining <= 2:
            break
        share = max(3, remaining // max(1, len(ordered) - len(granted)))
        excerpt = _excerpt_lines(entry, min(share, remaining) - 1)
        if excerpt:
            granted.append((position, excerpt))
            remaining -= len(excerpt)
    for position, excerpt in sorted(granted, key=lambda item: item[0], reverse=True):
        lines[position:position] = excerpt
    return "\n".join(lines[:REVIEWER_MAX_LINES])
