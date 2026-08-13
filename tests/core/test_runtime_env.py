from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.core.runtime_env import (
    configure_framework_python_env,
    load_backend_runtime_env,
)


def test_load_backend_runtime_env_is_allowlisted_and_non_overriding(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env_file = runtime / "claude.env"
    env_file.write_text(
        'export ANTHROPIC_BASE_URL="http://glm.test:40000"\n'
        'export ANTHROPIC_API_KEY="file-key"\n'
        'export PATH="/untrusted"\n',
        encoding="utf-8",
    )
    os.chmod(env_file, 0o600)
    env = {"ANTHROPIC_API_KEY": "existing-key"}

    loaded = load_backend_runtime_env(env, root=tmp_path)

    assert loaded == {"ANTHROPIC_BASE_URL": "http://glm.test:40000"}
    assert env["ANTHROPIC_API_KEY"] == "existing-key"
    assert "PATH" not in env


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows chmod does not expose POSIX group/world write bits",
)
def test_load_backend_runtime_env_rejects_writable_file(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env_file = runtime / "claude.env"
    env_file.write_text('export ANTHROPIC_API_KEY="key"\n', encoding="utf-8")
    os.chmod(env_file, 0o622)
    env: dict[str, str] = {}

    assert load_backend_runtime_env(env, root=tmp_path) == {}
    assert env == {}


def test_configure_framework_python_env_prepends_current_interpreter(tmp_path) -> None:
    interpreter = tmp_path / "framework-venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    system_bin = tmp_path / "system-bin"
    env = {"PATH": str(system_bin)}

    configured = configure_framework_python_env(env, executable=interpreter)

    assert configured is env
    assert env["ARGUS_SKILL_PYTHON"] == str(interpreter)
    assert env["PATH"].split(os.pathsep) == [str(interpreter.parent), str(system_bin)]
    if os.name == "nt":
        assert env["PYTHONUTF8"] == "1"
        assert env["PYTHONIOENCODING"] == "utf-8"


def test_configure_framework_python_env_preserves_explicit_runtime_and_deduplicates_path(
    tmp_path,
) -> None:
    explicit = tmp_path / "explicit-venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    fallback = tmp_path / "fallback-venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    runtime_bin = tmp_path / "runtime-bin"
    system_bin = tmp_path / "system-bin"
    env = {
        "ARGUS_SKILL_PYTHON": str(explicit),
        "ARGUS_SKILL_RUNTIME_BIN": str(runtime_bin),
        "PATH": os.pathsep.join(
            [str(system_bin), str(explicit.parent), str(runtime_bin), str(explicit.parent)]
        ),
    }

    configure_framework_python_env(env, executable=fallback)

    assert env["ARGUS_SKILL_PYTHON"] == str(explicit)
    assert env["PATH"].split(os.pathsep) == [
        str(runtime_bin),
        str(explicit.parent),
        str(system_bin),
    ]


def test_cli_entrypoint_normalizes_framework_python_before_argument_handling(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.apps.cli._core import main

    monkeypatch.delenv("ARGUS_SKILL_PYTHON", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RUNTIME_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "unrelated-python"))

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    framework_python = os.environ["ARGUS_SKILL_PYTHON"]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(
        Path(framework_python).resolve().parent
    )
