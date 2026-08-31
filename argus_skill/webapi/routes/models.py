"""Pydantic request bodies shared across web API route modules."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TaskIn(BaseModel):
    text: str
    # Lazy daemon spawn (default on): queueing a task starts this project's
    # executor if none is alive — the same behaviour as the Python cockpit's
    # _autospawn_daemon_for_task, so `argus` + submit a task actually runs it.
    autostart_daemon: bool = True


class NudgeIn(BaseModel):
    text: str


class AttachmentRefIn(BaseModel):
    attachment_id: str


class MessageIn(BaseModel):
    text: str
    attachments: list[AttachmentRefIn] = Field(default_factory=list)
    # Explicit Task/Chat is operator authority and skips only the category
    # classifier. Task still follows Manager -> Planner -> Engineer -> Reviewer.
    route_override: Literal["auto", "chat", "task"] = "auto"


class AnswerIn(BaseModel):
    text: str


class DecisionIn(BaseModel):
    option_id: str
    note: str = ""


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
