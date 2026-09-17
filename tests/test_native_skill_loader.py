from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COPILOT,
    BACKEND_OPENCODE,
    BACKEND_PI,
)
from argus.skills.role_library import role_skill_libraries
from argus.skills.store import SkillStore


@pytest.mark.parametrize(
    ("backend", "binary"),
    [
        (BACKEND_CODEX, "codex"),
        (BACKEND_CLAUDE, "claude"),
        (BACKEND_COPILOT, "copilot"),
        (BACKEND_OPENCODE, "opencode"),
        (BACKEND_PI, "pi"),
    ],
)
def test_supported_backends_use_native_loader_or_portable_path_fallback(
    backend: str,
    binary: str,
    tmp_path: Path,
) -> None:
    role_dir = tmp_path / "skills" / "engineer"
    role_dir.mkdir(parents=True)
    command = AgentCliRunner(agent_bin=binary, backend=backend)._build_command(
        resume_thread_id=None,
        options=RunnerOptions(skill_paths=[str(role_dir)]),
    )

    if backend == BACKEND_PI:
        assert command[command.index("--skill") + 1] == str(role_dir)
    else:
        assert "--skill" not in command

    libraries = role_skill_libraries(
        SkillStore(tmp_path / "skills"),
        role="engineer",
    )
    assert str((tmp_path / "skills").resolve()) in libraries.block
    assert "portable fallback" in libraries.block
