from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings
from foundry.integrations.release_stager import (
    ReleaseStageError,
    ReleaseStager,
    hardened_git_environment,
    validate_repository,
)

SHA = "c22de7c581a5577a01c00ca7c1bd17df8de2ebc4"
OTHER_SHA = "455da6cb6156dc68b698277c87514e02529dfeec"
REPOSITORY = "https://github.com/lbx154/Argus.git"
REF = "refs/heads/main"


def test_hardened_git_environment_clears_all_host_git_injection() -> None:
    environment = hardened_git_environment(
        {
            "PATH": "keep-me",
            "GIT_DIR": "host-worktree",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "malicious-helper",
            "GIT_ASKPASS": "prompt.exe",
            "SSH_ASKPASS": "ssh-prompt.exe",
        }
    )

    assert environment["PATH"] == "keep-me"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"]
    assert environment["GCM_INTERACTIVE"] == "Never"
    assert "GIT_DIR" not in environment
    assert "GIT_CONFIG_COUNT" not in environment
    assert "GIT_CONFIG_KEY_0" not in environment
    assert "GIT_CONFIG_VALUE_0" not in environment
    assert "GIT_ASKPASS" not in environment
    assert "SSH_ASKPASS" not in environment


class FakeGitRunner:
    def __init__(self, *, remote_sha: str = SHA, fail_operation: str | None = None) -> None:
        self.remote_sha = remote_sha
        self.fail_operation = fail_operation
        self.calls: list[tuple[tuple[str, ...], dict]] = []

    def __call__(self, argv, **kwargs):
        args = tuple(argv)
        self.calls.append((args, kwargs))
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] > 0
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"

        if "ls-remote" in args:
            return subprocess.CompletedProcess(args, 0, f"{self.remote_sha}\t{REF}\n", "")
        operation = ""
        if "init" in args:
            operation = "init"
            (Path(args[-1]) / ".git").mkdir()
        elif "fetch" in args:
            operation = "fetch"
        elif "checkout" in args:
            operation = "checkout"
        elif "rev-parse" in args:
            operation = "rev-parse"
        elif "remote" in args:
            operation = "remote"
        if operation == self.fail_operation:
            return subprocess.CompletedProcess(args, 23, "", f"fake {operation} failure")
        stdout = self.remote_sha + "\n" if operation == "rev-parse" else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")


def _commands(runner: FakeGitRunner) -> list[tuple[str, ...]]:
    return [argv for argv, _ in runner.calls]


@pytest.mark.parametrize(
    "repository",
    [
        "https://github.com/lbx154/Argus.git",
        "ssh://git@github.com/lbx154/Argus.git",
        "git://github.com/lbx154/Argus.git",
        "git@github.com:lbx154/Argus.git",
    ],
)
def test_only_explicit_supported_network_remote_forms_are_accepted(repository: str) -> None:
    assert validate_repository(repository) == repository


