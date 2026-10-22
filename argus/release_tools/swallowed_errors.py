"""Inventory of exception handlers that can swallow errors in ``argus/``.

Roadmap CO-1 and CO-3. The scan is purely syntactic (``ast``), so it runs in a
few seconds without importing anything. Two kinds of handler are recorded:

- **broad**: a bare ``except:``, or one whose caught types include
  ``Exception`` or ``BaseException``. Every broad handler is listed, whatever it
  does, and the per-package count is the CO-3 baseline that may only go down.
- **default on error**: a narrower handler whose body ends in a success-shaped
  default (``return`` of ``None``/``""``/``[]``/``{}``/``()``/``False``/``0``,
  or only ``pass``/``continue``/``break``) — the pattern the mechanical rule in
  ``docs/simplification-plan.md`` says to delete or propagate for state,
  prompts, routing, checklists and stage transitions.

``python -m argus.release_tools.swallowed_errors --write-doc`` regenerates
``docs/audits/swallowed-errors.md``; ``--write-baseline`` rewrites the counts
that ``tests/tools/test_swallowed_errors_baseline.py`` holds the tree to.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "argus"
DOC_PATH = ROOT / "docs" / "audits" / "swallowed-errors.md"
BASELINE_PATH = ROOT / "tests" / "tools" / "swallowed_errors_baseline.json"
ROOT_PACKAGE = "<root>"  # argus/__init__.py, loop.py, ... (as in docs/LAYOUT.md)

_BROAD_NAMES = frozenset({"Exception", "BaseException"})
_EMPTY_CALLS = frozenset({"list", "dict", "set", "tuple", "frozenset", "str"})

# The task main path: a backlog item from daemon dispatch to settlement.
# Derived from docs/LAYOUT.md ("Where to look") and the reading table and
# single-mission order in docs/runtime-maintainability.md. A path ending in
# "/" covers the whole directory. First match wins.
MAIN_PATH: tuple[tuple[str, str], ...] = (
    ("daemon/life_worker.py", "daemon dispatch"),
    ("daemon/_life_worker_", "daemon dispatch"),
    ("daemon/commands.py", "daemon dispatch"),
    ("daemon/handoff.py", "daemon handoff"),
    ("daemon/state.py", "daemon dispatch"),
    ("life/supervisor/_mission_execution_settlement.py", "settlement"),
    ("life/supervisor/", "mission loop"),
    ("life/memory.py", "mission loop (backlog)"),
    ("life/event_log.py", "mission loop (events)"),
    ("apps/_runtime", "runner assembly"),
    ("apps/_self_reply.py", "runner assembly"),
    ("loop.py", "round loop"),
    ("skills/loop_settlement.py", "settlement"),
    ("skills/loop_", "round loop"),
    ("engineer/round_settlement.py", "settlement"),
    ("engineer/", "Engineer turn"),
    ("reviewer/", "Reviewer turn"),
    ("planner/", "Planner turn"),
    ("manager/front_door.py", "Manager turn"),
    ("manager/_front_door_ops.py", "Manager turn"),
    ("manager/_core.py", "Manager turn"),
    ("manager/_stage_ops.py", "Manager turn (stage)"),
    ("manager/stage_decider.py", "Manager turn (stage)"),
    ("manager/dispatch.py", "Manager turn"),
    ("manager/supervision.py", "Manager turn"),
    ("skills/stage_machine.py", "stage transition"),
    ("skills/checklist_store.py", "stage transition"),
    ("core/pipeline_state.py", "stage transition"),
    ("core/project_api.py", "settlement (project completion)"),
    ("core/role_handoff.py", "role handoff"),
    ("core/role_decision.py", "role handoff"),
    ("core/role_reply.py", "role handoff"),
    ("core/role_session.py", "role handoff"),
    ("life/context_packet.py", "role handoff"),
    ("core/run_gateway.py", "model call"),
)


@dataclass(frozen=True)
class Handler:
    path: str  # relative to argus/, POSIX separators
    line: int
    function: str
    caught: str
    broad: bool
    outcome: str  # "raises", "returns default", "pass/continue", "other"

    @property
    def package(self) -> str:
        head, sep, _ = self.path.partition("/")
        return head if sep else ROOT_PACKAGE

    @property
    def main_path(self) -> str | None:
        for prefix, stage in MAIN_PATH:
            if self.path == prefix or (self.path.startswith(prefix) and not prefix.endswith(".py")):
                return stage
        return None

    @property
    def swallowing(self) -> bool:
        return self.outcome in {"returns default", "pass/continue"}


def _is_broad(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_is_broad(item) for item in node.elts)
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    return name in _BROAD_NAMES


def _is_default(value: ast.expr | None) -> bool:
    if value is None:
        return True
    if isinstance(value, ast.Constant):
        return value.value in (None, False, 0, "", b"") or value.value is ...
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id in _EMPTY_CALLS and not value.args and not value.keywords
    return False


def _own_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Nodes of ``body`` without descending into nested functions or classes."""
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    stack: list[ast.AST] = [node for node in body if not isinstance(node, scopes)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, scopes))


