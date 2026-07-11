from __future__ import annotations

import ast
from pathlib import Path


def test_skill_and_wiki_emitters_use_canonical_event_constants() -> None:
    root = Path(__file__).parents[2] / "argus_skill"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "event_catalog.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "type"):
                    continue
                literal = value.value if isinstance(value, ast.Constant) else ""
                if isinstance(value, ast.JoinedStr):
                    literal = "".join(
                        part.value
                        for part in value.values
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    )
                if isinstance(literal, str) and literal.startswith(("skill.", "wiki.")):
                    violations.append(
                        f"{path.relative_to(root)}:{value.lineno}:{literal}"
                    )

    assert violations == [], (
        "skill/wiki event emitters must use EventType constants:\n"
        + "\n".join(violations)
    )
