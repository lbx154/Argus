"""Optional cryptographic provenance for a teammate's result file.

A teammate's banked metric is read from a JSON file (``ARGUS_TEAMMATE_RESULT_FILE``)
that lives inside the engineer's OWN, unsandboxed working directory. On its own the
harness therefore cannot tell a real official-eval number from one the engineer
typed — the "the scorer authored it" property is convention, not enforcement.

This module closes that hole when the operator opts in: the *isolated* scorer (the
eval server, which the engineer cannot enter or tamper with) SIGNS the result with
an Ed25519 private key the engineer never has, and the harness VERIFIES it with only
the public key. A forged or tampered result then fails verification and is not banked.

Design notes:

* **Vertical-blind.** The harness only knows "verify a signature if a verify key is
  configured". Which scorer signs, and the operator glue that carries the signature
  into the result file, are domain concerns — not baked in here.
* **Asymmetric on purpose.** An HMAC would force the verifier (which runs in the
  engineer's reach) to hold the secret, so the engineer could forge. With Ed25519
  the private key stays inside the scorer container; the harness needs only the
  public key, which is safe to expose.
* **Off by default.** Both sides are inert unless their env var is set, so nothing
  changes for a fleet that has not wired signing.

``cryptography`` is imported lazily so the base package keeps no hard crypto
dependency; it is only required once provenance is actually enabled.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Result fields covered by the signature. ``metric`` is the value worth forging so
#: it MUST be signed; ``target`` binds the signature to a specific problem (a sig
#: for kernel A cannot be lifted onto kernel B's result file); ``correct`` stops a
#: failed eval's signature standing in for a passing one.
SIGNED_FIELDS = ("target", "metric", "mechanism", "correct")


def _canonical(data: dict[str, Any]) -> bytes:
    """Deterministic bytes over ``SIGNED_FIELDS`` (stable key order/format)."""
    payload = {k: data.get(k) for k in SIGNED_FIELDS}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _ed25519():
    try:
        from cryptography.hazmat.primitives import serialization
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "result provenance requires the 'cryptography' package; install it or "
            "unset ARGUS_EVAL_SIGNING_KEY / ARGUS_TEAMMATE_RESULT_VERIFY_KEY"
        ) from exc
    return serialization


def read_key(path_or_pem: str) -> bytes:
    """Resolve a key: a filesystem path is read; otherwise treat the string as PEM."""
    p = Path(path_or_pem)
    if p.exists():
        return p.read_bytes()
    return path_or_pem.encode("utf-8")


def sign_result(data: dict[str, Any], private_key_pem: bytes) -> str:
    """Return a hex Ed25519 signature over ``data``'s ``SIGNED_FIELDS``."""
    serialization = _ed25519()
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    return key.sign(_canonical(data)).hex()


def verify_result(data: dict[str, Any], public_key_pem: bytes) -> bool:
    """True iff ``data['sig']`` is a valid Ed25519 signature over ``SIGNED_FIELDS``.

    Never raises — any malformed key/signature/payload yields ``False``.
    """
    sig = data.get("sig")
    if not isinstance(sig, str):
        return False
    try:
        serialization = _ed25519()
        key = serialization.load_pem_public_key(public_key_pem)
        key.verify(bytes.fromhex(sig), _canonical(data))
        return True
    except Exception:  # noqa: BLE001 — any failure means "not verified"
        return False


def generate_keypair() -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)`` for a fresh Ed25519 key (PEM bytes).

    Convenience for operator provisioning and for tests; not used in the hot path.
    """
    serialization = _ed25519()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem
