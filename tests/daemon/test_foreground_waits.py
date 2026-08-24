from __future__ import annotations

from argus_skill.daemon.foreground_waits import (
    ProcessInfo,
    foreground_wait_shells,
)


def _process(
    pid: int,
    ppid: int,
    age: float,
    *argv: str,
) -> ProcessInfo:
    return ProcessInfo(pid=pid, ppid=ppid, age_seconds=age, argv=argv)


def test_direct_long_sleep_is_interrupted_but_detached_or_script_sleep_is_not() -> None:
    processes = {
        10: _process(10, 1, 500, "argus"),
        20: _process(20, 10, 400, "pi"),
        30: _process(30, 20, 80, "/bin/bash", "-c", "sleep 110; date -u"),
        31: _process(31, 30, 75, "sleep", "110"),
        40: _process(40, 1, 80, "/bin/bash", "-c", "sleep 110"),
        41: _process(41, 40, 75, "sleep", "110"),
        50: _process(50, 20, 80, "/bin/bash", "run-test.sh"),
        51: _process(51, 50, 75, "sleep", "110"),
        60: _process(60, 20, 20, "/bin/bash", "-c", "sleep 110"),
        61: _process(61, 60, 15, "sleep", "110"),
    }

    waits = foreground_wait_shells(
        processes,
        root_pid=10,
        minimum_age_seconds=60,
    )

    assert [process.pid for process in waits] == [30]


def test_direct_pidfd_select_wait_is_interrupted() -> None:
    processes = {
        10: _process(10, 1, 500, "argus"),
        20: _process(20, 10, 400, "pi"),
        30: _process(
            30,
            20,
            80,
            "/bin/bash",
            "-c",
            "python3 - <<'PY'\nos.pidfd_open(1); select.select([], [], [], 900)\nPY",
        ),
        31: _process(31, 30, 75, "python3", "-"),
    }

    waits = foreground_wait_shells(
        processes,
        root_pid=10,
        minimum_age_seconds=60,
    )

    assert [process.pid for process in waits] == [30]


def test_direct_inotify_select_wait_is_interrupted() -> None:
    processes = {
        10: _process(10, 1, 500, "argus"),
        20: _process(20, 10, 400, "pi"),
        30: _process(
            30,
            20,
            80,
            "/bin/bash",
            "-c",
            "python3 - <<'PY'\ninotify_init1(); select.select([], [], [])\nPY",
        ),
        31: _process(31, 30, 75, "python3", "-"),
    }

    waits = foreground_wait_shells(
        processes,
        root_pid=10,
        minimum_age_seconds=60,
    )

    assert [process.pid for process in waits] == [30]


def test_direct_inotify_read_wait_is_interrupted() -> None:
    processes = {
        10: _process(10, 1, 500, "argus"),
        20: _process(20, 10, 400, "pi"),
        30: _process(
            30,
            20,
            80,
            "/bin/bash",
            "-c",
            "python3 - <<'PY'\ninotify_init1(); os.read(fd, 65536)\nPY",
        ),
        31: _process(31, 30, 75, "python3", "-"),
    }

    waits = foreground_wait_shells(
        processes,
        root_pid=10,
        minimum_age_seconds=60,
    )

    assert [process.pid for process in waits] == [30]


def test_direct_tail_pid_wait_is_interrupted() -> None:
    processes = {
        10: _process(10, 1, 500, "argus"),
        20: _process(20, 10, 400, "pi"),
        30: _process(
            30,
            20,
            80,
            "tail",
            "--pid=999",
            "-f",
            "/dev/null",
        ),
    }

    waits = foreground_wait_shells(
        processes,
        root_pid=10,
        minimum_age_seconds=60,
    )

    assert [process.pid for process in waits] == [30]


def test_direct_sleep_without_a_shell_parent_is_interrupted() -> None:
    processes = {
        10: _process(10, 1, 500, "argus"),
        20: _process(20, 10, 400, "pi"),
        30: _process(30, 20, 20, "sleep", "25"),
    }

    waits = foreground_wait_shells(
        processes,
        root_pid=10,
        minimum_age_seconds=15,
    )

    assert [process.pid for process in waits] == [30]
