"""Compatibility exports for the shared task-timeline implementation."""

from ...core.timeline_models import (
    Duration,
    ExecutionOption,
    Proposal,
    Task,
    label,
    number,
    resources,
    strings,
)

__all__ = ['Duration', 'ExecutionOption', 'Task', 'Proposal', 'number', 'label', 'strings', 'resources']