def test_stage_is_content_addressed_detached_and_stage_only(tmp_path: Path) -> None:
    runner = FakeGitRunner()
    result = ReleaseStager(tmp_path, runner=runner, timeout=17).stage(
        REPOSITORY,
        ref=REF,
        expected_sha=SHA,
        confirm_isolated_stage=True,
    )

    stage_dir = tmp_path / "releases" / "staging" / SHA
    assert Path(result.stage_dir) == stage_dir.resolve()
    assert Path(result.source_dir) == (stage_dir / "source").resolve()
    assert result.reused is False
    manifest = json.loads((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["repository"] == REPOSITORY
    assert manifest["ref"] == REF
    assert manifest["sha"] == SHA
    assert manifest["status"] == "staged"
    assert manifest["checkout_mode"] == "detached_exact_sha"
    assert manifest["tests"]["status"] == "not_run"
    assert manifest["adoption"]["status"] == "not_adopted"
    assert manifest["daemon"]["status"] == "not_started"
    assert manifest["running_campaigns_mutated"] is False

    commands = _commands(runner)
    flat = "\n".join(" ".join(command) for command in commands).lower()
    assert commands[0] == ("git", "ls-remote", REPOSITORY, REF)
    assert " checkout " in flat and " --detach " in flat
    assert " pull " not in f" {flat} "
    assert " reset " not in f" {flat} "
    assert all(call[1]["shell"] is False for call in runner.calls)


def test_duplicate_stage_reuses_only_matching_complete_content(tmp_path: Path) -> None:
    runner = FakeGitRunner()
    stager = ReleaseStager(tmp_path, runner=runner)
    first = stager.stage(REPOSITORY, ref=REF, expected_sha=SHA, confirm_isolated_stage=True)
    mutations_after_first = [call for call in _commands(runner) if "ls-remote" not in call]
    second = stager.stage(REPOSITORY, ref=REF, expected_sha=SHA, confirm_isolated_stage=True)
    mutations_after_second = [call for call in _commands(runner) if "ls-remote" not in call]

    assert first.reused is False
    assert second.reused is True
    assert first.stage_dir == second.stage_dir
    assert mutations_after_second == mutations_after_first
    assert sum("ls-remote" in call for call in _commands(runner)) == 2


def test_sha_mismatch_is_rejected_before_stage_creation_and_audited(tmp_path: Path) -> None:
    runner = FakeGitRunner(remote_sha=OTHER_SHA)
    with pytest.raises(ReleaseStageError) as caught:
        ReleaseStager(tmp_path, runner=runner).stage(
            REPOSITORY,
            ref=REF,
            expected_sha=SHA,
            confirm_isolated_stage=True,
        )
    assert caught.value.code == "sha_mismatch"
    assert caught.value.http_status == 409
    assert _commands(runner) == [("git", "ls-remote", REPOSITORY, REF)]
    assert not (tmp_path / "releases" / "staging" / SHA).exists()
    attempts = list((tmp_path / "releases" / "attempts").glob("*.json"))
    assert len(attempts) == 1
    attempt = json.loads(attempts[0].read_text(encoding="utf-8"))
    assert attempt["status"] == "rejected"
    assert attempt["error"]["code"] == "sha_mismatch"


@pytest.mark.parametrize(
    ("repository", "ref", "sha", "confirm"),
    [
        ("file:///tmp/Argus", REF, SHA, True),
        ("C:/Argus", REF, SHA, True),
        ("../Argus", REF, SHA, True),
        ("https://user:secret@github.com/lbx154/Argus.git", REF, SHA, True),
        ("https://github.com/../../Argus.git", REF, SHA, True),
        (REPOSITORY, "refs/heads/../../outside", SHA, True),
        (REPOSITORY, "refs/heads/-upload-pack=evil", SHA, True),
        (REPOSITORY, REF, "../" + SHA[:-3], True),
        (REPOSITORY, REF, SHA, False),
    ],
)
def test_unsafe_inputs_and_missing_confirmation_never_call_git_or_create_stage(
    tmp_path: Path, repository: str, ref: str, sha: str, confirm: bool
) -> None:
    runner = FakeGitRunner()
    with pytest.raises(ReleaseStageError):
        ReleaseStager(tmp_path, runner=runner).stage(
            repository,
            ref=ref,
            expected_sha=sha,
            confirm_isolated_stage=confirm,
        )
    assert runner.calls == []
    assert not (tmp_path / "releases").exists()


def test_failed_fetch_preserves_manifest_and_diagnostics_without_cleanup(tmp_path: Path) -> None:
    runner = FakeGitRunner(fail_operation="fetch")
    with pytest.raises(ReleaseStageError) as caught:
        ReleaseStager(tmp_path, runner=runner).stage(
            REPOSITORY,
            ref=REF,
            expected_sha=SHA,
            confirm_isolated_stage=True,
        )
    assert caught.value.code == "git_command_failed"
    stage_dir = tmp_path / "releases" / "staging" / SHA
    manifest = json.loads((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    diagnostics = json.loads((stage_dir / "diagnostics.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["error"]["code"] == "git_command_failed"
    assert diagnostics["status"] == "failed"
    assert any(command["returncode"] == 23 for command in diagnostics["commands"])
    assert stage_dir.is_dir()


def test_existing_incomplete_or_other_origin_stage_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "releases" / "staging" / SHA
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(
        json.dumps({"status": "staged", "repository": "https://example.com/other.git", "ref": REF, "sha": SHA}),
        encoding="utf-8",
    )
    sentinel = target / "do-not-touch.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    runner = FakeGitRunner()
    with pytest.raises(ReleaseStageError) as caught:
        ReleaseStager(tmp_path, runner=runner).stage(
            REPOSITORY,
            ref=REF,
            expected_sha=SHA,
            confirm_isolated_stage=True,
        )
    assert caught.value.code == "stage_conflict"
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert _commands(runner) == [("git", "ls-remote", REPOSITORY, REF)]


@pytest.fixture()
def api_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "foundry.db",
        data_dir=tmp_path / "data",
        seed_data_dir=tmp_path / "missing-seeds",
        cors_origins=("http://localhost:5174",),
        poll_interval_seconds=0,
        auto_seed=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_release_stage_api_requires_explicit_confirmation_and_expected_sha(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_confirmation = api_client.post(
        "/api/releases/stage",
        json={"repository": REPOSITORY, "ref": REF, "expected_sha": SHA},
    )
    assert missing_confirmation.status_code == 422
    false_confirmation = api_client.post(
        "/api/releases/stage",
        json={
            "repository": REPOSITORY,
            "ref": REF,
            "expected_sha": SHA,
            "confirm_isolated_stage": False,
        },
    )
    assert false_confirmation.status_code == 422

    calls: list[dict] = []

    def fake_stage(repository: str, **kwargs):
        calls.append({"repository": repository, **kwargs})
        return {
            "repository": repository,
            "ref": kwargs["ref"],
            "sha": kwargs["expected_sha"],
            "status": "staged",
            "reused": False,
            "stage_dir": "isolated",
            "source_dir": "isolated/source",
            "manifest_path": "isolated/manifest.json",
            "attempt_id": "attempt-1",
            "manifest": {
                "tests": {"status": "not_run"},
                "adoption": {"status": "not_adopted"},
                "daemon": {"status": "not_started"},
            },
        }

    monkeypatch.setattr("foundry.api.stage_release", fake_stage)
    response = api_client.post(
        "/api/releases/stage",
        json={
            "repository": REPOSITORY,
            "ref": REF,
            "expected_sha": SHA,
            "confirm_isolated_stage": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["manifest"]["adoption"]["status"] == "not_adopted"
    assert calls[0]["confirm_isolated_stage"] is True
    assert calls[0]["data_dir"] == api_client.app.state.settings.data_dir


def test_release_inspect_atomically_refreshes_registry_and_preserves_stable_canary(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = api_client.app.state.settings.data_dir / "releases" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    original_stable = {"sha": OTHER_SHA, "approved_by": "human", "state": "stable"}
    original_canary = {"sha": "a" * 40, "state": "passed", "runs": 2}
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stable_sha": OTHER_SHA,
                "stable": original_stable,
                "canary_sha": "a" * 40,
                "canary": original_canary,
                "manual_note": "preserve me",
                "remote_sha": "b" * 40,
            }
        ),
        encoding="utf-8",
    )

    inspection = {
        "repository": REPOSITORY,
        "ref": REF,
        "remote_sha": SHA,
        "reported_sha": "d" * 40,
        "stable_sha": OTHER_SHA,
        "candidate_sha": SHA,
        "candidate_available": True,
        "checked_at": "2026-08-23T08:30:00+00:00",
        "status": "candidate",
        "error": None,
        "staging": "isolated_stage_available_confirmation_required",
        "canary": "not_run",
        "adoption": "human_approval_required_for_new_campaigns_only",
    }
    monkeypatch.setattr("foundry.api.inspect_release", lambda *args, **kwargs: dict(inspection))

    response = api_client.post(
        "/api/releases/inspect",
        json={"repository": REPOSITORY, "ref": REF},
    )
    assert response.status_code == 200
    assert response.json()["registry_persisted"] is True
    persisted = json.loads(registry_path.read_text(encoding="utf-8"))
    assert persisted["remote_sha"] == SHA
    assert persisted["candidate_sha"] == SHA
    assert persisted["status"] == "candidate"
    assert persisted["stable_sha"] == OTHER_SHA
    assert persisted["stable"] == original_stable
    assert persisted["canary_sha"] == "a" * 40
    assert persisted["canary"] == original_canary
    assert persisted["manual_note"] == "preserve me"
    assert persisted["last_inspection"]["remote_sha"] == SHA
    assert list(registry_path.parent.glob(".registry.json.*.tmp")) == []
    assert api_client.get("/api/releases").json()["registry"]["remote_sha"] == SHA


def test_release_inspect_defaults_to_official_microsoft_repository(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def fake_inspect(repository: str, **kwargs):
        seen["repository"] = repository
        return {
            "repository": repository,
            "ref": kwargs["ref"],
            "remote_sha": SHA,
            "reported_sha": None,
            "stable_sha": None,
            "candidate_sha": SHA,
            "candidate_available": True,
            "checked_at": "2026-08-23T08:30:00+00:00",
            "status": "candidate",
            "error": None,
        }

    monkeypatch.setattr("foundry.api.inspect_release", fake_inspect)
    response = api_client.post("/api/releases/inspect", json={})

    assert response.status_code == 200
    assert seen["repository"] == "https://github.com/microsoft/ArgusAgent.git"
    assert response.json()["repository"] == "https://github.com/microsoft/ArgusAgent.git"


def test_release_inspect_refuses_to_overwrite_invalid_existing_registry(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = api_client.app.state.settings.data_dir / "releases" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    invalid = "{stable_sha: do-not-destroy"
    registry_path.write_text(invalid, encoding="utf-8")
    monkeypatch.setattr(
        "foundry.api.inspect_release",
        lambda *args, **kwargs: {
            "repository": REPOSITORY,
            "ref": REF,
            "remote_sha": SHA,
            "reported_sha": None,
            "stable_sha": None,
            "candidate_sha": SHA,
            "candidate_available": True,
            "checked_at": "2026-08-23T08:30:00+00:00",
            "status": "candidate",
            "error": None,
        },
    )

    response = api_client.post(
        "/api/releases/inspect",
        json={"repository": REPOSITORY, "ref": REF},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_registry"
    assert registry_path.read_text(encoding="utf-8") == invalid
