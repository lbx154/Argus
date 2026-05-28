from __future__ import annotations

import os
import shutil
import sys
from typing import Sequence, TextIO

_BANNER_LARGE_LINES: tuple[str, ...] = (
    "╔═════════════════════════════════════════════════════════════════════════════════╗",
    "║   $$$$$$\\                                          $$$$$$$\\         $$$$$$$$\\   ║",
    "║  $$  __$$\\                                         $$  __$$\\        \\__$$  __|  ║",
    "║  $$ /  $$ | $$$$$$\\   $$$$$$\\  $$\\   $$\\  $$$$$$$\\ $$ |  $$ | $$$$$$\\  $$ |     ║",
    "║  $$$$$$$$ |$$  __$$\\ $$  __$$\\ $$ |  $$ |$$  _____|$$$$$$$\\ |$$  __$$\\ $$ |     ║",
    "║  $$  __$$ |$$ |  \\__|$$ /  $$ |$$ |  $$ |\\$$$$$$\\  $$  __$$\\ $$ /  $$ |$$ |     ║",
    "║  $$ |  $$ |$$ |      $$ |  $$ |$$ |  $$ | \\____$$\\ $$ |  $$ |$$ |  $$ |$$ |     ║",
    "║  $$ |  $$ |$$ |      \\$$$$$$$ |\\$$$$$$  |$$$$$$$  |$$$$$$$  |\\$$$$$$  |$$ |     ║",
    "║  \\__|  \\__|\\__|       \\____$$ | \\______/ \\_______/ \\_______/  \\______/ \\__|     ║",
    "║                      $$\\   $$ |                                                 ║",
    "║                      \\$$$$$$  |                                                 ║",
    "║                       \\______/                                                  ║",
    "╚═════════════════════════════════════════════════════════════════════════════════╝",
)

_BANNER_MEDIUM_LINES: tuple[str, ...] = (
    "    _                           ____        _   ",
    "   / \\   _ __ __ _ _   _ ___   | __ )  ___ | |_ ",
    "  / _ \\ | '__/ _` | | | / __|  |  _ \\ / _ \\| __|",
    " / ___ \\| | | (_| | |_| \\__ \\  | |_) | (_) | |_ ",
    "/_/   \\_\\_|  \\__, |\\__,_|___/  |____/ \\___/ \\__|",
    "             |___/                               ",
)

_BANNER_SMALL_LINES: tuple[str, ...] = (":: ArgusBot ::",)

# Backward-compatible alias for the original full-size art.
_BANNER_LINES = _BANNER_LARGE_LINES

# Cyan/blue gradient for a terminal-friendly neon effect.
_LINE_COLOR_CODES: tuple[str, ...] = (
    "1;38;5;45",
    "1;38;5;51",
    "1;38;5;87",
    "1;38;5;123",
    "1;38;5;159",
    "1;38;5;195",
    "1;38;5;159",
    "1;38;5;123",
    "1;38;5;87",
    "1;38;5;51",
    "1;38;5;45",
    "1;38;5;39",
    "1;38;5;33",
)


def maybe_print_banner(*, subcommand: str | None, stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    if not should_print_banner(subcommand=subcommand, stream=stream):
        return
    print_banner(stream=stream)


def should_print_banner(*, subcommand: str | None, stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    if subcommand != "init":
        return False
    mode = str(os.getenv("ARGUSBOT_BANNER", "")).strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return False
    if mode in {"1", "true", "on", "yes", "force"}:
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def print_banner(*, stream: TextIO | None = None, use_color: bool | None = None) -> None:
    stream = stream or sys.stdout
    if use_color is None:
        use_color = supports_color(stream)
    columns = terminal_columns(stream)
    lines = select_banner_lines(stream=stream, columns=columns)
    for idx, line in enumerate(lines):
        rendered = trim_to_columns(line=line, columns=columns)
        if use_color:
            color = _LINE_COLOR_CODES[idx % len(_LINE_COLOR_CODES)]
            stream.write(f"\033[{color}m{rendered}\033[0m\n")
            continue
        stream.write(f"{rendered}\n")
    stream.flush()


def supports_color(stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    if str(os.getenv("NO_COLOR", "")).strip():
        return False
    force_color = str(os.getenv("ARGUSBOT_FORCE_COLOR", "")).strip().lower()
    if force_color in {"1", "true", "on", "yes"}:
        return True
    if not bool(getattr(stream, "isatty", lambda: False)()):
        return False
    term = str(os.getenv("TERM", "")).strip().lower()
    return term != "dumb"


def terminal_columns(stream: TextIO | None = None) -> int:
    stream = stream or sys.stdout
    forced = str(os.getenv("ARGUSBOT_BANNER_COLUMNS", "")).strip()
    if forced.isdigit() and int(forced) > 0:
        return int(forced)
    fileno = getattr(stream, "fileno", None)
    if callable(fileno):
        try:
            return max(1, int(os.get_terminal_size(fileno()).columns))
        except OSError:
            pass
        except ValueError:
            pass
    return max(1, int(shutil.get_terminal_size(fallback=(80, 24)).columns))


def select_banner_lines(*, stream: TextIO | None = None, columns: int | None = None) -> tuple[str, ...]:
    if columns is None:
        columns = terminal_columns(stream)
    candidates: tuple[tuple[str, ...], ...] = (
        _BANNER_LARGE_LINES,
        _BANNER_MEDIUM_LINES,
        _BANNER_SMALL_LINES,
    )
    for lines in candidates:
        if banner_width(lines) <= columns:
            return lines
    return _BANNER_SMALL_LINES


def banner_width(lines: Sequence[str]) -> int:
    return max((len(line) for line in lines), default=0)


def trim_to_columns(*, line: str, columns: int) -> str:
    if columns <= 0:
        return ""
    if len(line) <= columns:
        return line
    return line[:columns]
