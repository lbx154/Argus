"""Compatibility imports for the former combined role-status module.

Runtime configuration now lives in :mod:`argus_skill.core.role_config`, event
activity projection in :mod:`argus_skill.life.role_activity`, and terminal
colors in :mod:`argus_skill.cli.role_colors`. New code should import the owning
module directly.
"""

from ..core.role_config import (
    ROLES,
    RoleConfig,
    is_reasoning_model,
    resolve_all_roles,
    resolve_role_config,
    runner_backend_label,
)
from ..life.role_activity import RoleActivity, role_activity
from .role_colors import ROLE_COLOR, ROLE_COLOR_BOLD, role_paint

__all__ = [
    "ROLES",
    "ROLE_COLOR",
    "ROLE_COLOR_BOLD",
    "RoleActivity",
    "RoleConfig",
    "is_reasoning_model",
    "resolve_all_roles",
    "resolve_role_config",
    "role_activity",
    "role_paint",
    "runner_backend_label",
]
