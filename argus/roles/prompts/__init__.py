"""Unified prompt catalog for the four persistent Argus roles."""

from .registry import PROMPT_CATALOG, RolePromptCatalog, resolve_role_prompt
from .types import (
    ChecklistMode,
    ResolvedRolePrompt,
    RoleName,
    RolePromptRequest,
)

__all__ = [
    "ChecklistMode",
    "PROMPT_CATALOG",
    "ResolvedRolePrompt",
    "RoleName",
    "RolePromptCatalog",
    "RolePromptRequest",
    "resolve_role_prompt",
]
