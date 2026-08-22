"""List likely maintenance problems without deciding what to edit."""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_IGNORED = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv",
    ".worktrees", "__pycache__", "build", "bundle", "dist", "node_modules",
    "site-packages", "technical_report",
}
_TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".ts", ".tsx", ".yaml", ".yml"}
_DIGEST = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,64}(?![0-9A-Fa-f])")
_MACHINE_PATH = re.compile(
    r"(?:/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+|"
    r"[A-Za-z]:\\Users\\[A-Za-z0-9._-]+)"
)
_HARDWARE = re.compile(r"\b(?:A100|B100|B200|H100|H200|GB200|RTX\s?[0-9]{4})\b", re.I)
_GENERIC_PARTS = {
    "adapters", "agent_cli", "apps", "builtin_skills", "core", "engineer",
    "life", "manager", "planner", "reviewer", "roles", "webapi",
}
_ALLOWED_VERTICAL_MODULES = {"_base", "_data_domain", "_registry"}


@dataclass(frozen=True)
class Finding:
    category: str
    path: str
    line: int
    evidence: str


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_test(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return (
        bool({"test", "tests", "__tests__"} & set(rel.parts))
        or path.name.startswith("test_")
        or ".test." in path.name
        or ".spec." in path.name
    )


def _is_vertical(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return len(parts) >= 3 and parts[:2] == ("argus_skill", "verticals")


def _files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _TEXT_SUFFIXES
        and not any(part in _IGNORED or part.startswith(".argus") for part in path.relative_to(root).parts)
    ]


def _line(lines: list[str], number: int) -> str:
    return lines[number - 1].strip()[:240] if 1 <= number <= len(lines) else ""


def _name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _vertical_names(root: Path) -> set[str]:
    selector = root / "argus_skill" / "skills" / "vertical_select.py"
    try:
        tree = ast.parse(selector.read_text(encoding="utf-8"))
        for node in tree.body:
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "VERTICALS"
            ):
                return set(ast.literal_eval(node.value))
    except (OSError, SyntaxError, ValueError):
        return set()
    return set()


def _thin_wrapper(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body.pop(0)
    if node.decorator_list or len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    call = body[0].value
    if not isinstance(call, ast.Call):
        return False
    params = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args) if arg.arg not in {"self", "cls"}]
    forwarded = [arg.id for arg in call.args if isinstance(arg, ast.Name)]
    forwarded += [
        kw.value.id
        for kw in call.keywords
        if kw.arg and isinstance(kw.value, ast.Name)
    ]
    return bool(params) and len(forwarded) == len(call.args) + len(call.keywords) and sorted(params) == sorted(forwarded)


def _python_findings(path: Path, root: Path, vertical_names: set[str]) -> list[Finding]:
    rel = _relative(path, root)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    source = "\n".join(lines)
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return [Finding("syntax_error", rel, int(exc.lineno or 0), str(exc.msg))]
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    findings: list[Finding] = []
    branch_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            findings.append(Finding("runtime_assert", rel, node.lineno, _line(lines, node.lineno)))
        if isinstance(node, ast.ExceptHandler):
            broad = node.type is None or _name(node.type).split(".")[-1] in {"Exception", "BaseException"}
            if broad and node.body and all(isinstance(statement, ast.Pass) for statement in node.body):
                findings.append(Finding("silent_broad_exception", rel, node.lineno, _line(lines, node.lineno)))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = int(getattr(node, "end_lineno", node.lineno) - node.lineno + 1)
            if size > 120:
                findings.append(Finding("oversized_function", rel, node.lineno, f"{node.name}: {size} lines"))
            if _thin_wrapper(node):
                findings.append(Finding("thin_wrapper", rel, node.lineno, node.name))
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) >= 3:
            parent = parents.get(node)
            guard = isinstance(parent, (ast.Assert, ast.If, ast.While)) or any(
                isinstance(value, ast.Compare)
                or (isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not))
                for value in node.values
            )
            if not guard and not isinstance(parent, ast.BoolOp):
                findings.append(Finding("fallback_chain", rel, node.lineno, _line(lines, node.lineno)))
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not _is_vertical(path, root):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for module in modules:
                parts = module.split(".")
                if "verticals" not in parts:
                    continue
                index = parts.index("verticals")
                concrete = parts[index + 1] if index + 1 < len(parts) else ""
                if concrete and concrete not in _ALLOWED_VERTICAL_MODULES:
                    findings.append(Finding("concrete_vertical_import", rel, node.lineno, _line(lines, node.lineno)))
        if (
            rel != "argus_skill/skills/vertical_select.py"
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in vertical_names
        ):
            parent = parents.get(node)
            for _ in range(3):
                if isinstance(parent, (ast.Compare, ast.MatchValue)):
                    segment = ast.get_source_segment(source, parent) or ""
                    line = int(getattr(parent, "lineno", node.lineno))
                    if "vertical" in segment.lower() and line not in branch_lines:
                        branch_lines.add(line)
                        findings.append(Finding("concrete_vertical_branch", rel, line, _line(lines, line)))
                    break
                parent = parents.get(parent) if parent is not None else None
    return findings


def _text_findings(path: Path, root: Path) -> list[Finding]:
    rel = _relative(path, root)
    parts = set(path.relative_to(root).parts)
    generic = bool(parts & _GENERIC_PARTS) and not _is_vertical(path, root)
    findings: list[Finding] = []
    for number, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        machine = _MACHINE_PATH.search(text)
        if machine and machine.group(0).split("/")[-1] != "...":
            findings.append(Finding("machine_specific_path", rel, number, machine.group(0)))
        digest = _DIGEST.search(text)
        if digest and path.name not in {"package-lock.json", "release.generated.ts", "release_manifest.json"}:
            findings.append(Finding("hardcoded_digest", rel, number, digest.group(0)))
        hardware = _HARDWARE.search(text)
        if hardware and generic:
            findings.append(Finding("domain_literal_outside_vertical", rel, number, hardware.group(0)))
    return findings


def scan_repository(project_root: Path | str) -> dict:
    root = Path(project_root).expanduser().resolve()
    files = _files(root)
    vertical_names = _vertical_names(root)
    findings: list[Finding] = []
    for path in files:
        if _is_test(path, root):
            continue
        if path.suffix == ".py":
            findings.extend(_python_findings(path, root, vertical_names))
        findings.extend(_text_findings(path, root))
    unique = {
        (row.category, row.path, row.line, row.evidence): row
        for row in findings
    }
    ordered = sorted(unique.values(), key=lambda row: (row.category, row.path, row.line))
    counts = Counter(row.category for row in ordered)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files_scanned": len(files),
        "counts": {"total": len(ordered), "by_category": dict(sorted(counts.items()))},
        "findings": [asdict(row) for row in ordered],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default="research/ARCHITECTURE_AUDIT.json")
    args = parser.parse_args(argv)
    report = scan_repository(args.project_root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output == "-":
        print(rendered, end="")
    else:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        print(f"architecture audit: {report['counts']['total']} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
