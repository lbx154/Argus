from __future__ import annotations

import os

import pytest

from argus.release_tools import build_release


def test_release_uses_the_platform_npm_launcher() -> None:
    expected = "npm.cmd" if os.name == "nt" else "npm"
    assert build_release.NPM_COMMAND == expected


def test_help_describes_the_build_without_running_it(monkeypatch, capsys) -> None:
    def forbidden(*_argv, **_kwargs):
        raise AssertionError("--help must not start a build")

    monkeypatch.setattr(build_release, "run", forbidden)
    with pytest.raises(SystemExit) as exc:
        build_release.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "build_release" in out
    assert "frontend/web/dist" in out


def test_unknown_option_is_rejected_without_running_a_build(monkeypatch, capsys) -> None:
    def forbidden(*_argv, **_kwargs):
        raise AssertionError("a usage error must not start a build")

    monkeypatch.setattr(build_release, "run", forbidden)
    with pytest.raises(SystemExit) as exc:
        build_release.main(["--clean"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_no_arguments_runs_the_full_pipeline(monkeypatch, capsys) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        build_release, "run", lambda *argv, **kwargs: commands.append(tuple(argv)),
    )
    assert build_release.main([]) == 0
    joined = [" ".join(command) for command in commands]
    assert any("argus.release_tools.generate_manifest" in command for command in joined)
    assert any(command.endswith("run build") for command in joined)
    assert any("argus.release_tools.check_artifacts" in command for command in joined)
    assert "release ready:" in capsys.readouterr().out
