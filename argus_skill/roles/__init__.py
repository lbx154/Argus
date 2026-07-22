"""Shared contracts and prompt composition for persistent Argus roles."""

from .prompts import (
    PROMPT_CATALOG,
    ChecklistMode,
    ResolvedRolePrompt,
    RoleName,
    RolePromptCatalog,
    RolePromptRequest,
    resolve_role_prompt,
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
