"""Per-boot daemon log segmentation: identity stays per-PROJECT (one daemon per
life_dir), but each boot gets its OWN log file so consecutive runs never
interleave; a stable <life_dir>/daemon.log alias always exposes the live boot.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.daemon.life_worker import (
    _daemon_log_path,
    _new_boot_id,
    _point_active_daemon_log,
)


def test_new_boot_id_unique_and_timestamped():
    a, b = _new_boot_id(), _new_boot_id()
    assert a != b  # random suffix makes even same-second boots distinct
    assert a[0].isdigit() and "T" in a and "Z" in a  # UTC timestamp shape


def test_daemon_log_path_is_per_boot(tmp_path: Path):
    assert _daemon_log_path(tmp_path, None, "b1") == tmp_path / "daemons" / "boot-b1.log"
    # two boots -> two distinct files
    assert _daemon_log_path(tmp_path, None, "b1") != _daemon_log_path(tmp_path, None, "b2")
    # an explicit override (config.log_path) always wins
    override = tmp_path / "custom.log"
    assert _daemon_log_path(tmp_path, override, "b1") == override


def test_point_active_daemon_log_aliases_target(tmp_path: Path):
    target = _daemon_log_path(tmp_path, None, "b1")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("boot 1\n")

    _point_active_daemon_log(tmp_path, target)
    link = tmp_path / "daemon.log"
    if os.name == "nt":
        assert not link.is_symlink()
        assert link.samefile(target)
    else:
        assert link.is_symlink()
        assert link.resolve() == target.resolve()
    assert link.read_text() == "boot 1\n"  # tail daemon.log == live boot
    with target.open("a", encoding="utf-8") as handle:
        handle.write("still live\n")
    assert link.read_text() == "boot 1\nstill live\n"


def test_point_active_repoints_to_new_boot(tmp_path: Path):
    b1 = _daemon_log_path(tmp_path, None, "b1")
    b2 = _daemon_log_path(tmp_path, None, "b2")
    b1.parent.mkdir(parents=True, exist_ok=True)
    b1.write_text("one\n")
    b2.write_text("two\n")

    _point_active_daemon_log(tmp_path, b1)
    _point_active_daemon_log(tmp_path, b2)  # a restart repoints
    link = tmp_path / "daemon.log"
    if os.name == "nt":
        assert link.samefile(b2)
        assert not link.samefile(b1)
    else:
        assert link.resolve() == b2.resolve()
    assert link.read_text() == "two\n"


def test_point_active_preserves_legacy_regular_daemon_log(tmp_path: Path):
    # An upgraded install has a pre-existing REGULAR daemon.log — it must be kept,
    # not clobbered, and the symlink must still be created (no FileExistsError).
    legacy = tmp_path / "daemon.log"
    legacy.write_text("old plain log\n")
    target = _daemon_log_path(tmp_path, None, "b1")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("new\n")

    _point_active_daemon_log(tmp_path, target)
    if os.name == "nt":
        assert (tmp_path / "daemon.log").samefile(target)
    else:
        assert (tmp_path / "daemon.log").is_symlink()
        assert (tmp_path / "daemon.log").resolve() == target.resolve()
    assert (tmp_path / "daemon.log.pre-segment").read_text() == "old plain log\n"


def test_redirect_std_to_log_captures_stdout_and_stderr(tmp_path: Path):
    # Real dup2 test in a FRESH subprocess (so it never clobbers the test
    # runner's fds): both stdout and codex-style stderr must land in the file.
    import subprocess
    import sys
    import textwrap

    log = tmp_path / "boot.log"
    code = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from argus_skill.daemon.life_worker import _redirect_std_to_log
        _redirect_std_to_log(Path({str(log)!r}), keep_console=False)
        sys.stdout.write("hello-stdout\\n"); sys.stdout.flush()
        sys.stderr.write("hello-stderr\\n"); sys.stderr.flush()
        """
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    content = log.read_text()
    assert "hello-stdout" in content
    assert "hello-stderr" in content


@pytest.mark.parametrize(
    ("encoding", "errors"),
    [("utf-8", "strict"), ("cp1252", "backslashreplace")],
)
def test_foreground_log_tee_preserves_console_encoding(tmp_path: Path, encoding, errors):
    """A fresh process tests real descriptor redirection without touching pytest's console."""
    import subprocess
    import sys
    import textwrap

    message = "Argus progress: 正在运行 🧪"
    code = textwrap.dedent(
        f"""
        import logging
        import sys
        from pathlib import Path
        from argus_skill.daemon.config import LifeWorkerConfig
        from argus_skill.daemon.process import run_foreground_process

        sys.stderr.reconfigure(encoding={encoding!r}, errors={errors!r})
        root = Path({str(tmp_path)!r})
        class Worker:
            def run_forever(self):
                logger = logging.getLogger()
                for handler in logger.handlers:
                    handler.setFormatter(logging.Formatter('%(message)s'))
                logger.warning({message!r})
                return 0

        config = LifeWorkerConfig(
            life_dir=root / 'projects' / 'fixture', global_root=root,
            project_workdir=root, backend='memory',
        )
        raise SystemExit(run_foreground_process(config, worker_factory=lambda _config: Worker()))
        """
    )
    env = dict(os.environ, PYTHONUTF8="0", ARGUS_SKILL_HOME=str(tmp_path),
               ARGUS_SKILL_RUNNER_BACKEND="memory")
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", code],
        env=env, capture_output=True, check=True, timeout=20,
    )

    expected = message.encode(encoding, errors=errors).decode(encoding)
    assert result.stderr.decode(encoding).splitlines() == [expected]
    boot_log, = (tmp_path / "projects" / "fixture" / "daemons").glob("boot-*.log")
    assert "Logging error" not in boot_log.read_text(encoding=encoding)
