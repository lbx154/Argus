from __future__ import annotations

import errno
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as NativeOptions
from argus.core.call_bound_execution import ExecutionCancelled
from argus.core.models import RunnerOptions
from argus.core.role_tool_bridge import bridge_request
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import PREFIX, review_action_tools
from argus.reviewer.validation import (
    CANCEL_TOOL,
    CLEANUP_SECONDS,
    CLOSE_SECONDS,
    COMMAND_TOOL,
    IMAGE_ENV,
    READ_DIRS_ENV,
    RESULT_TOOL,
    ReviewValidation,
    configured_read_dirs,
)
from argus.skills.store import SkillStore


def wait_for_command(options, result):
    deadline = time.monotonic() + 30
    while result["status"] == "running" and time.monotonic() < deadline:
        result = bridge_request(
            PREFIX, RESULT_TOOL, {"command_id": result["command_id"]}, env=options.extension_env,
        )
    assert result["status"] != "running", result
    return result


@pytest.mark.parametrize("raw", ['"relative"', '{"path": "/tmp"}', "[1]", '["relative"]', '["/"]'])
def test_invalid_read_roots_fail_closed(monkeypatch, raw):
    monkeypatch.setenv(READ_DIRS_ENV, raw)
    with pytest.raises((ValueError, FileNotFoundError)):
        configured_read_dirs()


