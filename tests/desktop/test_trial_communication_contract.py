"""Windows preview checks derived from communication-review.zh-CN.md."""
import json
from types import SimpleNamespace

import pytest

from argus_skill.trial import attention, client, desktop
from argus_skill.trial.storage import write_private

KEY = "argus_trial_" + "c" * 64


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv(client.TRIAL_ENV, "1")
    monkeypatch.delenv("ARGUS_DESKTOP_TRIAL_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_WORKBENCH_HOST_ROOT", raising=False)
    write_private(tmp_path / "copilot-trial.json", json.dumps({
        "api_key": KEY, "base_url": "https://argusbot.cn/v1",
    }).encode())
    return tmp_path


def test_status_request_keeps_public_protocol_and_no_redirects(home, monkeypatch):
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            return json.dumps({"tokens_remaining": 123, "token_limit": 1_000_000,
                               "api_key": "must-not-return", "unknown": "ignored"}).encode()
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return Response()
    def build(*handlers):
        assert any(isinstance(h, client._NoRedirect) for h in handlers)
        return Opener()
    monkeypatch.setattr(client.urllib.request, "build_opener", build)
    result = client.query_status("https://argusbot.cn", KEY)
    request = requests[0]
    assert request.full_url == "https://argusbot.cn/trial/status"
    assert request.get_header("Authorization") == "Bearer " + KEY
    assert request.get_header("User-agent") == "Argus/0.1.1"
    assert KEY not in request.full_url
    assert set(result) == {"tokens_remaining", "token_limit"}
    assert client._NoRedirect().redirect_request(None, None, 302, None, {}, "https://other.example") is None


def test_stream_error_is_not_turned_into_success_by_final_http_200(home):
    from argus_skill.agent_cli._event_consumers import EventConsumerMixin
    state = EventConsumerMixin._consume_copilot_event(event={
        "error": {"code": "provider_usage_missing", "message": "missing usage"},
    }, thread_id="fixture", agent_messages=[], turn_completed=False, turn_failed=False, fatal_error=None)
    assert state[1:3] == (False, True)
    state = EventConsumerMixin._consume_copilot_event(event={"type": "result", "exitCode": 0},
        thread_id=state[0], agent_messages=[], turn_completed=state[1], turn_failed=state[2], fatal_error=state[3])
    assert state[1:3] == (False, True)
    assert "provider_usage_missing" in state[3]


def test_quoted_error_in_assistant_prose_is_not_a_protocol_failure(home):
    from argus_skill.agent_cli._event_consumers import EventConsumerMixin
    state = EventConsumerMixin._consume_copilot_event(event={"type": "assistant.message",
        "data": {"content": "Documentation describes trial_quota_exceeded."}},
        thread_id="fixture", agent_messages=[], turn_completed=False, turn_failed=False, fatal_error=None)
    assert state[2] is False and state[3] is None


def test_ambiguous_usage_stops_retries_until_explicit_balance_check(home, monkeypatch):
    attention.record_failure("provider_stream_incomplete")
    assert attention.reason()
    assert KEY not in (home / "trial-attention.json").read_text()
    monkeypatch.setattr(desktop, "query_status", lambda *_: {"token_limit": 100, "tokens_remaining": 42})
    assert desktop.current_status()["paused"] is True  # A refresh alone is not permission to retry.
    assert desktop.current_status(resume_trial=True)["paused"] is False
    assert not attention.reason()


def test_tpm_requires_wait_and_does_not_reset_allowance(home, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(attention.time, "time", lambda: now[0])
    attention.record_failure("trial_tpm_exceeded")
    with pytest.raises(ValueError, match="60"):
        attention.resume()
    now[0] = 161
    attention.resume()
    assert not attention.reason()


def test_onboarding_failure_does_not_pause_current_trial(home, monkeypatch):
    monkeypatch.setenv("ARGUS_DESKTOP_TRIAL_PROFILE", str(home / "candidate.json"))
    attention.record_failure("provider_stream_failed")
    assert not (home / "trial-attention.json").exists()


def test_file_backed_trial_key_is_registered_for_redaction(home):
    assert any(value == KEY for value in client.runtime_redactions())
    from argus_skill.adapters.agent_cli_backend._core import AgentCliBackend
    from argus_skill.adapters.agent_cli_backend._io_log import AgentIOLogger
    backend = SimpleNamespace(_is_copilot=True, _known_secret_values_override=())
    AgentCliBackend._refresh_known_secret_values(backend)
    logger = AgentIOLogger()
    path = home / "redaction-check.jsonl"
    logger.log(path, {"type": "fixture", "message": KEY}, known_secret_values=backend._known_secret_values)
    assert not any(value == KEY for value in json.loads(path.read_text()).values())
    assert KEY not in path.read_text()


def test_explicit_non_alias_model_cannot_bypass_trial_routing(home, monkeypatch):
    from argus_skill.adapters.agent_cli_backend import _exec
    from argus_skill.core.models import RunnerOptions
    backend = SimpleNamespace(_runner=SimpleNamespace(backend="copilot"),
        _refresh_known_secret_values=lambda: None, _resolve_execution_options=lambda o: o)
    monkeypatch.setattr(_exec, "_execute_prepared", lambda *a, **k: pytest.fail("must not use a builtin/personal model"))
    result = _exec.execute(backend, prompt="fixture", options=RunnerOptions(model="unverified-model"), run_label="fixture")
    assert result.exit_code != 0 and "服务端选择真实模型" in result.fatal_error


def test_paused_trial_cannot_spawn_a_second_model_call(home, monkeypatch):
    from argus_skill.adapters.agent_cli_backend import _exec
    from argus_skill.core.models import RunnerOptions
    attention.record_failure("provider_connection_failed")
    backend = SimpleNamespace(_runner=SimpleNamespace(backend="copilot"),
        _refresh_known_secret_values=lambda: None, _resolve_execution_options=lambda o: o)
    monkeypatch.setattr(_exec, "_execute_prepared", lambda *a, **k: pytest.fail("must not retry a paid call"))
    result = _exec.execute(backend, prompt="fixture", options=RunnerOptions(), run_label="fixture")
    assert result.exit_code != 0 and result.stop_kind == "permanent_error"
    assert "自动重试" in result.fatal_error