def _outcome(handler: ast.ExceptHandler) -> str:
    if any(isinstance(node, ast.Raise) for node in _own_nodes(handler.body)):
        return "raises"
    last = handler.body[-1]
    if isinstance(last, ast.Return) and _is_default(last.value):
        return "returns default"
    if all(isinstance(stmt, (ast.Pass, ast.Continue, ast.Break))
           or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
           for stmt in handler.body):
        return "pass/continue"
    return "other"


def _handlers(tree: ast.AST, path: str) -> Iterator[Handler]:
    def visit(node: ast.AST, scope: str) -> Iterator[Handler]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield from visit(child, f"{scope}.{child.name}" if scope else child.name)
                continue
            if isinstance(child, ast.ExceptHandler):
                yield Handler(
                    path=path, line=child.lineno, function=scope or "<module>",
                    caught="(bare)" if child.type is None else ast.unparse(child.type),
                    broad=_is_broad(child.type), outcome=_outcome(child),
                )
            yield from visit(child, scope)

    yield from visit(tree, "")


def scan(package: Path = PACKAGE) -> list[Handler]:
    """Broad handlers, and narrower ones that return a default or pass."""
    found: list[Handler] = []
    for file in sorted(package.rglob("*.py")):
        relative = file.relative_to(package).as_posix()
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        found.extend(h for h in _handlers(tree, relative) if h.broad or h.swallowing)
    return found


def broad_counts(handlers: list[Handler]) -> dict[str, int]:
    """Per-package count of broad handlers: the CO-3 baseline."""
    counts = Counter(h.package for h in handlers if h.broad)
    return dict(sorted(counts.items()))


def _row(h: Handler) -> str:
    caught = h.caught.replace("|", "\\|")
    return f"| `{h.path}:{h.line}` | `{h.function}` | `{caught}` | {h.outcome} |"


