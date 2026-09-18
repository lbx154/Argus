"""Call-local accounting evidence emitted at known runner boundaries.

An exception caught outside these boundaries is not proof of no provider work.
The scope spans authorization retries; a later refusal cannot forgive an earlier
attempt. This is an internal producer contract, not a client attestation API.
"""
import subprocess
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps


@dataclass
class _Scope:
    reservation: object
    attempts: int = 0
    proof: object = None


@dataclass(frozen=True)
class _Proof:
    scope: _Scope
    kind: str
    receipt: object = None


_current = ContextVar("accounting_no_charge_scope", default=None)


def _certify(scope, kind, receipt=None):
    proof = _Proof(scope, kind, receipt)
    scope.proof = proof
    try:
        scope.reservation.prove_no_charge(proof=proof)
    finally:
        scope.proof = None


def consume_producer_proof(reservation, proof):
    """Reject caller labels/objects and stale or cross-call capabilities."""
    from .accounting_integrity import AccountingIntegrityError
    scope = _current.get()
    if (not isinstance(proof, _Proof) or scope is None or proof.scope is not scope
            or scope.proof is not proof or scope.reservation is not reservation):
        raise AccountingIntegrityError(reservation.root, 0, b"", "no active producer proof")
    scope.proof = None
    return proof.kind, proof.receipt


def accounting_scope(function):
    @wraps(function)
    def wrapped(ctx, *args, **kwargs):
        token = _current.set(_Scope(ctx.cost_reservation))
        try:
            return function(ctx, *args, **kwargs)
        finally:
            _current.reset(token)
    return wrapped


def possible_provider_attempt():
    scope = _current.get()
    if scope is not None:
        scope.attempts += 1
        if scope.reservation is not None:
            scope.reservation.invalidate_no_charge_proof()


def preparation_refused():
    scope = _current.get()
    if scope is not None and scope.attempts == 0 and scope.reservation is not None:
        _certify(scope, "copilot_preparation")


def accounted_popen(*args, **kwargs):
    scope = _current.get()
    possible_provider_attempt()
    try:
        return subprocess.Popen(*args, **kwargs)
    except FileNotFoundError:
        # This catch surrounds only the process constructor, not streaming,
        # cleanup, session files, or arbitrary runner/plugin code.
        if scope is not None and scope.attempts == 1 and scope.reservation is not None:
            _certify(scope, "process_create_enoent")
        raise


def certify_local_startup(reservation, receipt):
    scope = _current.get()
    if scope is not None and scope.reservation is reservation and scope.attempts <= 1:
        _certify(scope, "local_startup_receipt", receipt)
