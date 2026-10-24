"""Continuation context and authoritative operator-stop regression coverage."""




def test_backend_stop_does_not_set_auth_failure_flag(monkeypatch, tmp_path):
    from argus.adapters.agent_cli_backend import AgentCliBackend
    from argus.agent_cli.models import AgentRunResult
    from argus.core.models import RunnerOptions as BackendOptions
    backend=AgentCliBackend(backend="codex")
    raw=AgentRunResult(command=["codex"], exit_code=1, turn_failed=True,
                       fatal_error="External interrupt: daemon stop requested",
                       stderr_lines=["HTTP 401 Unauthorized (recovered earlier)"])
    monkeypatch.setattr(backend._runner, "run_exec", lambda **kwargs: raw)
    result=backend.run_exec(prompt="synthetic fixture", options=BackendOptions(working_dir=str(tmp_path)), run_label="engineer-r1")
    assert result.stop_kind == "daemon_shutdown"
    assert not backend._auth_failure_detected
