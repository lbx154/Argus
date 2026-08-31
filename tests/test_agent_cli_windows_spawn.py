"""Non-interactive Agent CLI processes must not flash a Windows console."""
from __future__ import annotations

import os
import subprocess

from argus_skill.agent_cli._process_control import (
    background_subprocess_kwargs,
    windows_hidden_subprocess_kwargs,
)


def test_background_agent_spawn_hides_windows_console_and_keeps_posix_session() -> None:
    options = background_subprocess_kwargs()
    windows_options = windows_hidden_subprocess_kwargs()

    if os.name == "nt":
        for candidate in (options, windows_options):
            assert candidate["creationflags"] & subprocess.CREATE_NO_WINDOW
            startup = candidate.get("startupinfo")
            assert startup is not None
            assert startup.dwFlags & subprocess.STARTF_USESHOWWINDOW
            assert startup.wShowWindow == subprocess.SW_HIDE
        assert "start_new_session" not in options
    else:
        assert options == {"start_new_session": True}
        assert windows_options == {}
