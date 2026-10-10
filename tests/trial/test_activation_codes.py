"""Activation codes: USD allowances enforced at the trial gateway."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from argus.trial import activation_web
from argus.trial import web_portal as portal
from argus.trial.admin import expiry_timestamp, format_codes, issue_codes, usd_amount
from argus.trial.gateway import Settings, create_app, prepare
from argus.trial.pricing import Price, load_prices, provider_nano_aiu
from argus.trial.secrets import Vault, write_private
from argus.trial.store import NANO_AIU_PER_USD, Store, TrialError

USD = NANO_AIU_PER_USD
CENT = USD // 100
MODEL_ID = "priced-model"
PRICES = {MODEL_ID: Price(input=5, output=30, cache_read=0.5)}
PAYLOAD = {"model": "argus-trial", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 100}
ORIGIN = "https://portal.test"


def coded_store(tmp_path, limit=USD, key_id="trial-01"):
    store = Store(tmp_path / "usage.sqlite3", key_limit=100)
    store.issue_code(key_id, "secret-" + key_id, usd_limit=limit, label="beta")
    return store


def test_reservation_settles_to_the_reported_cost_and_refunds_the_rest(tmp_path):
    store = coded_store(tmp_path)
    request = store.reserve("trial-01", 1000, cost=30 * CENT)
    assert store.status("trial-01")["usd_remaining"] == pytest.approx(0.70)
    store.settle(request, 400, 7 * CENT, "provider")
    status = store.status("trial-01")
    assert status["usd_spent"] == pytest.approx(0.07) and status["usd_remaining"] == pytest.approx(0.93)
    assert status["usd_limit"] == 1.0 and status["label"] == "beta" and not status["quota_exhausted"]
    with store.transaction() as db:
        assert db.execute("SELECT source FROM trial_request_costs").fetchone()[0] == "provider"


def test_ambiguous_outcomes_keep_the_whole_cost_reservation(tmp_path):
    store = coded_store(tmp_path)
    unknown = store.reserve("trial-01", 1000, cost=20 * CENT)
    store.settle(unknown, None, 1 * CENT)  # Usage unknown: a reported cost cannot refund it.
    without_cost = store.reserve("trial-01", 1000, cost=10 * CENT)
    store.settle(without_cost, 50, None)  # Usage known, cost unknown: keep the bound.
    nothing = store.reserve("trial-01", 1000, cost=10 * CENT)
    store.settle(nothing, 0, None)  # Nothing generated: nothing owed.
    assert store.status("trial-01")["usd_spent"] == pytest.approx(0.30)


def test_restart_recovery_never_refunds_submitted_cost(tmp_path):
    store = coded_store(tmp_path)
    store.reserve("trial-01", 1000, cost=25 * CENT, operation_key="submitted")
    store.submit_operation("submitted")
    store.reserve("trial-01", 1000, cost=25 * CENT, operation_key="never-sent")
    store.recover()
    assert store.status("trial-01")["usd_spent"] == pytest.approx(0.25)


def test_exhaustion_refuses_with_balance_in_both_languages(tmp_path):
    store = coded_store(tmp_path)
    store.settle(store.reserve("trial-01", 1000, cost=95 * CENT), 1000, 95 * CENT)
    with pytest.raises(TrialError) as refused:
        store.reserve("trial-01", 1000, cost=10 * CENT)
    assert refused.value.status == 402 and refused.value.code == "quota_insufficient"
    assert "额度不足" in str(refused.value) and "$0.0500 of $1.00 remaining" in str(refused.value)
    assert "up to $0.1000" in str(refused.value)
    assert store.status("trial-01")["usd_spent"] == pytest.approx(0.95)
    store.settle(store.reserve("trial-01", 1000, cost=5 * CENT), 1000, 5 * CENT)
    assert store.status("trial-01")["quota_exhausted"] is True
    with pytest.raises(TrialError) as used_up:
        store.reserve("trial-01", 1000, cost=1)
    assert used_up.value.status == 402 and used_up.value.code == "quota_exhausted"
    assert "额度已用完" in str(used_up.value) and "quota used up" in str(used_up.value)
    assert "$0.0000 of $1.00 remaining" in str(used_up.value)
    # The workspace's trial client names the refusal from the code in its text.
    from argus.trial.attention import MESSAGES
    for error in (refused.value, used_up.value):
        assert error.code in MESSAGES and error.code in str(error)


def test_concurrent_reservations_never_exceed_the_allowance(tmp_path):
    store = coded_store(tmp_path, limit=5 * 10 * CENT)

    def attempt(_):
        try:
            return store.reserve("trial-01", 100, cost=10 * CENT)
        except TrialError as exc:
            assert exc.code in {"quota_exhausted", "quota_insufficient"}
            return None

    with ThreadPoolExecutor(max_workers=16) as workers:
        admitted = [request for request in workers.map(attempt, range(40)) if request is not None]
    assert len(admitted) == 5
    assert store.status("trial-01")["usd_spent"] == pytest.approx(0.50)


def test_overshoot_is_bounded_by_what_concurrent_requests_exceed_their_reservations(tmp_path):
    limit = 50 * CENT
    store = coded_store(tmp_path, limit=limit)
    reserved, excess = 10 * CENT, 2 * CENT
    with ThreadPoolExecutor(max_workers=8) as workers:
        admitted = [request for request in workers.map(
            lambda _: _try_reserve(store, reserved), range(20)) if request is not None]
    for request in admitted:  # Worst case: every admitted request costs more than reserved.
        store.settle(request, 100, reserved + excess)
    spent = round(store.status("trial-01")["usd_spent"] * USD)
    assert limit < spent <= limit + len(admitted) * excess
    assert _try_reserve(store, 1) is None


def _try_reserve(store, cost):
    try:
        return store.reserve("trial-01", 100, cost=cost)
    except TrialError:
        return None


def test_unpriced_requests_are_refused_only_for_codes(tmp_path):
    store = coded_store(tmp_path)
    with pytest.raises(TrialError) as refused:
        store.reserve("trial-01", 100, cost=0)
    assert refused.value.code == "model_unpriced"
    store.issue("legacy", "legacy-secret")
    store.reserve("legacy", 100, cost=0)  # Token-metered keys are unchanged.


def test_codes_ignore_the_token_limit_but_legacy_keys_keep_it(tmp_path):
    store = Store(tmp_path / "usage.sqlite3", token_limit=100, key_limit=100)
    store.issue_code("trial-01", "secret", usd_limit=USD)
    store.issue("legacy", "legacy-secret")
    store.reserve("trial-01", 1000, cost=CENT)
    with pytest.raises(TrialError, match="Insufficient trial tokens"):
        store.reserve("legacy", 1000)


def test_prices_bound_reservations_and_price_fallback():
    price = PRICES[MODEL_ID]
    # Every input token is reserved at the dearest input rate.
    assert price.reserve(1000, 100) == 1000 * 5 * USD // 1_000_000 + 100 * 30 * USD // 1_000_000
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 800}}
    assert price.charge(usage) == (200 * 5 + 800 * 0.5 + 100 * 30) * USD // 1_000_000
    assert price.charge(usage) < price.reserve(1000, 100)
    assert load_prices({MODEL_ID: {"input": 1, "output": 2}})[MODEL_ID].cache_read is None
    for invalid in ({"input": 1}, {"input": -1, "output": 1}, {"input": 1, "output": 1, "batch": 3}):
        with pytest.raises(ValueError):
            Price.load(invalid)
    assert provider_nano_aiu({"usage": {}}, {"copilot_usage": {"total_nano_aiu": 7}}) == 7
    assert provider_nano_aiu({"copilot_usage": {"total_nano_aiu": -1}}) is None


@pytest.fixture
def settings(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    key = tmp_path / "key"
    write_private(key, Fernet.generate_key())
    Vault(key, state / "github-token.enc").save("provider-login-never-forwarded")
    return Settings(state, key, model=MODEL_ID, timeout=5, prices=PRICES, models=("unpriced-model",))


def coded_auth(client, usd_limit=USD, key_id="trial-01"):
    credential = client.app.state.vault.credential(key_id)
    client.app.state.store.issue_code(key_id, credential, usd_limit=usd_limit)
    return {"Authorization": "Bearer " + credential}


def provider_response(receipt=None, *, on_response=False):
    data = {"id": "r-1", "object": "response", "created_at": 1, "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
                      "input_tokens_details": {"cached_tokens": 800}}}
    if receipt is not None and on_response:
        data["copilot_usage"] = {"total_nano_aiu": receipt, "token_details": []}
    return data


def provider(receipt, *, stream, seen, on_response=False):
    def handler(request):
        seen.append(request)
        # A non-streamed body is the response object itself.
        data = provider_response(receipt, on_response=on_response or not stream)
        if not stream:
            return httpx.Response(200, json=data)
        event = {"type": "response.completed", "response": data}
        if receipt is not None and not on_response:
            event["copilot_usage"] = {"total_nano_aiu": receipt}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n",
                              headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


def spent(client, auth):
    client.portal.call(client.app.state.accounting.wait_idle)
    return client.get("/trial/status", headers=auth).json()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("on_response", [False, True])
def test_gateway_charges_exactly_the_providers_receipt(settings, stream, on_response):
    seen = []
    receipt = 3 * CENT + 123
    with TestClient(create_app(settings, transport=provider(receipt, stream=stream, seen=seen,
                                                            on_response=on_response))) as client:
        auth = coded_auth(client)
        response = client.post("/v1/chat/completions", headers=auth, json={**PAYLOAD, "stream": stream})
        assert response.status_code == 200
        assert "_argus_copilot_usage" not in response.text and "copilot_usage" not in response.text
        status = spent(client, auth)
    assert round(status["usd_spent"] * USD) == receipt
    assert seen[0].headers["authorization"] == "Bearer provider-login-never-forwarded"
    assert auth["Authorization"][7:] not in seen[0].content.decode()


def test_gateway_falls_back_to_the_price_table_without_a_receipt(settings):
    with TestClient(create_app(settings, transport=provider(None, stream=False, seen=[]))) as client:
        auth = coded_auth(client)
        assert client.post("/v1/chat/completions", headers=auth, json=PAYLOAD).status_code == 200
        status = spent(client, auth)
        with client.app.state.store.transaction() as db:
            source = db.execute("SELECT source FROM trial_request_costs").fetchone()[0]
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 800}}
    assert round(status["usd_spent"] * USD) == PRICES[MODEL_ID].charge(usage)
    assert source == "price_table"


def test_gateway_refuses_unpriced_and_exhausted_codes_before_the_provider(settings):
    seen = []
    with TestClient(create_app(settings, transport=provider(CENT, stream=False, seen=seen))) as client:
        auth = coded_auth(client)
        unpriced = client.post("/v1/chat/completions", headers=auth, json={**PAYLOAD, "model": "unpriced-model"})
        assert unpriced.status_code == 400 and unpriced.json()["error"]["code"] == "model_unpriced"
        _, tokens = prepare(PAYLOAD, settings.model)
        needed = PRICES[MODEL_ID].reserve(tokens - 100, 100)
        poor = coded_auth(client, usd_limit=needed - 1, key_id="trial-02")
        refused = client.post("/v1/chat/completions", headers=poor, json=PAYLOAD)
        assert refused.status_code == 402
        assert refused.json()["error"]["code"] == "quota_insufficient"
        assert "额度不足" in refused.json()["error"]["message"]
        status = client.get("/trial/status", headers=poor).json()
        assert status["usd_spent"] == 0 and status["usd_remaining"] == pytest.approx((needed - 1) / USD)
    assert seen == []


def test_gateway_rejects_credentials_that_are_not_issued_codes(settings):
    with TestClient(create_app(settings, transport=provider(CENT, stream=False, seen=[]))) as client:
        for header in ({}, {"Authorization": "Bearer old-public-access-token"},
                       {"Authorization": "Bearer argus_trial_" + "0" * 64}):
            assert client.post("/v1/chat/completions", headers=header, json=PAYLOAD).status_code == 401


def test_issue_codes_hashes_codes_lists_spend_and_continues_numbering(tmp_path, capsys):
    key = tmp_path / "master.key"
    write_private(key, Fernet.generate_key())
    vault = Vault(key, tmp_path / "github-token.enc")
    output = tmp_path / "codes.json"
    first = issue_codes(vault, tmp_path, output, count=2, usd_limit=usd_amount("2.50"), label="workshop")
    later = issue_codes(vault, tmp_path, output, count=1, usd_limit=usd_amount("1"),
                        expires_at=expiry_timestamp("2999-01-01"))
    assert first == ["trial-01", "trial-02"] and later == ["trial-03"]
    codes = json.loads(output.read_text())
    printed = capsys.readouterr().out
    store = Store(tmp_path / "usage.sqlite3", key_limit=100)
    assert all(code["code"] not in printed and code["code"].encode() not in store.path.read_bytes() for code in codes)
    store.settle(store.reserve("trial-01", 10, cost=CENT), 10, CENT)
    rows = {row["key_id"]: row for row in store.list_codes()}
    assert rows["trial-01"]["usd_spent"] == 0.01 and rows["trial-01"]["usd_remaining"] == 2.49
    assert rows["trial-01"]["label"] == "workshop" and rows["trial-01"]["last_used_at"] is not None
    assert rows["trial-02"]["last_used_at"] is None and rows["trial-03"]["expires_at"] is not None
    table = format_codes(store.list_codes())
    assert "workshop" in table and all(code["code"] not in table for code in codes)
    if hasattr(output, "stat"):
        assert output.stat().st_mode & 0o777 == 0o600
    for invalid in ("0", "-1", "abc", "0.000000000001"):
        with pytest.raises(ValueError):
            usd_amount(invalid)


def test_expired_code_is_refused(tmp_path):
    clock = [1_000.0]
    store = Store(tmp_path / "usage.sqlite3", key_limit=100, clock=lambda: clock[0])
    store.issue_code("trial-01", "secret", usd_limit=USD, expires_at=2_000.0)
    store.reserve("trial-01", 10, cost=CENT)
    clock[0] = 2_000.0
    with pytest.raises(TrialError) as refused:
        store.authenticate("secret")
    assert refused.value.code == "trial_access_disabled"


class OneChunk(httpx.AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        yield self.body


@pytest.fixture
def code_portal(tmp_path):
    state = tmp_path / "meter"
    state.mkdir()
    key = tmp_path / "master.key"
    write_private(key, Fernet.generate_key())
    vault = Vault(key, state / "github-token.enc")
    store = Store(state / "usage.sqlite3", key_limit=100)
    for tenant, limit in (("trial-01", USD), ("trial-02", 2 * USD)):
        store.issue_code(tenant, vault.credential(tenant), usd_limit=limit)
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html><head></head><body><div id=root></div></body></html>")
    config = {
        "state_dir": str(state), "key_file": str(key), "token_limit": None, "activation_codes": True,
        "frontend_dir": str(frontend),
        "tenants": {tenant: {"url": f"http://{tenant}.internal", "token": f"web-token-{tenant}"}
                    for tenant in ("trial-01", "trial-02")},
    }
    return config, vault, store


def test_each_code_reaches_only_its_own_workspace(code_portal):
    config, vault, store = code_portal
    reached = []

    def backend(request):
        reached.append((request.url.host, request.headers["authorization"]))
        body = json.dumps({"projects": [request.url.host]}).encode()
        return httpx.Response(200, stream=OneChunk(body), headers={"content-type": "application/json"})

    app = portal.create_app(config, transport=httpx.MockTransport(backend))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as alice, \
            TestClient(app, base_url=ORIGIN, follow_redirects=False) as bob:
        for client, tenant in ((alice, "trial-01"), (bob, "trial-02")):
            assert client.post("/invite/login", headers={"Origin": ORIGIN},
                               json={"code": vault.credential(tenant)}).status_code == 200
        assert alice.get("/api/projects").json() == {"projects": ["trial-01.internal"]}
        assert bob.get("/api/projects").json() == {"projects": ["trial-02.internal"]}
        store.settle(store.reserve("trial-01", 10, cost=40 * CENT), 10, 40 * CENT)
        alice_status, bob_status = alice.get("/invite/status").json(), bob.get("/invite/status").json()
    assert reached == [("trial-01.internal", "Bearer web-token-trial-01"),
                       ("trial-02.internal", "Bearer web-token-trial-02")]
    assert alice_status["usd_remaining"] == pytest.approx(0.60) and alice_status["usd_limit"] == 1.0
    assert bob_status["usd_remaining"] == 2.0 and bob_status["usd_spent"] == 0


def test_old_public_token_alone_grants_nothing(code_portal):
    config, _, _ = code_portal
    reached = []
    app = portal.create_app(config, transport=httpx.MockTransport(lambda r: reached.append(r) or httpx.Response(200)))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        assert client.get("/?token=old-public-access-token").status_code == 401
        assert client.get("/", headers={"Authorization": "Bearer old-public-access-token"}).status_code == 303
        assert client.post("/api/projects/p/message/stream", headers={
            "Origin": ORIGIN, "Authorization": "Bearer web-token-trial-01"}, json={"text": "hi"}).status_code == 401
        assert client.post("/invite/login", headers={"Origin": ORIGIN},
                           json={"code": "old-public-access-token"}).status_code == 400
    assert reached == []


def test_workspace_header_shows_the_usd_balance_and_used_up_state(code_portal):
    config, vault, store = code_portal
    app = portal.create_app(config, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.post("/invite/login", headers={"Origin": ORIGIN}, json={"code": vault.credential("trial-01")})
        page = client.get("/")
        assert '<script nonce="' in page.text and 'src="/invite/quota.js"' in page.text
        script = client.get("/invite/quota.js").text
        assert "额度已用完" in script and "quota used up" in script and "usd_remaining" in script
        store.settle(store.reserve("trial-01", 10, cost=USD), 10, USD)
        status = client.get("/invite/status").json()
    assert status["quota_exhausted"] is True and status["usd_remaining"] == 0


def test_activation_portal_settings_accept_any_contiguous_tenant_set(code_portal):
    config, _, _ = code_portal
    assert portal.Settings.load(config).activation_codes
    gap = {**config, "tenants": {"trial-01": config["tenants"]["trial-01"],
                                 "trial-03": {"url": "http://c.internal", "token": "web-token-c"}}}
    with pytest.raises(ValueError, match="contiguous"):
        portal.Settings.load(gap)
    with pytest.raises(ValueError, match="trial-11"):
        portal.Settings.load({**config, "activation_codes": False})


def test_provisioned_runtimes_are_isolated_from_each_other_and_the_operator(tmp_path):
    root = tmp_path / "deployment"
    source, venv, binary = tmp_path / "src", tmp_path / "venv", tmp_path / "copilot"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").touch()
    source.mkdir()
    binary.touch()
    activation_web.initialize(root, source=source, venv=venv, copilot_bin=binary, copilot_pkg=None,
                              public_origin="https://codes.example")
    vault = Vault(root / "secrets/master.key", root / "meter/github-token.enc")
    issue_codes(vault, root / "meter", root / "codes/c.json", count=2, usd_limit=USD)
    config = activation_web.provision(root)
    assert config["activation_codes"] and set(config["tenants"]) == {"trial-01", "trial-02"}
    assert config["public_origin"] == "https://codes.example"
    portal.Settings.load(config)
    command = activation_web.sandbox_command(root, "trial-01")
    joined = " ".join(command)
    assert "--unshare-all" in command and "--clearenv" in command
    assert str(root / "tenants/trial-01/data") in joined and "trial-02" not in joined
    for private in (root / "secrets", root / "meter", root / "codes", tmp_path / "home"):
        assert str(private) + " " not in joined + " " and str(private) + "/" not in joined
    runtime = json.loads((root / "tenants/trial-01/bootstrap/runtime.json").read_text())
    assert runtime["api_key"] == vault.credential("trial-01") and runtime["tenant_id"] == "trial-01"
    assert json.loads((root / "prices.json").read_text())  # A reservation bound exists from the start.
    units = activation_web.install_units(root, port=8989, prefix="t", unit_dir=tmp_path / "units")
    assert "t-runtime@.service" in units
    assert "--prices" in (tmp_path / "units/t-gateway.service").read_text()


def test_token_trials_need_no_price_table(settings):
    plain = replace(settings, prices=None, models=None)
    with TestClient(create_app(plain, transport=provider(None, stream=False, seen=[]))) as client:
        credential = client.app.state.vault.credential("legacy")
        client.app.state.store.issue("legacy", credential)
        auth = {"Authorization": "Bearer " + credential}
        assert client.post("/v1/chat/completions", headers=auth, json=PAYLOAD).status_code == 200
        assert "usd_limit" not in spent(client, auth)
