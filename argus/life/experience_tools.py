"""Project-bound experience inspection and evidence-backed correction tools."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from ..advisor.evidence import collect_evidence
from ..core.role_tool_bridge import CallBoundBridge, bridge_request, require_fields
from .failure_experience import FailureAnnotation, FailureExperienceStore

PREFIX = "ARGUS_PLUGIN_EXPERIENCE"
_TEXT_FIELDS = {"title", "objective", "factual_outcome", "research_narrative"}
_LIST_FIELDS = {
    "passed_assumptions", "lessons", "transfer_insights", "claim_boundaries",
    "retry_conditions", "artifact_refs", "concepts", "causes",
}


def _guard_project_files(root: Path) -> None:
    if root.resolve() != root:
        raise ValueError("experience tool project root changed")
    files = ["failure_experiences.jsonl", "failure_experiences.jsonl.lock", "failure_experiences.sqlite3",
             "embedding/config.json", "embedding/config.lock", "embedding/cache.sqlite3", "embedding/usage.sqlite3"]
    files += [name + suffix for name in files if name.endswith(".sqlite3") for suffix in ("-journal", "-wal", "-shm")]
    for name in files:
        path = root / name
        if path.resolve() != path or path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("experience project files must be canonical regular files")


def _text(value: Any, name: str, *, limit: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a nonempty string of at most {limit} characters")
    return value.strip()


def _refs(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError("corrections require 1 to 16 explicit file evidence references")
    return [_text(ref, "evidence reference", limit=700) for ref in value]


class ExperienceToolService:
    """The host owns project, role and call identity; requests cannot override them."""

    def __init__(self, project_root: Path, *, role: str, parent_call_id: str,
                 writable: bool = True, workspace: Path | None = None,
                 redact: Callable[[str], str] | None = None) -> None:
        if role not in {"manager", "planner", "engineer", "reviewer"}:
            raise ValueError("unsupported experience tool role")
        root = Path(project_root).absolute()
        if root.resolve() != root:
            raise ValueError("experience tool project root must be canonical")
        self.root = root
        _guard_project_files(root)
        self.workspace = Path(workspace).absolute() if workspace is not None else root
        if self.workspace.resolve() != self.workspace:
            raise ValueError("experience tool workspace must be canonical")
        self.role = role
        self.parent_call_id = _text(parent_call_id, "parent call id", limit=200)
        self.writable = writable and role != "reviewer"
        if redact is None:
            from ..core.secret_guard import redact_secrets_text

            redact = redact_secrets_text
        self.redact = redact
        self.store = FailureExperienceStore(root / "failure_experiences.jsonl")
        self._mutations = 0
        self._lock = threading.Lock()

    def dispatch(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        _guard_project_files(self.root)
        if operation == "search":
            require_fields(data, {"query"}, required={"query"})
            query = _text(data["query"], "query")
            return {"experiences": [
                {"id": hit.experience.id, "revision": hit.experience.revision,
                 "title": hit.experience.title, "status": hit.experience.status,
                 "channel": hit.channel, "factual_outcome": hit.experience.factual_outcome[:1200],
                 "claim_boundaries": hit.experience.claim_boundaries[:3]}
                for hit in self.store.retrieve(query, max_entries=4)
            ], "authority": "advisory"}
        if operation == "get":
            require_fields(data, {"experience_id"}, required={"experience_id"})
            item = self.store.get(_text(data["experience_id"], "experience id", limit=200))
            return {"experience": item.to_jsonable() if item else None, "authority": "advisory"}
        if operation not in {"revise", "retract"}:
            raise ValueError("unknown experience operation")
        if not self.writable:
            raise ValueError("this role turn can inspect experiences but cannot mutate them")
        required = {"experience_id", "expected_revision", "evidence_refs", "reason"}
        if operation == "revise":
            required.add("changes")
        require_fields(data, required, required=required)
        identity = _text(data["experience_id"], "experience id", limit=200)
        revision = data["expected_revision"]
        if type(revision) is not int or revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        refs = _refs(data["evidence_refs"])
        reason = self.redact(_text(data["reason"], "correction reason", limit=2000))
        with self._lock:
            if self._mutations >= 8:
                raise ValueError("experience mutation limit reached for this role turn")
            if self.root.resolve() != self.root or self.workspace.resolve() != self.workspace:
                raise ValueError("experience evidence scope changed")
            evidence = collect_evidence(
                refs, workspace=self.workspace, project_root=self.root,
                byte_limit=32_768, redact=self.redact,
            )
            if any(row["truncated"] or row["bytes_read"] != row["file_size"] for row in evidence):
                raise ValueError("experience correction requires complete evidence files within 32 KiB total")
            evidence_receipt = [
                {key: row[key] for key in ("ref", "source_sha256", "sha256", "bytes_read", "file_size")}
                for row in evidence
            ]
            refs = [f"{row['ref']}#sha256={row['source_sha256']}" for row in evidence]
            _guard_project_files(self.root)
            if operation == "retract":
                written = self.store.retract(
                    identity, expected_revision=revision, evidence_refs=refs,
                    reason=f"{reason} [role:{self.role}; call:{self.parent_call_id}]",
                )
            else:
                changes = data["changes"]
                if not isinstance(changes, dict) or not changes or set(changes) - (_TEXT_FIELDS | _LIST_FIELDS):
                    raise ValueError("changes must contain supported capsule content fields")
                for key, value in changes.items():
                    if key in _TEXT_FIELDS:
                        if not isinstance(value, str) or len(value) > (500 if key == "title" else 4000):
                            raise ValueError(f"invalid content for {key}")
                    elif not isinstance(value, list) or len(value) > 24 or any(
                        not isinstance(entry, str) or len(entry) > 1000 for entry in value
                    ):
                        raise ValueError(f"invalid content for {key}")
                changes = {key: self.redact(value) if isinstance(value, str)
                           else [self.redact(entry) for entry in value]
                           for key, value in changes.items()}
                previous = self.store.get(identity)
                if previous is None:
                    raise ValueError("experience is missing or retired")
                annotation = FailureAnnotation.new(
                    reason, relation=f"correction by {self.role}; call:{self.parent_call_id}",
                    evidence_refs=refs,
                )
                written = self.store.revise(
                    identity, expected_revision=revision, evidence_refs=refs, **changes,
                    annotations=(previous.annotations + [annotation])[-24:],
                )
            self._mutations += 1
        return {"status": "updated", "experience_id": written.id,
                "revision": written.revision, "state": written.state,
                "evidence": evidence_receipt,
                "evidence_verification": "Host read the scoped file bytes and recorded their hashes; the interpretation remains advisory."}


class ExperienceBridge(CallBoundBridge):
    def __init__(self, service: ExperienceToolService) -> None:
        super().__init__(service.dispatch, env_prefix=PREFIX, timeout_seconds=15)


def request(operation: str, payload: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    return bridge_request(PREFIX, operation, payload, env=env)
