"""Synthetic desired-behavior regressions for independent B2-R1/R2/R3.

No review module imports, provider executables, network, or live accounting.
"""

import json
import os
import time
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from argus.core import cost_control as cc
from argus.core.accounting_integrity import AccountingIntegrityError
from argus.core.usage import UsageLedger, UsageRecord, _rewrite_usage_rows, summarize_usage


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("ARGUS_", "COPILOT_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "projects" / "b3-synthetic"
    p.mkdir(parents=True)
    return p


def receipt(project, amount=0, **kw):
    row = dict(
        call_id="call",
        project_id=project.name,
        provider="copilot",
        model="synthetic",
        run_label="test",
        status="completed",
        pricing_status="priced",
        cost_usd=amount,
        started_at=time.time() - 1,
        completed_at=time.time(),
    )
    row.update(kw)
    return UsageRecord.from_jsonable(row)


def reserve(root, project):
    call, reason = cc.reserve_call_budget(
        call_id="call",
        project_root=project,
        mission_id=None,
        provider="copilot",
        model="synthetic",
        run_label="test",
        global_root=root,
        global_daily_cap_usd=10,
    )
    assert call is not None, reason
    return call


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def denied(project):
    return receipt(project, status="denied", pricing_status="not_billed")


@pytest.mark.parametrize("transition", ["prepare", "settle", "release"])
@pytest.mark.parametrize("state", ["started", "failed", "ambiguous_prepare"])
@pytest.mark.parametrize("observed", [0, 30])
def test_no_charge_guard_precedes_every_mutation(tmp_path, project, transition, state, observed):
    call = reserve(tmp_path, project)
    if state == "started":
        call.mark_execution_started()
    elif state == "failed":
        call.finalization_failed("synthetic response lost")
    else:
        call.prepare_finalization(error="synthetic uncertain response")
    if observed:
        call.observe_cost(observed, tokens=120)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        if transition == "prepare":
            call.prepare_finalization(denied(project))
        elif transition == "settle":
            call.settle(denied(project))
        else:
            call.release(reason="not_started")
    assert contents(tmp_path) == before
    call.intent.detach()


@pytest.mark.parametrize("transition", ["release", "settle"])
def test_genuine_not_entered_denial(tmp_path, project, transition):
    call = reserve(tmp_path, project)
    if transition == "release":
        call.release()
    else:
        call.settle(denied(project))
    cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert cc.cost_control_snapshot(global_root=tmp_path)["unresolved_calls"] == 0


@pytest.mark.parametrize("amount,pricing", [(30, "priced"), (None, "partial"), (0, "partial")])
@pytest.mark.parametrize("entry", ["prepare", "settle", "append"])
def test_contradictory_denial_rejected_upfront(tmp_path, project, amount, pricing, entry):
    call = reserve(tmp_path, project)
    record = receipt(project, amount, status="denied", pricing_status=pricing)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        if entry == "prepare":
            call.prepare_finalization(record)
        elif entry == "settle":
            call.settle(record)
        else:
            UsageLedger(project).append(record)
    assert contents(tmp_path) == before
    call.intent.detach()


def test_raw_paid_denial_cannot_be_zeroed_by_reconciliation(tmp_path, project):
    import argus.core.usage as usage

    record = receipt(project, 30, status="denied", total_nano_aiu=3_000_000_000_000)
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.path.write_text(json.dumps(record.to_jsonable()) + "\n")
    before = contents(tmp_path)
    with (
        patch.object(usage, "_copilot_reconcile_enabled_for", return_value=True),
        patch.object(usage, "copilot_usage_store_signature", return_value=[]),
    ):
        with pytest.raises(AccountingIntegrityError):
            ledger.ensure_copilot_usage_reconciled()
    assert contents(tmp_path) == before


def test_denial_cannot_erase_positive_tokens(tmp_path, project):
    record = replace(denied(project), input_tokens=10000)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        UsageLedger(project).append(record)
    assert contents(tmp_path) == before


@pytest.mark.parametrize("new_amount", [0, 5, 40])
def test_explicit_priced_correction_is_counted_once(tmp_path, project, new_amount):
    call = reserve(tmp_path, project)
    call.mark_execution_started()
    old = receipt(project, 30)
    call.settle(old)
    ledger = UsageLedger(project)
    new = replace(old, cost_usd=new_amount)
    with ledger._locked():
        _rewrite_usage_rows(ledger.path, [new.to_jsonable()])
    assert json.loads(ledger.path.read_text())["accounting_history"] == [old.to_jsonable()]
    assert ledger.summary().known_cost_usd == new_amount
    before = contents(tmp_path)
    cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert contents(tmp_path) == before


@pytest.mark.parametrize(
    "field",
    [
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    ],
)
@pytest.mark.parametrize("duplicates", [2, 5])
def test_residual_uses_call_local_unique_events(project, field, duplicates):
    a = dict(session_id=" s ", usage_event_id=1, cost_usd=1, **{field: 60})
    b = dict(session_id="s", usage_event_id=2, cost_usd=1)
    record = replace(receipt(project, 2), **{field: 100}, model_usage=tuple([a] * duplicates + [b]))
    result = summarize_usage([record])
    assert getattr(result, field) == 100
    assert result.known_cost_usd == 2
    overlap = replace(record, call_id="overlap")
    result = summarize_usage([record, overlap])
    assert getattr(result, field) >= 100  # documented conservative incomplete-token overlap
    assert result.known_cost_usd == 2


@pytest.mark.parametrize(
    "kind", ["missing", "preparation", "post_entry_0", "post_entry_30", "post_entry_typed"]
)
def test_real_backend_real_runner_boundaries_fake_process(tmp_path, project, monkeypatch, kind):
    # Keep real runner phase methods; replace only command construction and the
    # process constructor/stream. No executable (including --help) is invoked.
    import test_agent_cli_backend as fixtures

    from argus.adapters.agent_cli_backend import AgentCliBackend, _accounting_admission
    from argus.agent_cli._run_exec import RunExecMixin
    from argus.core.models import RunnerOptions

    fixtures.fake_agent_cli.__wrapped__(monkeypatch)
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, global_root=tmp_path, mission_id="test")
    held = []
    real = _accounting_admission.accounting_admission

    def admission(ctx, model):
        value = real(ctx, model)
        held.append(value.reservation)
        return value

    monkeypatch.setattr(_accounting_admission, "accounting_admission", admission)
    constructor = []

    class SyntheticPreparationError(FileNotFoundError):
        """An ordinary typed error is not a producer capability."""

    class Driver(RunExecMixin):
        backend = "copilot" if kind == "preparation" else "codex"
        before_exec = None
        default_extra_args = []
        agent_bin = "/synthetic/nonexistent-b3-provider"

        def _apply_sandbox_policy(self, options):
            return options

        def _run_exec_start_gate(self, **kw):
            return None

        def _acp_enabled(self, *args):
            return False

        def _build_command(self, **kw):
            if kind == "preparation":
                raise SyntheticPreparationError("synthetic preparation refusal")
            return [self.agent_bin]

        def _prepare_prompt_delivery(self, command, *args, **kw):
            return command, "", None

        def _resolve_executable(self, executable):
            return executable

        def _prompt_stdin(self, prompt):
            return nullcontext(None)

        def _child_env(self, *args, **kw):
            return {}

        def _stream_turn_output(self, **kw):
            if kind == "post_entry_30":
                held[0].observe_cost(30, tokens=120)
            if kind == "post_entry_typed":
                raise SyntheticPreparationError("typed error after provider entry")
            raise FileNotFoundError("same synthetic missing file text")

    def popen(*args, **kw):
        constructor.append(1)
        if kind == "missing":
            raise FileNotFoundError("same synthetic missing file text")
        return SimpleNamespace()

    monkeypatch.setattr("subprocess.Popen", popen)

    def runner(*args, **kw):
        options = SimpleNamespace(
            working_dir=None,
            isolate_workdir=False,
            extra_args=["--session-id=conflict"],
            _bind_provider_session=lambda *a: None,
        )
        return Driver()._run_prepared_exec(
            prompt="synthetic", resume_thread_id=None, options=options, run_label="test"
        )

    monkeypatch.setattr(type(backend._runner), "run_exec", runner)
    result = backend.run_exec(
        prompt="synthetic", options=RunnerOptions(model="gpt-5.6-sol"), run_label="test"
    )
    if kind == "missing":
        assert len(constructor) == 1
        rows = UsageLedger(project).records()
        assert len(rows) == 1 and rows[0].cost_usd == 0
        assert held[0].intent.data["no_charge_evidence"]["kind"] == (
            "process_create_enoent"
        )
        assert held[0].intent.data["execution_started"] is True  # possible marker retained
        cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    else:
        # Current upstream has no PR130 prepare_session producer. A preparation
        # exception, like a post-entry typed error, cannot certify a free call.
        assert constructor == ([] if kind == "preparation" else [1])
        assert result.exit_code == -1 and "accounting integrity" in result.fatal_error
        assert not UsageLedger(project).records()
        assert held[0].intent.data["phase"] == "failed"
        state = json.loads((tmp_path / "cost-control.json").read_text())
        assert len(state["reservations"]) == 1
        assert state["reservations"][0].get("observed_cost_usd", 0) == (
            30 if kind.endswith("30") else 0
        )
        with pytest.raises(AccountingIntegrityError):
            cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_retry_scope_cannot_certify_later_enoent(tmp_path, project, monkeypatch):
    from argus.core.no_charge_provenance import accounted_popen, accounting_scope

    call = reserve(tmp_path, project)
    call.mark_execution_started()
    outcomes = iter([SimpleNamespace(), FileNotFoundError("missing on retry")])

    def constructor(*a, **kw):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("subprocess.Popen", constructor)

    @accounting_scope
    def work(ctx):
        accounted_popen(["synthetic"])
        with pytest.raises(FileNotFoundError):
            accounted_popen(["synthetic"])

    work(SimpleNamespace(cost_reservation=call))
    assert "no_charge_evidence" not in call.intent.data
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.settle(denied(project))
    assert contents(tmp_path) == before
    call.intent.detach()