def test_copilot_reviewer_gets_skill_and_operator_read_roots(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    skills = tmp_path / "skills"
    backend = MemoryBackend()
    backend.backend = "copilot"
    backend.queue("reviewer", CannedResponse(
        review_action=("revise_review", {"review": "A source obligation remains."}),
    ))
    monkeypatch.setenv(READ_DIRS_ENV, json.dumps([str(source)]))

    decision = Reviewer(backend, skill_store=SkillStore(skills)).evaluate(
        objective="Independently review the source proof", round_index=1,
        session_id=None, main_summary="A candidate is available.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "continue"
    options = backend.history[0][2]
    assert str(skills.resolve()) in options.add_dirs
    assert str(source.resolve()) in options.add_dirs
    assert options.sandbox_mode == "read-only"
    assert options.force_safe_mode
    assert not options.dangerous_yolo


def test_validation_is_opt_in_and_does_not_grant_a_native_shell(tmp_path, monkeypatch):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    with review_action_tools(
        SimpleNamespace(backend="copilot"),
        RunnerOptions(sandbox_mode="read-only", working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        assert {COMMAND_TOOL, RESULT_TOOL, CANCEL_TOOL} <= {tool["name"] for tool in actions.tools}
        assert actions.decision is None
        native = NativeOptions(
            sandbox_mode=options.sandbox_mode, force_safe_mode=options.force_safe_mode,
            extra_args=options.extra_args, trusted_tool_names=options.trusted_tool_names,
            extension_env=options.extension_env, add_dirs=options.add_dirs,
        )
        argv = AgentCliRunner(agent_bin="copilot", backend="copilot")._build_command(
            resume_thread_id=None, options=native,
        )
        available = argv[argv.index("--available-tools") + 1].split(",")
        assert "bash" not in available
        assert f"argus_review_actions-{COMMAND_TOOL}" in available
        assert "--yolo" not in argv
        assert "--allow-all-paths" not in argv
        assert "--allow-all-tools" not in argv

    monkeypatch.delenv(IMAGE_ENV)
    with review_action_tools(
        SimpleNamespace(backend="copilot"),
        RunnerOptions(sandbox_mode="read-only", working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, _):
        assert COMMAND_TOOL not in {tool["name"] for tool in actions.tools}


def test_command_has_readonly_inputs_no_network_and_no_bridge_credentials(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("ARGUS_PLUGIN_REVIEW_TOKEN", "private-test-token")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///operator/docker.sock")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "context" in argv:
            return subprocess.CompletedProcess(argv, 0, "unix:///var/run/docker.sock\n", "")
        if "inspect" in argv:
            return subprocess.CompletedProcess(argv, 0, "sha256:" + "a" * 64 + "\n", "")
        if "start" in argv:
            kwargs["stdout"].write(b"check output\n")
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("argus.reviewer.validation.run_process", run)
    validator = ReviewValidation(str(project), [str(source)], "ubuntu:24.04")
    result = validator.run({"argv": ["checker", "--out", "{scratch}/proof"]})

    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["stdout"] == "check output\n"
    command, kwargs = next(call for call in calls if "create" in call[0])
    assert command[:2] == ["/usr/bin/docker", "--config"]
    assert command[3:5] == ["--host", "unix:///operator/docker.sock"]
    assert json.loads((Path(command[2]) / "config.json").read_text()) == {}
    assert "DOCKER_HOST" not in kwargs["env"]
    assert "ARGUS_PLUGIN_REVIEW_TOKEN" not in kwargs["env"]
    assert command[command.index("--pull") + 1] == "never"
    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
    assert f"type=bind,src={project},dst={project},readonly" in mounts
    assert f"type=bind,src={source},dst={source},readonly" in mounts
    assert len([mount for mount in mounts if not mount.endswith(",readonly")]) == 1
    container_env = [command[i + 1] for i, value in enumerate(command) if value == "--env"]
    assert not any("ARGUS_PLUGIN" in value or "private-test-token" in value for value in container_env)
    assert command[-1] == result["scratch"] + "/proof"
    assert json.loads((Path(result["stdout_path"]).parent / "result.json").read_text())["status"] == "completed"
    validator.close()


def test_remote_context_cannot_be_disguised_by_a_local_host_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///local/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        "argus.reviewer.validation.run_process",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "ssh://remote\n", ""),
    )
    result = ReviewValidation(str(tmp_path), [], "ubuntu:24.04").run({"argv": ["/bin/true"]})
    assert result["status"] == "environment_error"
    assert "local Unix-socket" in result["error"]


def test_missing_docker_never_runs_unsandboxed(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: None)
    result = ReviewValidation(str(tmp_path), [], "ubuntu:24.04").run({"argv": ["/bin/true"]})
    assert result["status"] == "environment_error"
    assert "refusing unsandboxed" in result["error"]


def test_missing_docker_diagnosis_survives_the_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: None)
    with review_action_tools(
        SimpleNamespace(backend="copilot"),
        RunnerOptions(sandbox_mode="read-only", working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env)
        assert result["status"] == "environment_error"
        assert "Docker is unavailable" in result["error"]
        assert "unsandboxed" in result["error"]
        assert json.loads(Path(result["receipt_path"]).read_text())["error"] == result["error"]
        assert actions.decision is None


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True, "5"])
def test_invalid_execution_deadlines_do_not_start_work(tmp_path, timeout):
    validator = ReviewValidation(str(tmp_path), [], "ubuntu:24.04")
    with pytest.raises(ValueError, match="finite nonnegative"):
        validator.run({"argv": ["/bin/true"], "timeout_seconds": timeout})
    assert not list(validator.output_root.iterdir())


@pytest.mark.parametrize("argv", [None, [], [""], ["a\0b"], [1]])
def test_invalid_commands_do_not_start_work(tmp_path, argv):
    validator = ReviewValidation(str(tmp_path), [], "ubuntu:24.04")
    with pytest.raises(ValueError, match="nonempty"):
        validator.run({"argv": argv})
    assert not list(validator.output_root.iterdir())


def test_command_results_and_cancellation_are_caller_bound(tmp_path, monkeypatch):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: None)
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (_, first_options), review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (second, second_options):
        first = bridge_request(PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=first_options.extension_env)
        for tool in (RESULT_TOOL, CANCEL_TOOL):
            for identifier in (first["command_id"], "../outside"):
                with pytest.raises(ValueError, match="does not belong"):
                    bridge_request(PREFIX, tool, {"command_id": identifier}, env=second_options.extension_env)
        assert second.decision is None
    with pytest.raises(RuntimeError, match="role turn ended"):
        # The HTTP capability has closed; the host service also rejects a late
        # dispatch that was admitted just before bridge teardown.
        second.validation.run({"argv": ["/bin/true"]})


@pytest.mark.parametrize("exception_type", [RuntimeError, ValueError, KeyError, TypeError])
def test_unexpected_internal_faults_remain_generic_and_persistent(tmp_path, monkeypatch, exception_type):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/synthetic/docker")

    def fail(*_args, **_kwargs):
        raise exception_type("unexpected-private-internal-detail")

    monkeypatch.setattr("argus.reviewer.validation.run_process", fail)
    with pytest.raises(RuntimeError, match="Role commands failed:") as teardown:
        with review_action_tools(
            SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
            venue="", venue_required=False,
        ) as (actions, options):
            with pytest.raises(ValueError, match="^role tool request failed$"):
                bridge_request(PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env)
            paths = list(actions.validation.output_root.glob("*/result.json"))
            assert len(paths) == 1
            receipt = json.loads(paths[0].read_text())
            assert receipt["status"] == "execution_error"
            assert receipt["error"] == "role tool request failed"
            assert "unexpected-private" not in paths[0].read_text()
            assert actions.decision is None
    assert "unexpected-private" not in str(teardown.value)


def test_expected_docker_diagnostics_are_redacted_before_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/synthetic/docker")
    monkeypatch.setattr(
        "argus.reviewer.validation.run_process",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, "", "Cannot connect through http://synthetic:private-diagnostic@proxy.invalid\n",
        ),
    )
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env)
        assert result["status"] == "environment_error"
        assert "Docker image inspect" in result["error"]
        assert "<REDACTED:creds>" in result["error"]
        assert "private-diagnostic" not in json.dumps(result)
        assert "private-diagnostic" not in Path(result["receipt_path"]).read_text()
        assert actions.decision is None


