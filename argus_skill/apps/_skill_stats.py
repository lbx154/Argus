"""Legacy command after removal of runtime Skill matching and scoring."""
from __future__ import annotations

import json
from pathlib import Path


def run_skill_stats(life_dir: Path, *, as_json: bool = False) -> int:
    _ = life_dir
    payload = {
        "available": False,
        "reason": "Agents discover semantic Skill libraries directly; no runtime matching or effectiveness counters are recorded.",
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(payload["reason"])
    return 0


__all__ = ["run_skill_stats"]
