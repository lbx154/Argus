"""The list of rule files at the end of a role's one-page prompt.

A role's rules live once each in ``argus/builtin_skills/<role>/``; the prompt
names each file and when to read it, and the model reads it when it needs it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

RULES_ROOT = Path(__file__).resolve().parents[2] / "builtin_skills"


def rules_list(role: str, rules: Iterable[tuple[str, str]]) -> str:
    """``## Rules`` with one line per ``(file under the role's folder, when to read it)``."""
    lines = "\n".join(f"- {name}: {when}" for name, when in rules)
    return f"## Rules: files under {RULES_ROOT / role}/, read one before doing what it covers\n{lines}"


__all__ = ["RULES_ROOT", "rules_list"]
