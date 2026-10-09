"""Operating-system capabilities the hosted trial service needs from its host.

The hosted service (gateway, portal, analytics, training capture, compute and
the container tooling) runs on a Linux operator host; see
docs/hosted-research-trial.md. Ordinary desktop and Web installations never
start it. The client pieces a Windows or macOS user runs -- trial setup
(``client``), the desktop protocol, attention, plugins and the runtime opt-in
hook (``training_runtime``) -- do not depend on these capabilities.

Where a hosted operation needs a capability this platform lacks, it raises
``HostedHostRequired`` naming the capability, instead of failing with an
``AttributeError`` or silently weakening a filesystem or peer-identity check.
"""
from __future__ import annotations

import hashlib
import os
import socket
import sys
from pathlib import Path

HOSTED_HOST = "the hosted trial service runs on its Linux operator host (docs/hosted-research-trial.md)"


class HostedHostRequired(Exception):
    """A hosted-trial operation needs an OS capability this platform lacks.

    Deliberately not an OSError or RuntimeError: handlers that turn those into
    "unsafe path" or "unavailable" results must not hide a wrong host.
    """


# Directory descriptors that refuse every symlink, opened relative to a parent.
# Evaluated once: callers may wrap os.open, which must not change the answer.
_NO_FOLLOW_DIRECTORIES = hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def no_follow_directories() -> bool:
    return _NO_FOLLOW_DIRECTORIES


def require_no_follow_directories(operation: str) -> None:
    if not no_follow_directories():
        raise HostedHostRequired(
            f"{operation} opens tenant files through no-follow directory descriptors "
            f"(O_DIRECTORY, O_NOFOLLOW, dir_fd), which this platform lacks; {HOSTED_HOST}"
        )


def require_mounted_volumes(operation: str) -> None:
    if os.name != "posix":
        raise HostedHostRequired(
            f"{operation} checks POSIX mount points and opens volume markers without following "
            f"symlinks, which this platform lacks; {HOSTED_HOST}"
        )


def require_unix_sockets(operation: str) -> None:
    if not hasattr(socket, "AF_UNIX"):
        raise HostedHostRequired(f"{operation} uses Unix domain sockets, which this platform lacks; {HOSTED_HOST}")


def require_posix_accounts(operation: str) -> None:
    if not hasattr(os, "getuid"):
        raise HostedHostRequired(
            f"{operation} assigns tenant files to POSIX user and group IDs, which this platform lacks; {HOSTED_HOST}"
        )


def require_linux_process_identity(operation: str) -> None:
    """Peer binding reads /proc/<pid> start ticks, executable and root."""
    if not sys.platform.startswith("linux"):
        raise HostedHostRequired(
            f"{operation} verifies peers through Linux /proc process identity (start ticks, "
            f"executable, mount root), which this platform lacks; {HOSTED_HOST}"
        )


def boot_id() -> str:
    """An identifier that changes on every reboot of this machine.

    Linux exposes one directly. Elsewhere the kernel's boot timestamp (which
    psutil reads from ``kern.boottime`` on macOS) is hashed into the same
    shape, so persisted worker registrations from an earlier boot never match.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        pass
    import psutil

    # Windows derives boot time from uptime, which can drift by a second.
    stamp = round(psutil.boot_time())
    digest = hashlib.sha256(f"argus-boot:{stamp}".encode()).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
