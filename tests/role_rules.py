"""A role's rule files, for tests that a rule is written once and the prompt points at it."""

from argus.roles.prompts.rules import RULES_ROOT


def rule(role: str, name: str) -> str:
    """The text of ``builtin_skills/<role>/<name>``, whitespace flattened."""
    return " ".join((RULES_ROOT / role / name).read_text(encoding="utf-8").split())


def lists(prompt: str, name: str) -> bool:
    """Whether the prompt's rule list names the file."""
    return f"\n- {name}: " in prompt
