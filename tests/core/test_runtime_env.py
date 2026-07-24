from __future__ import annotations

import os

from argus_skill.core.runtime_env import load_backend_runtime_env


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


def test_load_backend_runtime_env_rejects_writable_file(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env_file = runtime / "claude.env"
    env_file.write_text('export ANTHROPIC_API_KEY="key"\n', encoding="utf-8")
    os.chmod(env_file, 0o622)
    env: dict[str, str] = {}

    assert load_backend_runtime_env(env, root=tmp_path) == {}
    assert env == {}
