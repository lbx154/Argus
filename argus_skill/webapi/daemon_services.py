"""The two daemon operations used by project and work-item HTTP commands.

``create_app`` supplies one immutable set per app. Business functions receive
only the operation they need; this is not a registry for unrelated services.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..daemon.state import DaemonStatus

DaemonStatusReader = Callable[[Path], DaemonStatus]


class ProjectDaemonStarter(Protocol):
    def __call__(
        self,
        sid: str,
        *,
        global_root: Path | str | None = None,
        resume_continuous: bool = False,
        reclaim_idle: bool = False,
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class DaemonServices:
    read_status: DaemonStatusReader
    start: ProjectDaemonStarter
