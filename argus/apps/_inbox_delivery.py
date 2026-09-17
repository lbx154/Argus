"""Durable operator intake shared by the Engineer and Supervisor.

Canonical acceptance and transient delivery have different boundaries. Stored
authority is acknowledged after its atomic receipt; ephemeral input remains in
its envelope until a complete Engineer prompt or an accepted Planner decision.
"""
from __future__ import annotations

import json
import logging
from contextvars import copy_context
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable, Iterable

from ..core.file_lock import bounded_file_lock_wait, cleanup_file_lock_wait
from ..core.operator_context import OperatorContextStore
from ..core.run_gateway import run_interrupt_scope
from ..manager.operator_inbox import check_intake_cancelled, classify_operator_message

log = logging.getLogger(__name__)
LEASE_SECONDS = 30.0


def _identity(claim: Any) -> str:
    return json.dumps(claim.identity, sort_keys=True, separators=(",", ":"))


class OperatorInboxText(str):
    """String compatibility for display, with a physical delivery identity."""

    delivery_id: str
    ephemeral: bool

    def __new__(cls, text: str, *, delivery_id: str, ephemeral: bool):
        value = super().__new__(cls, text)
        value.delivery_id = delivery_id
        value.ephemeral = ephemeral
        return value


def operator_live_turn(messages: Iterable[str]) -> str:
    # Replaying a canonical receipt must not reinstate consumed/revoked
    # authority through the higher-precedence live-turn channel.
    return "\n".join(
        text for text in messages
        if not isinstance(text, OperatorInboxText) or text.ephemeral
    )


def unique_operator_messages(messages: Iterable[str]) -> list[str]:
    """Collapse repeated carries of one envelope, preserving equal new input."""
    seen: set[str] = set()
    result: list[str] = []
    for message in messages:
        if isinstance(message, OperatorInboxText):
            if message.delivery_id in seen:
                continue
            seen.add(message.delivery_id)
        result.append(message)
    return result


