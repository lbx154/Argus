"""Feed events to the Mission View the way the runtime does: through the log."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from argus.core.mission_view import load_mission_view
from argus.life.event_log import JsonlEventSink


def project_event(root: Path | str, event: Mapping[str, Any]) -> dict[str, Any]:
    """Append ``event`` to ``root``'s event log and return the view computed from it.

    An event the log would mark invalid is never projected, so it is refused here.
    """
    from argus.core.event_catalog import normalize_event_envelope

    problems = normalize_event_envelope(dict(event), timestamp=0.0).get("event_validation")
    assert not problems, f"{event.get('type')}: {problems}"
    JsonlEventSink(None, life_dir=Path(root)).append(dict(event))
    return load_mission_view(root)
