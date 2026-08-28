"""Configurable pull-request consistency gate."""

from .config import load_config
from .criteria import evaluate
from .patch import patch_stats

__all__ = ["evaluate", "load_config", "patch_stats"]
