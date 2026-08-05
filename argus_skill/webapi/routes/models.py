"""Pydantic request bodies shared across the webapi route domains.

Extracted from ``create_app`` (previously locally-defined classes named
``_TaskIn``, ``_NudgeIn``, ...). Field names, defaults, and validation are
byte-for-byte identical; only the module location and the leading underscore
(no longer needed once these are shared across domain modules) changed.
"""

from __future__ import annotations

from pydantic import BaseModel


class TaskIn(BaseModel):
    text: str
    # Lazy daemon spawn (default on): queueing a task starts this project's
    # executor if none is alive — the same behaviour as the Python cockpit's
    # _autospawn_daemon_for_task, so `argus` + submit a task actually runs it.
    autostart_daemon: bool = True


class NudgeIn(BaseModel):
    text: str


class MessageIn(BaseModel):
    text: str


class AnswerIn(BaseModel):
    text: str


class DecisionIn(BaseModel):
    option_id: str
    note: str = ""
    expected_revision: int | None = None


class AbortMissionIn(BaseModel):
    reason: str = ""


class CommandIn(BaseModel):
    command_id: str = ""
    expected_revision: int | None = None


class CreateDaemonIn(CommandIn):
    objective: str = ""
    name: str = ""
    launch_cwd: str = ""
    workdir: str = ""


class LaunchCwdIn(BaseModel):
    launch_cwd: str


class WorkdirIn(BaseModel):
    workdir: str


class StopIn(CommandIn):
    drain: bool = False
    force: bool = False


class ReplaceDaemonIn(CommandIn):
    victim_sid: str
    resume_continuous: bool = False


class ContinuousIn(BaseModel):
    enabled: bool
    objective: str = ""


class NoteIn(BaseModel):
    text: str


class PlanIn(BaseModel):
    text: str


class RewriteIn(BaseModel):
    text: str


class ConfigSetIn(BaseModel):
    name: str
    value: str


class BudgetSetIn(BaseModel):
    values: dict[str, str]


class ProjectUpdateIn(BaseModel):
    name: str


class IdentitySetIn(BaseModel):
    text: str


class SkillsIn(BaseModel):
    args: str = "ls"


class DisposeIn(BaseModel):
    op: str = "done"  # done | skip | rm
