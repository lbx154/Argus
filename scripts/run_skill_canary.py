#!/usr/bin/env python3
"""Three-call live canary for the skill generate→reuse→reject loop."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.core.knobs import resolve_role_backend, resolve_role_model
from argus_skill.skills.scientist import SkillScientist
from argus_skill.skills.skill_router import SkillRouter
from argus_skill.skills.store import SkillStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    events_path = root / "events.jsonl"
    os.environ["ARGUS_SKILL_AGENT_IO_LOG"] = str(events_path)

    global_skills = Path.home() / ".argus-skill" / "skills"
    global_before = sum(
        1 for path in global_skills.rglob("*.md") if "_archive" not in path.parts
    )
    backend_name = resolve_role_backend("engineer")
    model = resolve_role_model(
        "engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL",
    )
    backend = AgentCliBackend(backend=backend_name)
    store = SkillStore(
        root / "skills",
        runner=backend,
        matcher_model=model,
        matcher_reasoning_effort="low",
    )
    router = SkillRouter(skill_store=store, judge_runner=backend, judge_model=model)
    emitted: list[dict] = []

    origin = (
        "Design a reusable procedure for checking atomic JSONL log rotation "
        "under concurrent appenders without losing or duplicating a completion record."
    )
    related = (
        "Verify that a rotating newline-delimited event log preserves exactly-once "
        "terminal markers while another process is appending."
    )
    unrelated = "Write a short haiku about spring rain."

    started = time.time()
    raw = SkillScientist(
        backend, model=model, reasoning_effort="medium",
    ).distill(origin)
    candidate = router.create_candidate(
        raw, task=origin, on_event=emitted.append,
    ) if raw else None

    related_match, _ = store.find_relevant(
        related, on_event=emitted.append, role="engineer",
    ) if candidate else (None, 0)
    if related_match:
        store.record_reuse(
            related_match[0], task_desc=related, success=True,
            on_event=emitted.append,
        )
    unrelated_match, _ = store.find_relevant(
        unrelated, on_event=emitted.append, role="engineer",
    ) if candidate else (None, 0)

    summaries = store.list_summaries()
    learned = store.load(summaries[0]["path"]) if summaries else None
    raw_events = events_path.read_text(encoding="utf-8", errors="ignore") if events_path.exists() else ""
    global_after = sum(
        1 for path in global_skills.rglob("*.md") if "_archive" not in path.parts
    )
    report = {
        "root": str(root),
        "backend": backend_name,
        "model": model,
        "elapsed_s": round(time.time() - started, 3),
        "web_search_observed": '"toolName":"web_search"' in raw_events,
        "candidate_created": candidate is not None,
        "candidate_name": candidate.name if candidate else "",
        "related_match": [skill.name for skill in related_match or []],
        "unrelated_match": [skill.name for skill in unrelated_match or []],
        "confirmed": bool(learned is not None and not learned.provisional),
        "successful_reuses": learned.successful_reuses if learned else 0,
        "isolated_file_count": len(summaries),
        "global_skill_count_before": global_before,
        "global_skill_count_after": global_after,
    }
    report["passed"] = bool(
        report["web_search_observed"]
        and report["candidate_created"]
        and report["related_match"]
        and not report["unrelated_match"]
        and report["confirmed"]
        and report["isolated_file_count"] == 1
        and global_before == global_after
    )
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