@pytest.mark.parametrize("stage", ["context", "image", "create"])
def test_preflight_deadlines_are_environment_results_with_receipts(tmp_path, monkeypatch, stage):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/synthetic/docker")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if stage in argv:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        output = "unix:///synthetic/docker.sock\n" if "context" in argv else "sha256:" + "a" * 64
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr("argus.reviewer.validation.run_process", run)
    validator = ReviewValidation(str(tmp_path), [], "ubuntu:24.04")
    try:
        result = validator.run({"argv": ["/bin/true"]})
        assert result["status"] == "environment_error"
        assert stage in result["error"] and "10 seconds" in result["error"]
        assert json.loads(Path(result["receipt_path"]).read_text())["error"] == result["error"]
        assert not any("start" in command for command in calls)
    finally:
        if stage == "create":
            assert result["cleanup_status"] == "failed"
            assert "create was not confirmed" in result["cleanup_error"]
            with pytest.raises(RuntimeError, match="cleanup needs attention"):
                validator.close()
        else:
            validator.close()


@pytest.mark.parametrize("stage", ["context", "image", "create"])
def test_closure_during_preflight_cannot_start_a_late_command(tmp_path, monkeypatch, stage):
    started, release = threading.Event(), threading.Event()
    calls = []
    cancellations = []
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/synthetic/docker")

    def run(argv, **kwargs):
        if kwargs.get("cancelled") is not None and kwargs["cancelled"].is_set():
            raise ExecutionCancelled
        calls.append(argv)
        if kwargs.get("cancelled") is not None:
            cancellations.append(kwargs["cancelled"])
        if stage in argv:
            started.set()
            if stage == "create":
                assert kwargs.get("cancelled") is None
                assert release.wait(3)
            else:
                assert kwargs["cancelled"].wait(3)
                raise ExecutionCancelled
        output = "unix:///synthetic/docker.sock\n" if "context" in argv else "sha256:" + "a" * 64
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr("argus.reviewer.validation.run_process", run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        with review_action_tools(
            SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
            venue="", venue_required=False,
        ) as (actions, options):
            future = executor.submit(
                bridge_request, PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env,
            )
            assert started.wait(3)
            closer = executor.submit(actions.validation.close)
            assert cancellations[0].wait(3)
            release.set()
            closer.result(3)
        result = future.result(3)
    assert result["status"] == "cancelled"
    assert not any("start" in command for command in calls)
    assert any("rm" in command for command in calls) == (stage == "create")
    assert json.loads(Path(result["receipt_path"]).read_text())["status"] == "cancelled"


@pytest.mark.parametrize("failure", ["exit", "timeout", "internal"])
def test_cleanup_failures_are_bounded_persistent_and_do_not_erase_timeout(tmp_path, monkeypatch, failure):
    docker = tmp_path / "synthetic-docker"
    pid_path = tmp_path / "cleanup.pid"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "args = sys.argv[1:]\n"
        "if 'image' in args:\n"
        "    print('sha256:' + 'a' * 64)\n"
        "elif 'create' in args:\n"
        "    print('synthetic-container')\n"
        "elif 'start' in args:\n"
        "    time.sleep(60)\n"
        "elif 'rm' in args:\n"
        f"    open({str(pid_path)!r}, 'w').write(str(os.getpid()))\n"
        + ("    time.sleep(60)\n" if failure == "timeout" else
           "    print('synthetic daemon cleanup failure', file=sys.stderr)\n    sys.exit(1)\n")
    )
    docker.chmod(0o700)
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: str(docker))
    if failure == "internal":
        from argus.core.call_bound_execution import run_process

        def cleanup_fault(argv, **kwargs):
            if "rm" in argv:
                raise ValueError("private-cleanup-internal-detail")
            return run_process(argv, **kwargs)

        monkeypatch.setattr("argus.reviewer.validation.run_process", cleanup_fault)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="cleanup needs attention"):
        with review_action_tools(
            SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
            venue="", venue_required=False,
        ) as (actions, options):
            if failure == "internal":
                with pytest.raises(ValueError, match="^role tool request failed$"):
                    bridge_request(PREFIX, COMMAND_TOOL, {
                        "argv": ["/bin/true"], "timeout_seconds": 0.2,
                    }, env=options.extension_env)
                result = json.loads(next(actions.validation.output_root.glob("*/result.json")).read_text())
            else:
                result = bridge_request(PREFIX, COMMAND_TOOL, {
                    "argv": ["/bin/true"], "timeout_seconds": 0.2,
                }, env=options.extension_env)
                if result["status"] == "running":
                    result = bridge_request(PREFIX, RESULT_TOOL, {
                        "command_id": result["command_id"],
                    }, env=options.extension_env)
            assert result["status"] == "timed_out"
            assert result["exit_code"] is None
            assert result["cleanup_status"] == "failed"
            assert "private-cleanup" not in json.dumps(result)
            assert json.loads(Path(result["receipt_path"]).read_text())["cleanup_status"] == "failed"
            assert actions.decision is None
    assert time.monotonic() - started < CLEANUP_SECONDS + 4
    if pid_path.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_path.read_text()), 0)