def test_closed_no_charge_proof_missing_is_fail_closed(tmp_path, project, monkeypatch):
    from argus.core.no_charge_provenance import accounted_popen, accounting_scope

    call = reserve(tmp_path, project)
    call.mark_execution_started()

    def constructor(*args, **kwargs):
        raise FileNotFoundError("synthetic missing binary")

    monkeypatch.setattr("subprocess.Popen", constructor)

    @accounting_scope
    def work(ctx):
        with pytest.raises(FileNotFoundError):
            accounted_popen(["synthetic"])

    work(SimpleNamespace(cost_reservation=call))
    call.settle(denied(project))
    data = json.loads(call.intent.path.read_text())
    data.pop("no_charge_evidence")
    call.intent.path.write_text(json.dumps(data))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert contents(tmp_path) == before


@pytest.mark.parametrize("observed", [0, 30])
def test_unknown_obligation_cannot_be_discharged_by_late_denial(tmp_path, project, observed):
    call = reserve(tmp_path, project)
    call.mark_execution_started()
    call.observe_cost(observed)
    call.settle_unknown(reason="synthetic missing tail")
    UsageLedger(project).append(denied(project))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert contents(tmp_path) == before


def test_token_only_observation_is_not_free(tmp_path, project):
    call = reserve(tmp_path, project)
    call.observe_cost(0, tokens=120)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.settle(denied(project))
    with pytest.raises(AccountingIntegrityError):
        call.prove_no_charge(proof={"kind": "process_create_enoent", "producer_checked": True})
    assert contents(tmp_path) == before
    call.intent.detach()