class _ClaimKeeper:
    """One bounded heartbeat worker per active receiver, never a queue lock."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.claims: dict[str, Any] = {}
        self.failures: dict[tuple[str, str], Exception] = {}
        self.lock = Lock()
        self.wake = Event()
        self.thread: Thread | None = None

    def hold(self, claim: Any) -> None:
        with self.lock:
            self.claims[_identity(claim)] = claim
            if self.thread is None:
                self.wake.clear()
                self.thread = Thread(target=self._run, name="argus-inbox-lease", daemon=True)
                self.thread.start()

    def check(self, claim: Any) -> None:
        with self.lock:
            error = self.failures.pop((_identity(claim), claim.owner), None)
        if error is not None:
            raise error
        check_intake_cancelled()

    def drop(self, claim: Any) -> None:
        with self.lock:
            key = _identity(claim)
            current = self.claims.get(key)
            if current is not None and current.owner == claim.owner:
                self.claims.pop(key, None)
            self.failures.pop((key, claim.owner), None)
            self.wake.set()

    def _run(self) -> None:
        from ._inbox import renew_inbox_claim

        while True:
            self.wake.wait(LEASE_SECONDS / 3)
            self.wake.clear()
            with self.lock:
                if not self.claims:
                    self.thread = None
                    return
                claims = list(self.claims.values())
            for claim in claims:
                try:
                    renewed = renew_inbox_claim(self.root, claim, lease_seconds=LEASE_SECONDS)
                except Exception as exc:  # retain the failure for the owning consumer
                    with self.lock:
                        current = self.claims.get(_identity(claim))
                        if current is not None and current.owner == claim.owner:
                            self.failures[(_identity(claim), claim.owner)] = exc
                            self.claims.pop(_identity(claim), None)
                            self.wake.set()
                else:
                    with self.lock:
                        current = self.claims.get(_identity(claim))
                        if current is not None and current.owner == claim.owner:
                            self.claims[_identity(claim)] = renewed


class DurableInboxReceiver:
    def __init__(
        self, root: Path | str, *, consumer: str, project_root: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.project_root = project_root or self.root
        self.consumer = consumer
        self.pending: dict[str, Any] = {}
        self.keeper = _ClaimKeeper(self.root)

    def receive(
        self, *, manager: Any = None, mission_id: str = "", limit: int = 10,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[str]:
        from ..skills.stage_machine import current_stage
        from ._inbox import claim_inbox_message

        stage = current_stage(self.project_root)
        out: list[str] = []
        selected: set[str] = set()
        claim = None
        caller_context = copy_context()

        def should_cancel() -> bool:
            # A caller may inspect current_run_interrupt_reason itself. Poll
            # in its original context, before installing this intake scope.
            return bool(cancelled and caller_context.copy().run(cancelled))

        try:
            with run_interrupt_scope(
                lambda: "operator inbox stop requested" if should_cancel() else None,
                retain_first_reason=True,
            ), bounded_file_lock_wait(timeout_seconds=float("inf"), cancelled=should_cancel):
                check_intake_cancelled()
                for pending in list(self.pending.values()):
                    if pending.mission_id != mission_id or pending.consumer_stage != stage:
                        self.release_pending()
                        break
                    self.keeper.check(pending)
                    out.append(self._text(pending, ephemeral=True))
                    selected.add(_identity(pending))
                    if len(out) >= max(1, limit):
                        return out
                for _ in range(max(1, limit) - len(out)):
                    check_intake_cancelled()
                    claim = claim_inbox_message(
                        self.root, consumer=self.consumer, current_stage=stage,
                        mission_id=mission_id, lease_seconds=LEASE_SECONDS,
                    )
                    if claim is None:
                        break
                    self.keeper.hold(claim)
                    if _identity(claim) in selected:
                        break
                    text = self._accept(claim, manager=manager)
                    if text is not None:
                        out.append(text)
                        selected.add(text.delivery_id)
                    claim = None
                return out
        except BaseException:
            # Leave durable rows discoverable. A failed best-effort lease
            # release is bounded by its expiry and cannot acknowledge input.
            if claim is not None:
                self._release(claim)
            self.release_pending()
            raise

    def _accept(self, claim: Any, *, manager: Any) -> OperatorInboxText | None:
        from ..core.operator_context import (
            apply_operator_delivery,
            close_operator_delivery_prefix,
            freeze_operator_intake,
        )
        from ..manager.directive import ACTIVE_MANAGER_DIRECTIVE_PREFIX, STEERING_HEADER
        from ._inbox import (
            accept_inbox_claim,
            acknowledge_inbox_claim,
            freeze_inbox_decision,
            settle_inbox_claim,
        )

        self.keeper.check(claim)
        if claim.decision is None:
            # Fail before any classification call when required policy is unreadable.
            _ = OperatorContextStore(self.root).revision
            if callable(manager) and not hasattr(manager, "classify_front_door"):
                manager = manager()
            decision = classify_operator_message(self.root, claim.text, manager=manager)
            plan = freeze_operator_intake(
                self.root, claim.text, decision, source="operator.inbox", mission_id=claim.mission_id,
            )
            self.keeper.check(claim)
            authoritative = plan["effect"] is not None
            existing_projection = claim.text.startswith((STEERING_HEADER, ACTIVE_MANAGER_DIRECTIVE_PREFIX))
            claim = freeze_inbox_decision(
                self.root, claim, decision=plan,
                target_root=plan["target_root"] if authoritative else None,
                transient_text=claim.text if not authoritative and not existing_projection else "",
            )
            self.keeper.hold(claim)
        if claim.target_root is not None:
            if not claim.acknowledged:
                self.keeper.check(claim)
                receipt = claim.receipt or apply_operator_delivery(claim.decision, claim.identity)
                claim = accept_inbox_claim(self.root, claim, receipt=receipt)
                claim = acknowledge_inbox_claim(self.root, claim)
            # Source ACK retains the original receipt until the target prefix
            # is closed. A crash here resumes closure without reapplying.
            self.keeper.check(claim)
            close_operator_delivery_prefix(claim.target_root, claim.closed_prefix)
            settle_inbox_claim(self.root, claim, canonical_closed=True)
            self.keeper.drop(claim)
            return self._text(claim, ephemeral=False)
        if not claim.acknowledged:
            self.keeper.check(claim)
            claim = accept_inbox_claim(self.root, claim)
            claim = acknowledge_inbox_claim(self.root, claim)
        if not claim.transient_text:
            settle_inbox_claim(self.root, claim)
            self.keeper.drop(claim)
            return None
        self.pending[_identity(claim)] = claim
        self.keeper.hold(claim)
        return self._text(claim, ephemeral=True)

    @staticmethod
    def _text(claim: Any, *, ephemeral: bool) -> OperatorInboxText:
        return OperatorInboxText(
            claim.transient_text if ephemeral else claim.text,
            delivery_id=_identity(claim), ephemeral=ephemeral,
        )

    def settle(self, messages: Iterable[str]) -> None:
        from ._inbox import settle_inbox_claim

        for text in messages:
            if not isinstance(text, OperatorInboxText) or not text.ephemeral:
                continue
            claim = self.pending.get(text.delivery_id)
            if claim is None:
                continue
            self.keeper.check(claim)
            settle_inbox_claim(self.root, claim)
            self.keeper.drop(claim)
            self.pending.pop(text.delivery_id, None)

    def _release(self, claim: Any) -> None:
        from ._inbox import release_inbox_claim

        self.keeper.drop(claim)
        try:
            with cleanup_file_lock_wait():
                release_inbox_claim(self.root, claim)
        except Exception:
            log.warning("operator inbox lease release deferred to expiry", exc_info=True)

    def release_pending(self) -> None:
        for claim in list(self.pending.values()):
            self._release(claim)
        self.pending.clear()


class EngineerInboxGuidance:
    """Prompt hook whose transient receipt is settled by PromptContextMixin."""

    def __init__(self, root: Path, workdir: Path, manager: Any, *, mission_id: str) -> None:
        self.receiver = DurableInboxReceiver(root, consumer="engineer", project_root=workdir)
        self.workdir = workdir
        self.manager = manager
        self.mission_id = mission_id
        self.selected: list[str] = []

    def __call__(self) -> list[str]:
        from ._runtime_execute import _engineer_guidance

        self.selected = []
        return _engineer_guidance(
            self.receiver.root, self.workdir, self.manager,
            receiver=self.receiver, delivery_messages=self.selected, mission_id=self.mission_id,
        )

    def settle(self) -> None:
        self.receiver.settle(self.selected)
        self.selected = []

    def release(self) -> None:
        self.receiver.release_pending()
        self.selected = []
