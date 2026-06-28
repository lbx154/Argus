from __future__ import annotations

from pathlib import Path

from argus_skill.team import result_provenance as rp


def _signed(target: str = "kA", metric: float = 1.85):
    priv, pub = rp.generate_keypair()
    data = {"target": target, "metric": metric, "mechanism": "official-eval", "correct": True}
    data["sig"] = rp.sign_result(data, priv)
    return data, priv, pub


def test_sign_then_verify_roundtrip() -> None:
    data, _priv, pub = _signed()
    assert rp.verify_result(data, pub) is True


def test_tampered_metric_fails_verification() -> None:
    data, _priv, pub = _signed(metric=1.85)
    data["metric"] = 0.0001  # forge a better number
    assert rp.verify_result(data, pub) is False


def test_tampered_target_fails_verification() -> None:
    data, _priv, pub = _signed(target="kA")
    data["target"] = "kB"  # lift the sig onto a different kernel
    assert rp.verify_result(data, pub) is False


def test_missing_signature_fails() -> None:
    _priv, pub = rp.generate_keypair()
    assert rp.verify_result({"target": "kA", "metric": 1.85, "correct": True}, pub) is False


def test_wrong_key_fails() -> None:
    data, _priv, _pub = _signed()
    _priv2, pub2 = rp.generate_keypair()  # an unrelated keypair
    assert rp.verify_result(data, pub2) is False


def test_read_key_path_or_inline(tmp_path: Path) -> None:
    _priv, pub = rp.generate_keypair()
    p = tmp_path / "pub.pem"
    p.write_bytes(pub)
    assert rp.read_key(str(p)) == pub        # a path → file bytes
    assert rp.read_key(pub.decode()) == pub  # inline PEM → bytes
