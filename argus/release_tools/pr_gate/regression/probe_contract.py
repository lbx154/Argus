"""Shared process-exit interpretation for probe producers and validators."""


def signal_exit(returncode: int) -> bool:
    """Include POSIX signals and Linux shell 128+signal conventions."""
    return returncode < 0 or 129 <= returncode <= 192
