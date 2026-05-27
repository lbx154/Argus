from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tarfile
import time
import venv
import zipfile
from pathlib import Path

import pytest


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def _run_input(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    input: str,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=input,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _clean_git_config_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    for key in list(env):
        if (
            key == "GIT_CONFIG_COUNT"
            or key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(key, None)
    return env


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_cli(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "argus-skill.exe"
    return venv_dir / "bin" / "argus-skill"


def _artifact_contains(wheel: Path, member_suffix: str) -> bool:
    with zipfile.ZipFile(wheel) as zf:
        return any(name.endswith(member_suffix) for name in zf.namelist())


def _wheel_metadata_lines(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        metadata_name = next(name for name in zf.namelist() if name.endswith("METADATA"))
        return zf.read(metadata_name).decode("utf-8").splitlines()


def _sdist_contains(sdist: Path, member_suffix: str) -> bool:
    with tarfile.open(sdist, "r:gz") as tf:
        return any(member.name.endswith(member_suffix) for member in tf.getmembers())


def _read_pid(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _discover_project_root(life_root: Path, *, marker: str | None = None, timeout: float = 10.0) -> Path:
    projects_dir = life_root / "projects"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if projects_dir.exists():
            if marker is None:
                roots = sorted(path for path in projects_dir.iterdir() if path.is_dir())
            else:
                roots = sorted(path.parent for path in projects_dir.glob(f"*/{marker}"))
            if roots:
                return roots[0]
        time.sleep(0.05)
    suffix = f" containing {marker!r}" if marker else ""
    raise AssertionError(f"timed out waiting for project root{suffix}")


def _project_root_from_output(output: str) -> Path:
    for line in output.splitlines():
        if "→" not in line:
            continue
        path_text = line.rsplit("→", 1)[1].strip()
        if path_text:
            return Path(path_text).parent
    raise AssertionError("could not find project root path in CLI output")


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for daemon lifecycle condition")


def _terminate_process(proc: subprocess.Popen[str]) -> tuple[str, str]:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    try:
        return proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.communicate(timeout=10)


def test_built_artifacts_and_installed_cli_contract_from_sdist(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "dist"
    _run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir)],
        cwd=repo,
    )

    wheel = next(outdir.glob("*.whl"))
    sdist = next(outdir.glob("*.tar.gz"))
    assert "Requires-Dist: rich>=13.7" in _wheel_metadata_lines(wheel)
    assert _artifact_contains(wheel, "argus_skill/skills/__init__.py")
    assert _artifact_contains(wheel, "argus_skill/skills/store.py")
    assert _artifact_contains(wheel, "argus_skill/skills/pipeline_policy.py")
    assert _artifact_contains(wheel, "argus_skill/tools/project_templates/code/generate_image_2.py")
    assert _artifact_contains(wheel, "argus_skill/builtin_skills/emnlp-paper-drafting.md")
    assert _artifact_contains(wheel, "argus_skill/builtin_skills/domains/agents-rag/langchain.md")
    assert _sdist_contains(sdist, "argus_skill/skills/__init__.py")
    assert _sdist_contains(sdist, "argus_skill/skills/store.py")
    assert _sdist_contains(sdist, "argus_skill/skills/pipeline_policy.py")
    assert _sdist_contains(sdist, "argus_skill/tools/project_templates/code/generate_image_2.py")
    assert _sdist_contains(sdist, "argus_skill/builtin_skills/emnlp-paper-drafting.md")
    assert _sdist_contains(sdist, "argus_skill/builtin_skills/domains/agents-rag/langchain.md")

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_python(venv_dir)
    _run([str(venv_python), "-m", "pip", "install", str(sdist)], cwd=tmp_path)

    cli = _venv_cli(venv_dir)
    runtime_repo = tmp_path / "runtime-repo"
    runtime_repo.mkdir()
    help_env = _clean_git_config_env(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "PYTHONUNBUFFERED": "1",
        }
    )

    help_run = _run([str(cli), "--help"], cwd=runtime_repo, env=help_env)
    assert help_run.returncode == 0
    assert "usage: argus-skill" in help_run.stdout
    assert help_run.stderr == ""

    life_dir = tmp_path / "life"
    status_run = _run(
        [str(cli), "--status", "--life-dir", str(life_dir)],
        cwd=runtime_repo,
        env=help_env,
    )
    assert status_run.returncode == 0, status_run
    status_output = status_run.stdout + status_run.stderr
    assert "Traceback" not in status_output
    assert "daemon   : not running" in status_run.stdout
    assert "active   : 0 pending" in status_run.stdout
    assert "inbox" in status_run.stdout

    demo_run = _run_input(
        [
            str(cli),
            "--no-daemon",
            "--life-dir",
            str(life_dir),
        ],
        cwd=runtime_repo,
        env=help_env,
        input="/status\n/exit\n",
        timeout=120,
    )
    assert demo_run.returncode == 0, demo_run
    demo_output = demo_run.stdout + demo_run.stderr
    assert "Traceback" not in demo_output
    assert "ArgusBot" not in demo_output
    assert "codex backend requested" not in demo_output
    assert "Install the codex extra" not in demo_output
    assert "continuous: off" in demo_run.stdout
    assert "daemon : not running" in demo_run.stdout
    assert "backlog : 0 pending" in demo_run.stdout
    assert "inbox   : 0 pending" in demo_run.stdout

    demo_project_root = _discover_project_root(life_dir)
    assert (life_dir / "identity.md").exists()
    assert (life_dir / "journal.jsonl").exists()
    assert (demo_project_root / "project.md").exists()
    assert (demo_project_root / "memory.jsonl").exists()
    assert (demo_project_root / "backlog.jsonl").exists()
    assert not (demo_project_root / "events.jsonl").exists()


def test_installed_cli_daemon_lifecycle(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "dist"
    _run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir)],
        cwd=repo,
    )

    wheel = next(outdir.glob("*.whl"))
    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_python(venv_dir)
    _run([str(venv_python), "-m", "pip", "install", str(wheel)], cwd=tmp_path)

    cli = _venv_cli(venv_dir)
    runtime_cwd = tmp_path / "runtime"
    runtime_cwd.mkdir()
    home_dir = tmp_path / "home"
    skills_dir = tmp_path / "skills"
    home_dir.mkdir()
    skills_dir.mkdir()
    (runtime_cwd / "README.md").write_text("packaging smoke\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "ARGUS_SKILL_SKILLS_DIR": str(skills_dir),
            "HOME": str(home_dir),
        }
    )

    life_dir = tmp_path / "life"
    project_root: Path | None = None
    pid_path: Path | None = None
    status_path: Path | None = None
    pid: int | None = None

    try:
        start = _run(
            [str(cli), "--daemon", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert start.returncode == 0, start

        project_root = _discover_project_root(life_dir, marker="daemon.pid")
        pid_path = project_root / "daemon.pid"
        status_path = project_root / "daemon.status.json"
        _wait_until(lambda: pid_path.exists() and status_path.exists())
        pid = _read_pid(pid_path)
        assert pid is not None and pid > 0
        assert _pid_is_alive(pid)

        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["pid"] == pid
        assert status["backend"] == "memory"
        assert status["life_dir"] == str(project_root)
        assert "started_at_iso" in status

        live_status = _run(
            [str(cli), "--status", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert live_status.returncode == 0, live_status
        assert f"pid {pid}" in live_status.stdout
        assert "backend memory" in live_status.stdout

        second = subprocess.run(
            [str(cli), "--daemon", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
            text=True,
            capture_output=True,
        )
        assert second.returncode == 2, second
        assert "already running" in second.stderr
        assert _read_pid(pid_path) == pid

        stop = _run(
            [str(cli), "--daemon-stop", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert stop.returncode == 0, stop

        _wait_until(lambda: not pid_path.exists() and not status_path.exists() and not _pid_is_alive(pid))

        final_status = _run(
            [str(cli), "--status", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert final_status.returncode == 0, final_status
        assert "daemon   : not running" in final_status.stdout
    finally:
        cleanup_pid = pid
        if cleanup_pid is None and pid_path is not None:
            cleanup_pid = _read_pid(pid_path)
        subprocess.run(
            [str(cli), "--daemon-stop", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if cleanup_pid is not None and _pid_is_alive(cleanup_pid):
            try:
                _wait_until(lambda: not _pid_is_alive(cleanup_pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(cleanup_pid, signal.SIGKILL)
                except OSError:
                    pass
                _wait_until(lambda: not _pid_is_alive(cleanup_pid), timeout=10.0)
        if pid_path is not None and status_path is not None and (pid_path.exists() or status_path.exists()):
            _wait_until(lambda: not pid_path.exists() and not status_path.exists(), timeout=10.0)


def test_installed_cli_watch_and_follow_smoke(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "dist"
    _run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir)],
        cwd=repo,
    )

    wheel = next(outdir.glob("*.whl"))
    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_python(venv_dir)
    _run([str(venv_python), "-m", "pip", "install", str(wheel)], cwd=tmp_path)

    cli = _venv_cli(venv_dir)
    runtime_repo = tmp_path / "runtime-repo"
    runtime_repo.mkdir()
    home_dir = tmp_path / "home"
    skills_dir = tmp_path / "skills"
    home_dir.mkdir()
    skills_dir.mkdir()

    env = _clean_git_config_env(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "ARGUS_SKILL_SKILLS_DIR": str(skills_dir),
            "HOME": str(home_dir),
            "PYTHONUNBUFFERED": "1",
        }
    )

    life_dir = tmp_path / "life"
    seed_run = _run(
        [
            str(cli),
            "--notify",
            "seed",
            "--life-dir",
            str(life_dir),
        ],
        cwd=runtime_repo,
        env=env,
    )
    assert seed_run.returncode == 0, seed_run
    project_root = _project_root_from_output(seed_run.stdout)
    events_path = project_root / "events.jsonl"
    events_path.touch(exist_ok=True)

    watch_output_path = tmp_path / "watch.log"
    with watch_output_path.open("w", encoding="utf-8") as watch_out:
        watch = subprocess.Popen(
            [str(cli), "--watch", "--life-dir", str(life_dir)],
            cwd=runtime_repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=watch_out,
            stderr=watch_out,
            text=True,
        )
        follow = subprocess.Popen(
            [str(cli), "--follow", "--life-dir", str(life_dir)],
            cwd=runtime_repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            _wait_until(
                lambda: watch.poll() is None
                and follow.poll() is None
                and watch_output_path.stat().st_size > 0
            )
            time.sleep(0.8)

            notify = _run(
                [
                    str(cli),
                    "--notify",
                    "watch smoke",
                    "--life-dir",
                    str(life_dir),
                ],
                cwd=runtime_repo,
                env=env,
            )
            assert notify.returncode == 0, notify
            assert "queued nudge" in notify.stdout

            time.sleep(1.5)
            assert watch.poll() is None
            assert follow.poll() is None

            watch.send_signal(signal.SIGTERM)
            watch.wait(timeout=10)
            follow_stdout, follow_stderr = _terminate_process(follow)
            follow_output = follow_stdout + follow_stderr

            assert "Traceback" not in follow_output
            assert "argus-skill: following" in follow_output
            assert "following" in follow_output
            assert "life.inbox.queued" in follow_output
            assert "cli.notify" in follow_output
        finally:
            _terminate_process(watch)
            _terminate_process(follow)

    watch_output = watch_output_path.read_text(encoding="utf-8")
    assert "Traceback" not in watch_output
    assert "argus-skill watch" in watch_output
    assert "life.inbox.queued" in watch_output
    assert "daemon" in watch_output
    assert "down" in watch_output


def test_installed_cli_codex_preflight_banner_without_codex_binary(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_python(venv_dir)
    install_env = _clean_git_config_env({"PYTHONUNBUFFERED": "1"})
    _run(
        [str(venv_python), "-m", "pip", "install", ".[codex]"],
        cwd=repo,
        env=install_env,
    )

    cli = _venv_cli(venv_dir)
    runtime_repo = tmp_path / "runtime-repo"
    runtime_repo.mkdir()
    home_dir = tmp_path / "home"
    skills_dir = tmp_path / "skills"
    no_codex_bin = tmp_path / "no-codex-bin"
    home_dir.mkdir()
    skills_dir.mkdir()
    no_codex_bin.mkdir()

    env = _clean_git_config_env(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "codex",
            "ARGUS_SKILL_SKILLS_DIR": str(skills_dir),
            "HOME": str(home_dir),
            "PATH": str(no_codex_bin),
            "PYTHONUNBUFFERED": "1",
        }
    )

    demo_run = _run_input(
        [
            str(cli),
            "--no-daemon",
            "--life-dir",
            str(tmp_path / "life"),
        ],
        cwd=runtime_repo,
        env=env,
        input="/exit\n",
        timeout=120,
    )
    assert demo_run.returncode == 0, demo_run
    demo_output = demo_run.stdout + demo_run.stderr
    assert "Traceback" not in demo_output
    assert "`codex` binary not found on PATH" in demo_output
    assert "set ARGUS_SKILL_RUNNER_BIN" in demo_output
    assert "Install the codex extra" not in demo_output
    assert "codex backend requested" not in demo_output


def test_module_cli_expands_shell_placeholders_for_runtime_roots(tmp_path: Path) -> None:
    runtime_cwd = tmp_path / "runtime"
    runtime_cwd.mkdir()
    (runtime_cwd / "README.md").write_text("placeholder smoke\n", encoding="utf-8")
    expanded_home = tmp_path / "expanded-home"
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    project_root: Path | None = None
    pid_path: Path | None = None
    status_path: Path | None = None

    env = os.environ.copy()
    env.update(
        {
            "ARGUS_SKILL_HOME": "$TMPDIR",
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "HOME": str(fake_home),
            "TMPDIR": str(expanded_home),
        }
    )

    notify = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--notify",
            "test",
            "--life-dir",
            "$TMPDIR",
        ],
        cwd=runtime_cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    project_root = _project_root_from_output(notify.stdout)
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    inbox = project_root / "inbox.jsonl"
    assert f"→ {inbox}" in notify.stdout
    assert inbox.exists()
    assert not (runtime_cwd / "$TMPDIR").exists()

    status = subprocess.run(
        [sys.executable, "-m", "argus_skill", "--status"],
        cwd=runtime_cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"global-root: {expanded_home}" in status.stdout
    assert f"  project  : {project_root}" in status.stdout

    daemon = subprocess.run(
        [sys.executable, "-m", "argus_skill", "--daemon"],
        cwd=runtime_cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert daemon.returncode == 0
    _wait_until(lambda: pid_path.exists() and status_path.exists())
    try:
        pid = _read_pid(pid_path)
        assert pid is not None and pid > 0
        live_status = subprocess.run(
            [sys.executable, "-m", "argus_skill", "--status"],
            cwd=runtime_cwd,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        assert f"global-root: {expanded_home}" in live_status.stdout
        assert f"pid {pid}" in live_status.stdout
        running_status = json.loads(status_path.read_text(encoding="utf-8"))
        assert running_status["life_dir"] == str(project_root)
    finally:
        stop = subprocess.run(
            [sys.executable, "-m", "argus_skill", "--daemon-stop"],
            cwd=runtime_cwd,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        assert stop.returncode == 0
        _wait_until(lambda: not pid_path.exists() and not status_path.exists())


@pytest.mark.parametrize(
    ("argv", "env_key"),
    [
        (["--notify", "test", "--life-dir", "$TMPDIR"], "ARGUS_SKILL_HOME"),
        (["--status"], "ARGUS_SKILL_HOME"),
        (["--daemon"], "ARGUS_SKILL_HOME"),
    ],
)
def test_module_cli_rejects_unresolved_shell_placeholders(
    tmp_path: Path,
    argv: list[str],
    env_key: str,
) -> None:
    runtime_cwd = tmp_path / "runtime"
    runtime_cwd.mkdir()
    env = os.environ.copy()
    env.pop("TMPDIR", None)
    env[env_key] = "$TMPDIR"
    env["HOME"] = str(tmp_path / "fake-home")

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill", *argv],
        cwd=runtime_cwd,
        env=env,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 2
    assert "unresolved placeholder" in proc.stderr
    assert not (runtime_cwd / "$TMPDIR").exists()
