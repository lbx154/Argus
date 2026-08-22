"""A long experiment has to be watchable while it is still running.

Background experiments write stdout to a file, not a tty, so CPython
block-buffers it. One campaign held four GPUs at 99.9% CPU for five hours
behind a 0-byte stdout.log: indistinguishable from a hang, unreachable by the
operator, and a crash would have destroyed the run with no record of how far it
had got. Both spawn paths must therefore hand the child an unbuffered stdout.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from argus_skill.tools.subagent._registry import _child_env


def test_child_env_unbuffers_stdout() -> None:
    assert _child_env().get("PYTHONUNBUFFERED") == "1"


def test_unbuffering_survives_the_quiet_logs_opt_out(monkeypatch) -> None:
    """Verbosity and observability are different choices."""
    monkeypatch.setenv("ARGUS_SUBAGENT_QUIET_LOGS", "0")
    assert _child_env().get("PYTHONUNBUFFERED") == "1"


def test_an_explicit_operator_choice_still_wins(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONUNBUFFERED", "0")
    assert _child_env().get("PYTHONUNBUFFERED") == "0"


def test_progress_reaches_the_log_before_the_process_exits(tmp_path) -> None:
    """The end-to-end property: a running child's output is readable."""
    script = tmp_path / "slow.py"
    script.write_text(
        textwrap.dedent(
            """
            import time
            print("step 1")
            time.sleep(30)
            """
        ).strip(),
        encoding="utf-8",
    )
    log = tmp_path / "stdout.log"
    env = {**os.environ, **_child_env()}
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, str(script)], stdout=out, env=env)
        try:
            deadline = 10.0
            waited = 0.0
            while waited < deadline and "step 1" not in log.read_text(encoding="utf-8"):
                import time as _time

                _time.sleep(0.2)
                waited += 0.2
            assert "step 1" in log.read_text(encoding="utf-8"), (
                "a live child's progress never reached the log"
            )
        finally:
            proc.kill()
            proc.wait(timeout=10)