@pytest.fixture
def synthetic_docker_client(tmp_path, monkeypatch):
    context = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout.strip()
    endpoint = context if os.environ.get("DOCKER_CONTEXT") else os.environ.get("DOCKER_HOST") or context
    assert endpoint.startswith("unix://"), "This check requires the selected local Docker daemon."
    config = tmp_path / "synthetic-docker-client"
    config.mkdir()
    proxy = "http://argus-synthetic:proxy-fixture-only@proxy.invalid:3128"
    (config / "config.json").write_text(json.dumps({
        "proxies": {"default": {
            "httpProxy": proxy, "httpsProxy": proxy, "allProxy": proxy,
            "ftpProxy": proxy, "noProxy": "synthetic-only.invalid",
        }},
    }))
    monkeypatch.setenv("DOCKER_CONFIG", str(config))
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    return ["docker", "--config", str(config), "--host", endpoint]


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
def test_real_bridge_does_not_inject_configured_proxy_credentials(tmp_path, monkeypatch, synthetic_docker_client):
    (tmp_path / "project").mkdir()
    control = subprocess.run(
        [*synthetic_docker_client, "run", "--rm", "--pull", "never", "--network", "none", "ubuntu:24.04",
         "/bin/sh", "-c", 'test -n "$HTTP_PROXY" && printf synthetic-proxy-present'],
        capture_output=True, text=True, check=True, timeout=20,
    )
    assert control.stdout == "synthetic-proxy-present"
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", str(tmp_path / "nonexistent-certificates"))
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic:ambient-proxy-only@proxy.invalid")
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    with review_action_tools(
        SimpleNamespace(backend="copilot"),
        RunnerOptions(sandbox_mode="read-only", working_dir=str(tmp_path / "project")),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {
            "argv": ["/bin/sh", "-c",
                     'for key in HTTP_PROXY HTTPS_PROXY ALL_PROXY FTP_PROXY NO_PROXY '
                     'http_proxy https_proxy all_proxy ftp_proxy no_proxy; do '
                     'if printenv "$key" >/dev/null; then printf "unexpected proxy variable"; exit 91; fi; done'],
            "timeout_seconds": 20,
        }, env=options.extension_env)
        result = wait_for_command(options, result)
        assert result["status"] == "completed", result
        assert result["exit_code"] == 0, result
        assert actions.decision is None


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
def test_real_selected_context_survives_client_config_isolation(tmp_path, monkeypatch, synthetic_docker_client):
    subprocess.run([
        *synthetic_docker_client[:3], "context", "create", "selected-local",
        "--docker", f"host={synthetic_docker_client[-1]}",
    ], capture_output=True, text=True, check=True, timeout=10)
    monkeypatch.setenv("DOCKER_CONTEXT", "selected-local")
    monkeypatch.setenv("DOCKER_HOST", "unix:///must-not-replace-the-selected-context.sock")
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env)
        result = wait_for_command(options, result)
        assert result["status"] == "completed", result
        assert result["exit_code"] == 0, result
        assert result["docker_endpoint"] == synthetic_docker_client[-1]
        assert actions.decision is None


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
@pytest.mark.parametrize("failure", ["image", "daemon", "context"])
def test_real_environment_diagnostics_through_the_bridge(tmp_path, monkeypatch, synthetic_docker_client, failure):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    expected = "Docker image inspect"
    if failure == "image":
        monkeypatch.setenv(IMAGE_ENV, "argus-review-synthetic-missing-image:never-pull")
    elif failure == "daemon":
        # Nested pytest paths can exceed Linux's Unix-socket address limit.
        socket_path = Path(__file__).resolve().parents[1] / ".missing-docker.sock"
        assert len(os.fsencode(socket_path)) < 108
        assert not os.path.lexists(socket_path)
        monkeypatch.setenv("DOCKER_HOST", f"unix://{socket_path}")
    else:
        monkeypatch.setenv("DOCKER_CONTEXT", "nonexistent-synthetic-context")
        expected = "Docker context inspect"
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env)
        result = wait_for_command(options, result)
        assert result["status"] == "environment_error", result
        assert expected in result["error"]
        assert "role tool request failed" not in result["error"]
        if failure == "daemon":
            assert result["docker_endpoint"] == f"unix://{socket_path}"
            assert f"dial unix {socket_path}: connect: no such file or directory" in result["error"]
            assert "is too long" not in result["error"]
            assert not os.path.lexists(socket_path)
        assert result["exit_code"] is None
        assert json.loads(Path(result["receipt_path"]).read_text())["error"] == result["error"]
        assert actions.decision is None


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_real_command_exit_status_and_output_cannot_submit_a_decision(tmp_path, monkeypatch, exit_code):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {
            "argv": ["/bin/sh", "-c", f"printf 'STATUS=done'; printf diagnostic >&2; exit {exit_code}"],
        }, env=options.extension_env)
        result = wait_for_command(options, result)
        assert result["status"] == "completed", result
        assert result["exit_code"] == exit_code, result
        assert result["stdout"] == "STATUS=done"
        assert result["stderr"] == "diagnostic"
        assert result["cleanup_status"] == "removed"
        assert actions.decision is None


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_real_timeout_and_explicit_cancellation_remove_the_container(tmp_path, monkeypatch, synthetic_docker_client, mode):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {
            "argv": ["/bin/sh", "-c", "printf started; sleep 60"],
            "timeout_seconds": 2 if mode == "timeout" else 0,
        }, env=options.extension_env)
        if mode == "cancel":
            deadline = time.monotonic() + 30
            while result["status"] == "running" and not result["stdout"] and time.monotonic() < deadline:
                result = bridge_request(PREFIX, RESULT_TOOL, {
                    "command_id": result["command_id"],
                }, env=options.extension_env)
            assert result["status"] == "running"
            assert result["exit_code"] is None
            assert result["stdout"] == "started"
            result = bridge_request(PREFIX, CANCEL_TOOL, {
                "command_id": result["command_id"],
            }, env=options.extension_env)
        result = wait_for_command(options, result)
        assert result["status"] == ("timed_out" if mode == "timeout" else "cancelled"), result
        assert result["cleanup_status"] == "removed", result
        assert result["stdout"] in {"", "started"}
        assert result["exit_code"] is None
        inspect = subprocess.run(
            [*synthetic_docker_client, "container", "inspect", result["container"]],
            capture_output=True, text=True, timeout=10, check=False,
        )
        assert inspect.returncode != 0 and "No such" in inspect.stderr
        assert actions.decision is None


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
def test_real_bridge_closure_cancels_active_validation(tmp_path, monkeypatch, synthetic_docker_client):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    receipt_path = None
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            with review_action_tools(
                SimpleNamespace(backend="copilot"),
                RunnerOptions(sandbox_mode="read-only", working_dir=str(project)),
                venue="", venue_required=False,
            ) as (actions, options):
                future = executor.submit(
                    bridge_request, PREFIX, COMMAND_TOOL,
                    {"argv": ["/bin/sh", "-c", "touch '{scratch}/started'; sleep 60"]},
                    env=options.extension_env,
                )
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    receipts = list(actions.validation.output_root.glob("*/result.json"))
                    if receipts and (receipts[0].parent / "scratch" / "started").exists():
                        receipt_path = receipts[0]
                        break
                    time.sleep(0.02)
                assert receipt_path is not None, "The real Docker command did not start."
                closing_at = time.monotonic()
            result = json.loads(receipt_path.read_text())
            assert time.monotonic() - closing_at < CLOSE_SECONDS + 1
            assert result["status"] == "cancelled", result
            assert result["cleanup_status"] == "removed", result
            inspect = subprocess.run(
                [*synthetic_docker_client, "container", "inspect", result["container"]],
                capture_output=True, text=True, timeout=10, check=False,
            )
            assert inspect.returncode != 0 and "No such" in inspect.stderr
            assert actions.decision is None
        finally:
            if receipt_path is not None:
                receipt = json.loads(receipt_path.read_text())
                subprocess.run(
                    [*synthetic_docker_client, "rm", "--force", receipt["container"]],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                future.result(timeout=10)


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
def test_real_sandbox_keeps_sources_readonly_and_allows_scratch(tmp_path, monkeypatch):
    project = tmp_path / "project"
    source = tmp_path / "source"
    framework = tmp_path / "framework"
    project.mkdir()
    source.mkdir()
    framework.mkdir()
    original = project / "implementation.txt"
    authoritative = source / "input.txt"
    framework_input = framework / "checker.py"
    secret = tmp_path / "outside-secret.txt"
    original.write_text("original\n")
    authoritative.write_text("authoritative\n")
    framework_input.write_text("framework\n")
    original_modes = [path.stat().st_mode for path in (original, authoritative, framework_input)]
    secret.write_text("must-not-be-visible\n")
    monkeypatch.setenv("ARGUS_PLUGIN_REVIEW_TOKEN", "must-not-leak")
    script = (
        f"cat {shlex.quote(str(original))} {shlex.quote(str(authoritative))}; "
        f"if printf changed > {shlex.quote(str(original))}; then exit 91; fi; "
        f"if chmod 777 {shlex.quote(str(original))}; then exit 92; fi; "
        f"if printf changed > {shlex.quote(str(authoritative))}; then exit 93; fi; "
        f"if printf changed > {shlex.quote(str(framework_input))}; then exit 96; fi; "
        f"test ! -e {shlex.quote(str(secret))} || exit 94; "
        'test -z \"$ARGUS_PLUGIN_REVIEW_TOKEN\" || exit 95; '
        "test ! -e /sys/class/net/eth0 || exit 97; "
        "test ! -S /var/run/docker.sock || exit 98; "
        "printf verified > '{scratch}/result.txt'"
    )

    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setenv(READ_DIRS_ENV, json.dumps([str(source), str(framework)]))
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(project)),
        venue="", venue_required=False,
    ) as (actions, options):
        result = bridge_request(
            PREFIX, COMMAND_TOOL, {"argv": ["/bin/sh", "-c", script], "timeout_seconds": 30},
            env=options.extension_env,
        )
        result = wait_for_command(options, result)
        assert actions.decision is None

    assert result["status"] == "completed", result
    assert result["exit_code"] == 0, result
    assert original.read_text() == "original\n"
    assert authoritative.read_text() == "authoritative\n"
    assert framework_input.read_text() == "framework\n"
    assert original_modes == [path.stat().st_mode for path in (original, authoritative, framework_input)]
    assert (Path(result["scratch"]) / "result.txt").read_text() == "verified"


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
@pytest.mark.parametrize("provider_raises", [False, True])
def test_evaluate_cancels_commands_on_normal_and_exceptional_turn_end(tmp_path, monkeypatch, provider_raises):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    observed = {}

    def review_turn(_prompt, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {
            "argv": ["/bin/sh", "-c", "printf started; sleep 60"],
        }, env=options.extension_env)
        assert result["status"] == "running", result
        observed.update(result)
        if provider_raises:
            raise RuntimeError("Synthetic provider interruption")
        return "STATUS=done\nThis printed footer cannot approve the cancelled command."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message_factory=review_turn))
    decision = Reviewer(backend).evaluate(
        objective="Review the current candidate", round_index=1, session_id=None,
        main_summary="A candidate exists.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )
    assert decision.status == "blocked"
    assert decision.backend_unavailable
    receipt = json.loads(Path(observed["receipt_path"]).read_text())
    assert receipt["status"] == "cancelled"
    assert receipt["cleanup_status"] == "removed"
    assert receipt["exit_code"] is None


def test_cleanup_failure_invalidates_an_ended_turn_not_the_proof(tmp_path, monkeypatch):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/synthetic/docker")

    def run(argv, **_kwargs):
        if "rm" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "synthetic daemon cleanup failure")
        return subprocess.CompletedProcess(argv, 0, "sha256:" + "a" * 64, "")

    monkeypatch.setattr("argus.reviewer.validation.run_process", run)
    observed = {}

    def review_turn(_prompt, options):
        result = bridge_request(PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env)
        observed.update(result)
        return "Native action already submitted."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message_factory=review_turn,
        review_action=("approve_review", {"review": "Synthetic native action for the lifecycle test."}),
    ))
    decision = Reviewer(backend).evaluate(
        objective="Review the current candidate", round_index=1, session_id=None,
        main_summary="A candidate exists.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )
    assert observed["status"] == "completed" and observed["exit_code"] == 0
    assert observed["cleanup_status"] == "failed"
    assert decision.status == "blocked" and decision.backend_unavailable
    assert "cleanup needs attention" in decision.reason


@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["receipt-only", "receipt-and-removal"])
@pytest.mark.parametrize("execution", ["completed", "timed_out", "cancelled"])
def test_terminal_receipt_failure_invalidates_an_earlier_approval(
    tmp_path, monkeypatch, cleanup_fails, execution,
):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/synthetic/docker")
    monkeypatch.setattr("argus.reviewer.validation.WAIT_SECONDS", 0.01)
    started = threading.Event()
    observed = {}

    def run(argv, **kwargs):
        if "start" in argv:
            started.set()
            if execution == "cancelled":
                assert kwargs["cancelled"].wait(3)
                raise ExecutionCancelled
            if execution == "timed_out":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if "rm" in argv and cleanup_fails:
            return subprocess.CompletedProcess(argv, 1, "", "synthetic daemon cleanup failure")
        return subprocess.CompletedProcess(argv, 0, "sha256:" + "a" * 64, "")

    replace = Path.replace

    def fail_terminal_replace(path, target):
        if path.name == "result.tmp":
            receipt = json.loads(path.read_text())
            if receipt["status"] != "running":
                observed["terminal"] = receipt
                raise OSError(errno.ENOSPC, "synthetic receipt storage exhausted", str(target))
        return replace(path, target)

    monkeypatch.setattr("argus.reviewer.validation.run_process", run)
    monkeypatch.setattr(Path, "replace", fail_terminal_replace)

    def review_turn(_prompt, options):
        observed["approval"] = bridge_request(
            PREFIX, "approve_review", {"review": "Synthetic approval before storage failure."},
            env=options.extension_env,
        )
        payload = {"argv": ["/bin/true"], "timeout_seconds": 1}
        if execution == "cancelled":
            observed["running"] = bridge_request(
                PREFIX, COMMAND_TOOL, payload, env=options.extension_env,
            )
            observed["started"] = started.wait(3)
        else:
            with pytest.raises(ValueError) as failure:
                result = bridge_request(PREFIX, COMMAND_TOOL, payload, env=options.extension_env)
                wait_for_command(options, result)
            observed["tool_error"] = str(failure.value)
        return "The earlier native action must not survive the storage failure."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message_factory=review_turn))
    decision = Reviewer(backend).evaluate(
        objective="Review the current candidate", round_index=1, session_id=None,
        main_summary="A candidate exists.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert observed["approval"] == {"recorded": "approve_review"}
    terminal = observed["terminal"]
    assert terminal["status"] == execution
    assert terminal["exit_code"] == (0 if execution == "completed" else None)
    assert terminal["cleanup_status"] == ("failed" if cleanup_fails else "removed")
    if cleanup_fails:
        assert "synthetic daemon cleanup failure" in terminal["cleanup_error"]
    persisted = json.loads(Path(terminal["receipt_path"]).read_text())
    assert persisted["status"] == "running"
    assert "cleanup_status" not in persisted
    if execution == "cancelled":
        assert observed["started"]
        assert observed["running"]["status"] == "running"
    assert decision.status == "blocked" and decision.backend_unavailable, decision
    assert "Role commands failed:" in decision.reason
    assert "Reviewer receipt persistence failed:" in decision.reason
    assert "synthetic receipt storage exhausted" in decision.reason
    if cleanup_fails:
        assert "cleanup needs attention" in decision.reason
        assert "synthetic daemon cleanup failure" in decision.reason
    if execution != "cancelled":
        assert observed["tool_error"] == "role tool request failed"


@pytest.mark.parametrize("write_stage", ["initial", "before_start"])
def test_receipt_write_failure_before_execution_invalidates_approval(
    tmp_path, monkeypatch, write_stage,
):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("argus.reviewer.validation.shutil.which", lambda _: "/synthetic/docker")
    calls = []
    attempts = []
    observed = {}

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "sha256:" + "a" * 64, "")

    write_text = Path.write_text

    def fail_one_write(path, data, *args, **kwargs):
        if path.name == "result.tmp":
            attempts.append(json.loads(data))
            if len(attempts) == (1 if write_stage == "initial" else 2):
                raise OSError(errno.ENOSPC, "synthetic receipt storage exhausted", str(path))
        return write_text(path, data, *args, **kwargs)

    monkeypatch.setattr("argus.reviewer.validation.run_process", run)
    monkeypatch.setattr(Path, "write_text", fail_one_write)

    def review_turn(_prompt, options):
        observed["approval"] = bridge_request(
            PREFIX, "approve_review", {"review": "Synthetic approval before storage failure."},
            env=options.extension_env,
        )
        try:
            observed["result"] = bridge_request(
                PREFIX, COMMAND_TOOL, {"argv": ["/bin/true"]}, env=options.extension_env,
            )
        except ValueError as exc:
            observed["tool_error"] = str(exc)
        return "The earlier native action must not survive the storage failure."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message_factory=review_turn))
    decision = Reviewer(backend).evaluate(
        objective="Review the current candidate", round_index=1, session_id=None,
        main_summary="A candidate exists.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert observed["approval"] == {"recorded": "approve_review"}
    assert not any("start" in command for command in calls)
    assert any("rm" in command for command in calls) == (write_stage == "before_start")
    assert decision.status == "blocked" and decision.backend_unavailable, decision
    assert "Reviewer receipt persistence failed:" in decision.reason
    assert "synthetic receipt storage exhausted" in decision.reason
    assert observed["tool_error"] == "role tool request failed"
    receipt_path = Path(attempts[0]["receipt_path"])
    if write_stage == "initial":
        assert len(attempts) == 1
        assert not receipt_path.exists()
        assert not calls
    else:
        receipt = json.loads(receipt_path.read_text())
        assert len(attempts) == 3
        assert receipt["status"] == "execution_error"
        assert receipt["cleanup_status"] == "removed"
        assert "synthetic receipt storage exhausted" in receipt["receipt_error"]


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ARGUS_TEST_DOCKER_REVIEW") != "1", reason="explicit local Docker probe")
def test_real_long_command_survives_the_old_transport_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv(IMAGE_ENV, "ubuntu:24.04")
    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(working_dir=str(tmp_path)),
        venue="", venue_required=False,
    ) as (actions, options):
        started = time.monotonic()
        result = bridge_request(PREFIX, COMMAND_TOOL, {
            "argv": ["/bin/sh", "-c", "sleep 132; printf long-check-complete"],
            "timeout_seconds": 160,
        }, env=options.extension_env)
        assert time.monotonic() - started < 10
        assert result["status"] == "running"
        assert result["exit_code"] is None
        command_id = result["command_id"]
        deadline = started + 175
        while result["status"] == "running" and time.monotonic() < deadline:
            requested = time.monotonic()
            result = bridge_request(PREFIX, RESULT_TOOL, {"command_id": command_id}, env=options.extension_env)
            assert time.monotonic() - requested < 10
        assert time.monotonic() - started > 130
        assert result["status"] == "completed", result
        assert result["exit_code"] == 0
        assert result["stdout"] == "long-check-complete"
        assert result["cleanup_status"] == "removed"
        assert json.loads(Path(result["receipt_path"]).read_text())["status"] == "completed"
        assert actions.decision is None
