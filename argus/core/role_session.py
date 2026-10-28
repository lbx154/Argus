"""Role-isolated coding-agent sessions.

A role resumes its provider thread whenever the backend can resume one; other
runners stay fresh. The backend owns its context window and compaction; a
session is replaced when what it was opened for changes, or when its history
grows past a budget a fresh turn with the full prompt undercuts. Each role owns
one small durable capsule; no transcript or other role's private context is
copied into it.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic_replace import write_atomic
from .secret_guard import known_secret_values, redact_secrets_text
from .session_ledger import session_size_tokens

_RESUMABLE_ROLE_SESSION_BACKENDS = frozenset({
    "codex", "claude", "copilot", "cursor", "grok", "opencode", "pi", "qoder",
})
ROLE_SESSION_SIGNALS = frozenset({
    "repeated_contradiction",
    "reviewer_confusion",
    "quality_degradation",
})
#: A capsule from another version is rotated, never resumed: version 3 threads
#: hand work on with ``hand_off`` (core.handoff), not baton lines.
ROLE_SESSION_SCHEMA_VERSION = 3
#: A resumed thread re-sends its whole history with every request. Past about
#: this many tokens of Argus prompts and tool output the history is mostly
#: material the next turn does not need (2026-10-26: an Engineer review fix
#: resumed a 250k-token literature round; a Planner's seventh cycle started at
#: 150k), so the role starts a fresh session with its full prompt instead.
ROLE_SESSION_RESUME_BUDGET_TOKENS = 60_000
_SEEN_THREAD_IDS_LIMIT = 64

log = logging.getLogger(__name__)


def role_session_policy(backend: object, *, allow_resume: bool = True) -> str:
    """``rolling`` resumes the role's thread; ``fresh`` starts one per turn.

    Backend names are persisted in role capsules, while test and third-party
    runners often expose only a class name, so an unknown backend stays fresh.
    """
    name = str(backend or "").strip().lower()
    return "rolling" if allow_resume and name in _RESUMABLE_ROLE_SESSION_BACKENDS else "fresh"


def objective_revision(objective: str) -> str:
    """The objective itself identifies its revision: any edit is a new one."""
    return objective.strip()


def _worktree_branch(workdir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _repository_map(workdir: Path) -> list[str]:
    try:
        if not workdir.is_dir():
            return []
        return [
            path.name + ("/" if path.is_dir() else "")
            for path in sorted(workdir.iterdir(), key=lambda item: item.name)[:80]
        ]
    except OSError:
        return []


def _checkpoint_open_items(path: str) -> list[str]:
    """Read optional checkpoint metadata without owning mission success.

    A first round is explicitly allowed to finish without creating a checkpoint.
    The file may also disappear between an existence check and this read.  Both
    cases mean "no durable open items", not that the provider turn failed.
    """
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        log.warning("role-session checkpoint metadata is unavailable: %s", exc)
        return []
    items: list[str] = []
    active = False
    for line in lines:
        if line.startswith("#"):
            active = line.lstrip("# ").strip() == "Open Questions / Blockers"
            continue
        if active and line.strip():
            items.append(line.strip())
    return items[:20]


_SECTION_HEADING = re.compile(r"^#{2,3} \S")
# Sections shorter than this stay inline: naming them costs about as much.
_ELIDE_MIN_SECTION_CHARS = 400


def _prompt_sections(prompt: str) -> list[str]:
    """Split a prompt at its level-2/3 headings, ignoring fenced code."""
    sections: list[str] = []
    current: list[str] = []
    fenced = False
    for line in prompt.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and current and _SECTION_HEADING.match(line):
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def elide_repeated_sections(prompt: str, previous: str) -> str:
    """Drop the sections a resumed thread already holds verbatim.

    ``previous`` is the full prompt of the thread's last completed turn. A
    section byte-identical to one of its sections is already in the thread's
    context, so it is named instead of repeated. The first section, which
    frames the turn, is always kept. Resumed Planner turns on 2026-10-15 were
    two-thirds identical to the turn before, and each repeat stayed in the
    thread for every later turn to pay for again.
    """
    seen = {section.strip() for section in _prompt_sections(previous)}
    sections = _prompt_sections(prompt)
    kept: list[str] = []
    omitted: list[str] = []
    for index, section in enumerate(sections):
        body = section.strip()
        if (
            index
            and len(body) >= _ELIDE_MIN_SECTION_CHARS
            and _SECTION_HEADING.match(body)
            and body in seen
        ):
            omitted.append(body.splitlines()[0].lstrip("#").strip())
        else:
            kept.append(section)
    if not omitted:
        return prompt
    note = (
        "## Unchanged since your previous turn\n"
        "These sections are identical to the copies already in this session and "
        "still apply as written: "
        + "; ".join(f"`{title}`" for title in omitted)
        + ".\n"
    )
    return "\n".join([kept[0], note, *kept[1:]]) + "\n"


@dataclass
class RoleSessionCapsule:
    role: str
    policy: str
    objective_revision: str
    workdir: str
    branch: str
    backend: str
    model: str
    checkpoint_path: str = ""
    mission_context_path: str = ""
    thread_id: str = ""
    turns: int = 0
    repository_map: list[str] = field(default_factory=list)
    inspected_paths: list[str] = field(default_factory=list)
    decisive_output: str = ""
    open_hypotheses: list[str] = field(default_factory=list)
    static_fingerprint: str = ""
    signal_kind: str = ""
    signal_detail: str = ""
    # Bounded history of this role's provider threads in the mission, kept so
    # another role can refuse to resume any of them after a process restart.
    seen_thread_ids: list[str] = field(default_factory=list)
    # Full text of the last prompt the current thread completed; a resumed turn
    # omits the sections the thread already holds from it.
    last_prompt: str = ""
    updated_at: float = 0.0
    path: Path | None = field(default=None, repr=False)
    action: str = field(default="fresh", repr=False)
    rotation_reason: str = field(default="", repr=False)
    persistence_error: str = field(default="", repr=False)

    @classmethod
    def open(
        cls,
        *,
        role: str,
        policy: str,
        objective_revision: str,
        workdir: Path,
        backend: str,
        model: str,
        checkpoint_path: Path | None,
        path: Path | None,
        seed_thread_id: str | None = None,
        mission_context_path: str = "",
    ) -> "RoleSessionCapsule":
        if policy not in {"fresh", "rolling"}:
            raise ValueError("role session capsule policy must be fresh or rolling")
        branch = "" if policy == "fresh" else _worktree_branch(workdir)
        payload: dict[str, Any] = {}
        if policy != "fresh" and path is not None and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, ValueError):
                payload = {}
        expected = {
            "role": role,
            "policy": policy,
            "objective_revision": objective_revision,
            "workdir": str(workdir),
            "branch": branch,
            "backend": backend,
            "model": model,
        }
        changed = next(
            (name for name, value in {**expected, "schema_version": ROLE_SESSION_SCHEMA_VERSION}.items()
             if payload.get(name) != value),
            "",
        )
        capsule = cls(
            **expected,
            checkpoint_path=str(checkpoint_path or ""),
            mission_context_path=str(mission_context_path or ""),
            path=path,
        )
        if payload and not changed:
            capsule.thread_id = str(payload.get("thread_id") or "")
            capsule.turns = int(payload.get("turns") or 0)
            capsule.repository_map = list(payload.get("repository_map") or [])
            capsule.inspected_paths = list(payload.get("inspected_paths") or [])
            capsule.decisive_output = str(payload.get("decisive_output") or "")
            capsule.open_hypotheses = list(payload.get("open_hypotheses") or [])
            capsule.static_fingerprint = str(payload.get("static_fingerprint") or "")
            capsule.signal_kind = str(payload.get("signal_kind") or "")
            capsule.signal_detail = str(payload.get("signal_detail") or "")
            capsule.last_prompt = str(payload.get("last_prompt") or "")
        elif payload:
            capsule.action = "rotated"
            capsule.rotation_reason = f"{changed}_changed"
        if payload and payload.get("role") == role:
            # The role's thread history outlives rotation and context changes:
            # another role must keep refusing every thread this one used.
            history = payload.get("seen_thread_ids")
            seen = [str(item) for item in history if item] if isinstance(history, list) else []
            previous = str(payload.get("thread_id") or "")
            if previous and previous not in seen:
                seen.append(previous)
            capsule.seen_thread_ids = seen[-_SEEN_THREAD_IDS_LIMIT:]
        # A seed continues the thread the capsule would have resumed. It must not
        # undo a rotation: a changed objective, model, backend, or workdir, or
        # a pending cross-role signal, means the old thread no longer applies.
        if (
            not capsule.thread_id
            and seed_thread_id
            and policy != "fresh"
            and capsule.action != "rotated"
            and not capsule.signal_kind
        ):
            capsule.thread_id = seed_thread_id
        if policy == "fresh":
            return capsule
        capsule.repository_map = _repository_map(workdir)
        if mission_context_path:
            try:
                mission = json.loads(
                    Path(mission_context_path).read_text(encoding="utf-8")
                )
                capsule.inspected_paths = [
                    str(ref.get("ref"))
                    for ref in mission.get("context_refs", [])
                    if isinstance(ref, dict) and ref.get("ref")
                ]
            except (OSError, ValueError, AttributeError):
                pass
        capsule.save()
        return capsule

    def prepare(self) -> str | None:
        if self.policy == "fresh":
            self.thread_id = ""
            self.action = "fresh"
            self.rotation_reason = ""
            return None
        branch = _worktree_branch(Path(self.workdir))
        if branch != self.branch:
            self.branch = branch
            self.rotate("branch_changed")
        if self.thread_id and session_size_tokens(self.thread_id) > ROLE_SESSION_RESUME_BUDGET_TOKENS:
            self.rotate("context_budget")
        if self.thread_id:
            self.action = "resumed"
            self.rotation_reason = ""
        elif self.action != "rotated":
            self.action = "fresh"
            self.rotation_reason = ""
        self.save()
        return self.thread_id or None

    def resume_prompt(self, prompt: str) -> str:
        """The prompt to send: unchanged sections are dropped on a resumed thread."""
        if self.action != "resumed" or not self.last_prompt:
            return prompt
        return elide_repeated_sections(prompt, self.last_prompt)

    def complete(
        self,
        result: Any,
        *,
        decisive_output: str = "",
        static_fingerprint: str = "",
        prompt: str = "",
    ) -> bool:
        """Persist compact role metadata without changing the turn outcome.

        Provider execution and review are authoritative.  A checkpoint/capsule
        is recovery metadata, so an unreadable filesystem, malformed optional
        checkpoint, or failed atomic replace must be surfaced as a warning but
        must never turn a successful provider result into a failed mission.
        """
        self.persistence_error = ""
        try:
            resumed_thread = self.thread_id if self.action == "resumed" else ""
            if self.action != "resumed":
                self.turns = 0
            self.turns += 1
            result_thread = str(getattr(result, "thread_id", "") or "")
            if result_thread and result_thread not in self.seen_thread_ids:
                self.seen_thread_ids = [
                    *self.seen_thread_ids, result_thread,
                ][-_SEEN_THREAD_IDS_LIMIT:]
            self.thread_id = "" if self.policy == "fresh" else result_thread
            if self.policy == "fresh":
                return True
            # A resume answered on another thread never saw the omitted
            # sections, so the next turn must send everything again.
            self.last_prompt = (
                prompt if not resumed_thread or result_thread == resumed_thread else ""
            )
            self.decisive_output = redact_secrets_text(
                decisive_output[:2000], known_values=known_secret_values()
            )
            self.open_hypotheses = _checkpoint_open_items(self.checkpoint_path)
            if static_fingerprint:
                self.static_fingerprint = static_fingerprint
            self.repository_map = _repository_map(Path(self.workdir))
            if self.action in {"fresh", "rotated"}:
                self.signal_kind = ""
                self.signal_detail = ""
            return self.save()
        except Exception as exc:  # noqa: BLE001 - metadata never owns mission success
            self.persistence_error = redact_secrets_text(
                f"{type(exc).__name__}: {exc}",
                known_values=known_secret_values(),
            )[:1000]
            log.warning(
                "role-session completion metadata failed for %s: %s",
                self.role,
                self.persistence_error,
                exc_info=True,
            )
            return False

    def signal(self, kind: str, detail: str = "") -> None:
        normalized = str(kind or "").strip().lower()
        if normalized not in ROLE_SESSION_SIGNALS:
            raise ValueError(f"unknown role session signal: {kind!r}")
        self.signal_kind = normalized
        self.signal_detail = redact_secrets_text(
            str(detail or "")[:1000], known_values=known_secret_values()
        )
        self.rotate(f"signal:{normalized}")

    def rotate(self, reason: str) -> None:
        self.thread_id = ""
        self.turns = 0
        self.last_prompt = ""
        self.action = "rotated"
        self.rotation_reason = reason
        self.save()

    def prompt_block(self) -> str:
        if self.path is None or not self.mission_context_path:
            return ""
        mission_lines = ""
        if self.mission_context_path:
            root = Path(self.mission_context_path).parent
            mission_lines = (
                f"\nMission contract: `{self.mission_context_path}`"
                f"\nLatest work handoff (may await review): `{root / 'latest.json'}`"
            )
        return (
            "## Role state references\n"
            f"Capsule: `{self.path}`"
            f"{mission_lines}\n"
            "Host already injected current state. Read a reference only for a specific "
            "contradiction or continuation; project artifacts remain authoritative. "
            "Do not edit capsule metadata."
        )

    def save(self) -> bool:
        """Atomically save the capsule, returning False on metadata I/O failure."""
        if self.path is None:
            return True
        try:
            self.updated_at = time.time()
            payload = {
                "schema_version": ROLE_SESSION_SCHEMA_VERSION,
                "role": self.role,
                "policy": self.policy,
                "objective_revision": self.objective_revision,
                "workdir": self.workdir,
                "branch": self.branch,
                "backend": self.backend,
                "model": self.model,
                "checkpoint_path": self.checkpoint_path,
                "mission_context_path": self.mission_context_path,
                "thread_id": self.thread_id,
                "turns": self.turns,
                "repository_map": self.repository_map,
                "inspected_paths": self.inspected_paths,
                "decisive_output": self.decisive_output,
                "open_hypotheses": self.open_hypotheses,
                "static_fingerprint": self.static_fingerprint,
                "signal_kind": self.signal_kind,
                "signal_detail": self.signal_detail,
                "seen_thread_ids": self.seen_thread_ids,
                "last_prompt": self.last_prompt,
                "updated_at": self.updated_at,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            self.persistence_error = ""
            return True
        except Exception as exc:  # noqa: BLE001 - capsule is advisory metadata
            self.persistence_error = redact_secrets_text(
                f"{type(exc).__name__}: {exc}",
                known_values=known_secret_values(),
            )[:1000]
            log.warning(
                "role-session capsule save failed for %s: %s",
                self.role,
                self.persistence_error,
                exc_info=True,
            )
            return False


def signal_role_session_file(path: Path | str, kind: str, detail: str = "") -> bool:
    """Rotate an existing role capsule from an explicit cross-role signal."""
    target = Path(path)
    normalized = str(kind or "").strip().lower()
    if normalized not in ROLE_SESSION_SIGNALS:
        raise ValueError(f"unknown role session signal: {kind!r}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    payload.update({
        "schema_version": ROLE_SESSION_SCHEMA_VERSION,
        "thread_id": "",
        "turns": 0,
        "signal_kind": normalized,
        "signal_detail": redact_secrets_text(
            str(detail or "")[:1000], known_values=known_secret_values()
        ),
        "updated_at": time.time(),
    })
    write_atomic(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return True


__all__ = [
    "ROLE_SESSION_SIGNALS",
    "RoleSessionCapsule",
    "role_session_policy",
    "signal_role_session_file",
    "objective_revision",
]