def test_next_attempt_invalidates_refusal_proof(tmp_path, project, monkeypatch):
    from argus.core.no_charge_provenance import accounted_popen, accounting_scope

    call = reserve(tmp_path, project)
    call.mark_execution_started()
    count = []

    def constructor(*a, **kw):
        count.append(1)
        if len(count) == 1:
            raise FileNotFoundError("synthetic")
        return SimpleNamespace()

    monkeypatch.setattr("subprocess.Popen", constructor)

    @accounting_scope
    def work(ctx):
        with pytest.raises(FileNotFoundError):
            accounted_popen(["synthetic"])
        assert call.intent.data["no_charge_evidence"]["kind"] == "process_create_enoent"
        accounted_popen(["synthetic"])

    work(SimpleNamespace(cost_reservation=call))
    assert "no_charge_evidence" not in call.intent.data
    assert "no_charge_evidence" not in json.loads(call.intent.path.read_text())
    with pytest.raises(AccountingIntegrityError):
        call.settle(denied(project))
    call.intent.detach()


@pytest.mark.parametrize("state", ["not_entered", "started"])
def test_producer_label_cannot_mint_provenance(tmp_path, project, state):
    call = reserve(tmp_path, project)
    if state == "started":
        call.mark_execution_started()
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.prove_no_charge(
            proof={
                "kind": "process_create_enoent",
                "producer_checked": True,
                "call_id": call.call_id,
            }
        )
    assert contents(tmp_path) == before
    call.intent.detach()


def test_producer_capability_is_single_use_and_scope_bound(tmp_path, project, monkeypatch):
    from argus.core.no_charge_provenance import accounted_popen, accounting_scope

    call = reserve(tmp_path, project)
    call.mark_execution_started()
    proofs = []
    real = call.prove_no_charge

    def capture(*, proof):
        proofs.append(proof)
        return real(proof=proof)

    monkeypatch.setattr(call, "prove_no_charge", capture)

    def constructor(*args, **kwargs):
        raise FileNotFoundError("synthetic")

    monkeypatch.setattr("subprocess.Popen", constructor)

    @accounting_scope
    def work(ctx):
        with pytest.raises(FileNotFoundError):
            accounted_popen(["synthetic"])
        before = contents(tmp_path)
        with pytest.raises(AccountingIntegrityError):
            real(proof=proofs[0])
        assert contents(tmp_path) == before

    work(SimpleNamespace(cost_reservation=call))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        real(proof=proofs[0])
    assert contents(tmp_path) == before
    call.settle(denied(project))


def test_real_producer_proof_cannot_override_observed_tokens(tmp_path, project, monkeypatch):
    from argus.core.no_charge_provenance import accounted_popen, accounting_scope

    call = reserve(tmp_path, project)
    call.mark_execution_started()
    call.observe_cost(0, tokens=120)

    def constructor(*args, **kwargs):
        raise FileNotFoundError("synthetic")

    monkeypatch.setattr("subprocess.Popen", constructor)
    before = contents(tmp_path)

    @accounting_scope
    def work(ctx):
        with pytest.raises(AccountingIntegrityError):
            accounted_popen(["synthetic"])

    work(SimpleNamespace(cost_reservation=call))
    assert contents(tmp_path) == before
    assert "no_charge_evidence" not in call.intent.data
    call.intent.detach()
