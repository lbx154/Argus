"""Eval-server result signing is gated on FULL coverage (R2-2).

A truncated run (`max_workloads` set, incl. the /compile 1-workload smoke) must
NOT be signed — otherwise a kernel specialised to one workload could POST
max_workloads=1 and obtain a valid signature for a fast partial-coverage 'win'.
"""
from __future__ import annotations

import importlib

from argus_skill.team import result_provenance as rp

oes = importlib.import_module(
    "argus_skill.verticals.kernelbench.official_eval_server"
)


def _set_key(tmp_path, monkeypatch):
    priv, pub = rp.generate_keypair()
    (tmp_path / "priv.pem").write_bytes(priv)
    monkeypatch.setenv("ARGUS_EVAL_SIGNING_KEY", str(tmp_path / "priv.pem"))
    return pub


def test_full_coverage_correct_run_is_signed(tmp_path, monkeypatch):
    pub = _set_key(tmp_path, monkeypatch)
    signed = oes._sign_result_if_full_coverage("kA", 1.85, True, None)
    assert signed is not None and signed["metric"] == 1.85
    assert rp.verify_result(signed, pub) is True


def test_partial_coverage_is_not_signed(tmp_path, monkeypatch):
    _set_key(tmp_path, monkeypatch)
    # max_workloads=1 (a /compile smoke, or a truncated /eval) must NOT be signed.
    assert oes._sign_result_if_full_coverage("kA", 0.0001, True, 1) is None


def test_incorrect_run_is_not_signed(tmp_path, monkeypatch):
    _set_key(tmp_path, monkeypatch)
    assert oes._sign_result_if_full_coverage("kA", 0.0, False, None) is None


def test_no_signing_key_is_unsigned(monkeypatch):
    monkeypatch.delenv("ARGUS_EVAL_SIGNING_KEY", raising=False)
    assert oes._sign_result_if_full_coverage("kA", 1.85, True, None) is None
