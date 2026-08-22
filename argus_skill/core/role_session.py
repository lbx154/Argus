"""Bounded, role-isolated coding-agent sessions.

The production default is backend-aware ``auto``: resumable native CLIs use
bounded rolling sessions while fresh-only runners remain fresh. ``mission`` and
``rolling`` stay explicit controls for evaluation and rollback. Each role owns
one small durable capsule; no transcript or other role's private context is
copied into it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .secret_guard import known_secret_values, redact_secrets_text

ROLE_SESSION_POLICIES = frozenset({"auto", "fresh", "mission", "rolling"})
_RESUMABLE_ROLE_SESSION_BACKENDS = frozenset({
    "codex", "claude", "copilot", "grok", "opencode", "pi", "qoder",
})
_NON_RESUMABLE_ROLE_SESSION_BACKENDS = frozenset({"dsh"})
ROLE_SESSION_SIGNALS = frozenset({
    "repeated_contradiction",
    "reviewer_confusion",
    "quality_degradation",
})
ROLE_SESSION_SCHEMA_VERSION = 2

log = logging.getLogger(__name__)


def configured_role_session_policy() -> str:
    """Return the product-wide requested session policy.

    ``auto`` is intentionally the default across Windows Desktop, pip installs,
    source checkouts, and CI.  It is resolved against the selected backend at
    the role boundary rather than relying on a machine-local Pi setting.
    """
    policy = os.environ.get("ARGUS_SKILL_ROLE_SESSION_POLICY", "auto").strip().lower()
    if policy not in ROLE_SESSION_POLICIES:
        raise ValueError(
            "ARGUS_SKILL_ROLE_SESSION_POLICY must be auto, fresh, mission, or rolling"
        )
    return policy


def effective_role_session_policy(
    policy: str,
    backend: object,
    *,
    allow_resume: bool = True,
) -> str:
    """Resolve ``auto`` to a safe concrete per-role policy.

    Backend names are persisted in role capsules, while a number of test and
    third-party runner implementations expose only a class name.  Unknown
    backends therefore stay fresh under ``auto`` but continue honoring an
    explicit mission/rolling request for backwards-compatible adapters.
    """
    requested = str(policy or "").strip().lower() or "auto"
    if requested not in ROLE_SESSION_POLICIES:
        raise ValueError("role session policy must be auto, fresh, mission, or rolling")
    name = str(backend or "").strip().lower()
    if not allow_resume or name in _NON_RESUMABLE_ROLE_SESSION_BACKENDS:
        return "fresh"
    if requested == "auto":
        return "rolling" if name in _RESUMABLE_ROLE_SESSION_BACKENDS else "fresh"
    return requested


def objective_revision(objective: str) -> str:
    return hashlib.sha256(objective.strip().encode("utf-8")).hexdigest()[:16]


def _worktree_branch(workdir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
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
    input_tokens: int = 0
    repository_map: list[str] = field(default_factory=list)
    inspected_paths: list[str] = field(default_factory=list)
    decisive_output: str = ""
    open_hypotheses: list[str] = field(default_factory=list)
    static_fingerprint: str = ""
    signal_kind: str = ""
    signal_detail: str = ""
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
        if policy not in {"fresh", "mission", "rolling"}:
            raise ValueError("role session capsule policy must be fresh, mission, or rolling")
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
            (name for name, value in expected.items() if payload.get(name) != value),
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
            capsule.input_tokens = int(payload.get("input_tokens") or 0)
            capsule.repository_map = list(payload.get("repository_map") or [])
            capsule.inspected_paths = list(payload.get("inspected_paths") or [])
            capsule.decisive_output = str(payload.get("decisive_output") or "")
            capsule.open_hypotheses = list(payload.get("open_hypotheses") or [])
            capsule.static_fingerprint = str(payload.get("static_fingerprint") or "")
            capsule.signal_kind = str(payload.get("signal_kind") or "")
            capsule.signal_detail = str(payload.get("signal_detail") or "")
        elif payload:
            capsule.action = "rotated"
            capsule.rotation_reason = f"{changed}_changed"
        if not capsule.thread_id and seed_thread_id and policy != "fresh":
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

    def prepare(self, *, max_turns: int, max_input_tokens: int) -> str | None:
        if self.policy == "fresh":
            self.thread_id = ""
            self.action = "fresh"
            self.rotation_reason = ""
            return None
        branch = _worktree_branch(Path(self.workdir))
        if branch != self.branch:
            self.branch = branch
            self.rotate("branch_changed")
        if self.policy == "rolling" and self.thread_id:
            if max_turns > 0 and self.turns >= max_turns:
                self.rotate("turn_limit")
            elif max_input_tokens > 0 and self.input_tokens >= max_input_tokens:
                self.rotate("context_limit")
            else:
                self.action = "resumed"
                self.rotation_reason = ""
        elif self.thread_id:
            self.action = "resumed"
            self.rotation_reason = ""
        elif self.action != "rotated":
            self.action = "fresh"
            self.rotation_reason = ""
        self.save()
        return self.thread_id or None

    def complete(
        self,
        result: Any,
        *,
        decisive_output: str = "",
        static_fingerprint: str = "",
    ) -> bool:
        """Persist compact role metadata without changing the turn outcome.

        Provider execution and review are authoritative.  A checkpoint/capsule
        is recovery metadata, so an unreadable filesystem, malformed optional
        checkpoint, or failed atomic replace must be surfaced as a warning but
        must never turn a successful provider result into a failed mission.
        """
        self.persistence_error = ""
        try:
            if self.action != "resumed":
                self.turns = 0
                self.input_tokens = 0
            self.turns += 1
            self.input_tokens += int(getattr(result, "input_tokens", 0) or 0)
            self.thread_id = (
                ""
                if self.policy == "fresh"
                else str(getattr(result, "thread_id", "") or "")
            )
            if self.policy == "fresh":
                return True
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
        self.input_tokens = 0
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
                f"\nLatest reviewed handoff: `{root / 'latest.json'}`"
                f"\nSemantic task frontier: `{root / 'frontier.json'}`"
            )
        return (
            "## Role state references\n"
            f"Capsule: `{self.path}`"
            f"{mission_lines}\n"
            "Host already injected current state. Read a reference only for a specific "
            "contradiction or continuation; project artifacts remain authoritative. "
            "Do not edit capsule or frontier metadata."
        )

    def save(self) -> bool:
        """Atomically save the capsule, returning False on metadata I/O failure."""
        if self.path is None:
            return True
        temporary: Path | None = None
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
                "input_tokens": self.input_tokens,
                "repository_map": self.repository_map,
                "inspected_paths": self.inspected_paths,
                "decisive_output": self.decisive_output,
                "open_hypotheses": self.open_hypotheses,
                "static_fingerprint": self.static_fingerprint,
                "signal_kind": self.signal_kind,
                "signal_detail": self.signal_detail,
                "updated_at": self.updated_at,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f"{self.path.name}.tmp-{os.getpid()}-{time.time_ns()}"
            )
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
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
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


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
        "input_tokens": 0,
        "signal_kind": normalized,
        "signal_detail": redact_secrets_text(
            str(detail or "")[:1000], known_values=known_secret_values()
        ),
        "updated_at": time.time(),
    })
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return True


__all__ = [
    "ROLE_SESSION_POLICIES",
    "ROLE_SESSION_SIGNALS",
    "RoleSessionCapsule",
    "configured_role_session_policy",
    "effective_role_session_policy",
    "signal_role_session_file",
    "objective_revision",
]
