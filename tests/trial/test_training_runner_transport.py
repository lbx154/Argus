"""Internal training capabilities survive runtime policy but never public logging."""
import copy
import dataclasses
import json

from argus_skill.agent_cli.agent_cli_runner import (
    AgentCliRunner,
    PrivateRunnerEnvironment,
    RunnerOptions,
)
from argus_skill.trial import training_runtime


def test_transport_survives_actual_sandbox_copy_without_repr_or_asdict_disclosure(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "0")
    secret = "synthetic-sensitive-capability-for-copy-test"
    private = PrivateRunnerEnvironment({training_runtime.SOCKET_ENV: "/private/test.sock", training_runtime.LEASE_ENV: secret})
    options = RunnerOptions(_training_extension="/immutable/pi_training_extension.mjs", _training_environment=private)
    runner = AgentCliRunner(backend="pi", agent_bin="pi")
    actual = runner._apply_sandbox_policy(options)
    assert actual is not options
    assert actual._training_environment is private
    assert actual._training_extension == options._training_extension
    assert actual._training_extension in runner._build_command(resume_thread_id=None, options=actual)
    assert runner._child_env(actual)[training_runtime.LEASE_ENV] == secret
    assert secret not in repr(options) and secret not in repr(vars(options))
    public = dataclasses.asdict(options)
    assert public["_training_environment"] is None
    assert secret not in json.dumps(public)
    assert copy.deepcopy(options)._training_environment is None


def test_real_dataclass_cleanup_restores_none_after_success_and_error(monkeypatch):
    from types import SimpleNamespace

    options = RunnerOptions()
    runner = AgentCliRunner(backend="pi", agent_bin="pi")
    context = SimpleNamespace(backend=SimpleNamespace(_backend_name="pi", _default_extra_args=[], _runner=runner),
                              resume_thread_id=None, usage_mission_id=None, call_id="actual-call", run_label="engineer-test")
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setenv(training_runtime.SOCKET_ENV, "/private/test.sock")
    monkeypatch.setattr(training_runtime, "_project", lambda ctx: "s-project")
    requests = []

    def request(path, action, value, lease=None):
        requests.append(action)
        return {"enabled": True, "lease": "synthetic-only-capability"} if action == "register" else {"closed": True}

    monkeypatch.setattr(training_runtime, "_request", request)
    for raises in (False, True):
        try:
            with training_runtime.capture_runtime_call(context, options):
                assert options._training_environment is not None and options._training_extension
                if raises:
                    raise RuntimeError("synthetic runtime failure")
        except RuntimeError:
            pass
        assert options._training_extension is None and options._training_environment is None
    assert requests == ["register", "close", "register", "close"]
