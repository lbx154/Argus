"""Autonomous model selection/creation for the quant vertical.

Mirrors ``factor_toolkit`` but for MODELS: a config-driven model space
(:mod:`.registry` + :mod:`.trainers` — "create a model = emit a config"), a
task-conditional prior (:mod:`.task_profile`), and a disciplined nested
walk-forward + successive-halving selector (:mod:`.selection`) that ledgers every
trial and reports the effective number of trials for honest deflation.

Importing this package pulls only numpy; lightgbm / torch / sklearn are imported
lazily inside the trainers, so it is safe to import without those installed.
"""
from __future__ import annotations

from .registry import ModelSpec, default_model_space
from .selection import CandidateResult, SelectionResult, select_model
from .task_profile import prior_for_profile, profile_task
from .trainers import available_families, build_trainer

__all__ = [
    "ModelSpec",
    "default_model_space",
    "build_trainer",
    "available_families",
    "profile_task",
    "prior_for_profile",
    "select_model",
    "SelectionResult",
    "CandidateResult",
]