def render(handlers: list[Handler]) -> str:
    packages = sorted({h.package for h in handlers})
    broad = [h for h in handlers if h.broad]
    narrow = [h for h in handlers if not h.broad]
    main = [h for h in handlers if h.main_path]
    lines = [
        "# Swallowed errors inventory (CO-1)",
        "",
        "Generated by `python -m argus.release_tools.swallowed_errors --write-doc`; do not edit by hand.",
        "It is a snapshot: line numbers drift with every edit. Regenerate it before working from it.",
        "",
        "What is listed, per package of `argus/`:",
        "",
        "- **Broad**: bare `except:` or a handler whose caught types include `Exception` or",
        "  `BaseException`. All of them are listed. Their per-package count is the baseline in",
        "  `tests/tools/swallowed_errors_baseline.json`, which `tests/tools/test_swallowed_errors_baseline.py`",
        "  only lets go down (CO-3).",
        "- **Default on error**: a narrower handler whose body ends in `return` of `None`, `\"\"`,",
        "  `[]`, `{}`, `()`, `False` or `0`, or is only `pass`/`continue`/`break`.",
        "",
        "Outcome of the handler body: `raises` (contains a `raise`, possibly conditional), `returns default`,",
        "`pass/continue`, or `other` (logs, records, assigns a fallback or returns some other value; read",
        "the code). The scan is syntactic and does not judge whether a handler is justified; the rule for that",
        "is \"A mechanical rule for the 2,277 `try:` blocks\" in `docs/simplification-plan.md`.",
        "",
        "## Totals",
        "",
        f"- Broad handlers: {len(broad)} "
        f"({sum(1 for h in broad if h.swallowing)} return a default or pass, "
        f"{sum(1 for h in broad if h.outcome == 'raises')} raise).",
        f"- Narrower handlers that return a default or pass: {len(narrow)}.",
        f"- On the task main path: {sum(1 for h in main if h.broad)} broad, "
        f"{sum(1 for h in main if not h.broad)} narrower default-on-error.",
        "",
        "| Package | Broad | Broad, returns default or pass | Default on error (narrower) | On main path |",
        "|---|---:|---:|---:|---:|",
    ]
    for package in packages:
        own = [h for h in handlers if h.package == package]
        lines.append(
            f"| `{package}` | {sum(h.broad for h in own)} | {sum(h.broad and h.swallowing for h in own)} "
            f"| {sum(not h.broad for h in own)} | {sum(bool(h.main_path) for h in own)} |"
        )
    lines += [
        "",
        "## Task main path",
        "",
        "One backlog item from daemon dispatch to settlement: the detached worker and blue/green handoff",
        "(`daemon/`), `LifeSupervisor` and the `Backlog` (`life/`), the runner assembly (`apps/_runtime*`),",
        "the round loop (`loop.py`, `skills/loop_*`), the Engineer, Reviewer and Planner turns, the Manager",
        "front door and stage authority, the stage machine, role handoff, and project completion. The module",
        "list is `MAIN_PATH` in `argus/release_tools/swallowed_errors.py`, taken from `docs/LAYOUT.md`",
        "(\"Where to look\") and `docs/runtime-maintainability.md`. These are the first candidates for CO-2.",
        "",
    ]
    stages: dict[str, list[Handler]] = {}
    for prefix_stage in dict.fromkeys(stage for _, stage in MAIN_PATH):
        stages[prefix_stage] = [h for h in main if h.main_path == prefix_stage]
    for stage, own in stages.items():
        if not own:
            continue
        lines += [f"### {stage} ({sum(h.broad for h in own)} broad, {sum(not h.broad for h in own)} narrower)", "",
                  "| Location | Function | Catches | Outcome |", "|---|---|---|---|"]
        lines += [_row(h) for h in own]
        lines.append("")
    lines += ["## Per package", ""]
    for package in packages:
        own = [h for h in handlers if h.package == package]
        lines += [f"### `{package}`", ""]
        for title, subset in (("Broad", [h for h in own if h.broad]),
                              ("Default on error (narrower)", [h for h in own if not h.broad])):
            if not subset:
                continue
            lines += [f"{title} ({len(subset)}; main path marked with *):", "",
                      "| Location | Function | Catches | Outcome |", "|---|---|---|---|"]
            lines += [_row(h).replace("| `", "| *`", 1) if h.main_path else _row(h) for h in subset]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-doc", action="store_true", help=f"rewrite {DOC_PATH.relative_to(ROOT)}")
    parser.add_argument("--write-baseline", action="store_true", help=f"rewrite {BASELINE_PATH.relative_to(ROOT)}")
    args = parser.parse_args(argv)
    handlers = scan()
    if args.write_doc:
        DOC_PATH.write_text(render(handlers), encoding="utf-8")
    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(broad_counts(handlers), indent=2) + "\n", encoding="utf-8")
    counts = broad_counts(handlers)
    print(f"{sum(counts.values())} broad handlers in {len(counts)} packages; "
          f"{sum(not h.broad for h in handlers)} narrower handlers return a default or pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
