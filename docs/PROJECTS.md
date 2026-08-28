# Argus Projects

The chosen model is:

```text
Operator
└── Project
    ├── execution/work directory
    ├── Argus-owned state: ~/.argus-skill/projects/<project-id>/
    ├── Wiki
    ├── Project Skills
    └── Missions
```

A Project is Argus's persistent organizing boundary. It is not one task, one backend, one provider/model session, or one round of role execution. A Project has one execution/work directory where roles inspect, build, and write project artifacts, plus one Argus-owned state directory under `~/.argus-skill/projects/<project-id>/` for runtime memory and control state. Current runtime paths keep the state root separate from the workdir, and session metadata persists the authoritative workdir used by Manager, Planner, Engineer, and Reviewer roles (`argus_skill/core/paths.py:62-67,114-137`; `argus_skill/core/session.py:47-58,79-106`). The Project continues after any single Mission settles.

A Mission is one bounded backlog unit within a Project. It carries an objective, scope, acceptance criteria, non-goals, owned paths, context references, and execution workdir in `mission.json`; it also owns `frontier.json` and `CHECKPOINT.md` under the same mission directory. The frontier records the current hypothesis, artifacts, evidence, resolved and remaining obligations, uncertainty, next decision point, reviewed transitions, and whether the next move is continue, diagnose, or replan (`argus_skill/life/context_packet.py:245-344`; `argus_skill/core/task_frontier.py:89-241`). Role execution is separated by responsibility: Manager stages and routes, Planner decomposes, Engineer performs the work, and Reviewer provides the verdict (`README.md:43-53`). Engineer and independent Reviewer handoffs are separate records, and `latest.json` points later roles at the current handoff reference (`argus_skill/life/context_packet.py:347-437`). Settlement records execution, review, stage-certification, interruption, and resumability dimensions; Project completion is a separate project-level write that requires declared evidence and the active vertical's completion gate (`argus_skill/life/mission_outcome.py:78-145`; `argus_skill/core/project_api.py:94-151,154-251`).

Project Wiki is project-scoped declarative knowledge. Wiki records what the Project knows. It is for facts and interpretations that should survive beyond the current Mission: architecture and interfaces, constraints and environment, findings, and decisions. The current Wiki tree is `.autors/<semantic-project>/wiki/`, with `INDEX.md`, `README.md`, and semantic pages under `pages/`; current tooling enforces only a page title, description, and Markdown content, so the organization below is documentation guidance rather than a required schema (`argus_skill/wiki/bootstrap.py:29-44`; `argus_skill/wiki/schema.py:1-14,56-86`; `argus_skill/wiki/store.py:29-44,63-94`).

Project Skills are project-scoped procedural knowledge. Skills record how the Project works. They capture reusable procedures, checks, diagnostic methods, stopping conditions, recovery methods, and role-specific ways of working for this Project. Current Skill storage is path-only: agents receive ordered library roots and inspect relevant Skills on demand; the runtime does not parse, match, rank, or inject Skill bodies. The ordered library model is project, vertical/domain, then global, and source documentation states that Project Skills override vertical and profile Skills for the same operation while task instructions, current evidence, and role boundaries still override Skill text (`argus_skill/skills/role_library.py:90-132,135-182`; `argus_skill/skills/layered.py:27-91,149-167`; `docs/FEATURES.md:337-359`).

Production and reuse are explicit. Missions create observable evidence and reviewed outcomes. During or after reviewed work, agents curate stable facts and insights into the Project Wiki, and procedures into Project Skills; later missions and roles in the same Project search and read those paths. After settlement, existing TEAM learning may review verified Project Skill deltas and promote broadly reusable procedures into shared profile role Skills. Current Argus has no automatic cross-project Wiki or knowledge-promotion route (`argus_skill/manager/skill_tidy.py:338-499`; `docs/FEATURES.md:284-318,360-372`).

| Concept | Current storage |
| --- | --- |
| Workdir | Active execution directory recorded as the Project/session `workdir` and per-Mission `execution_workdir` |
| Argus-owned Project state | `~/.argus-skill/projects/<project-id>/` |
| Mission state | `handoffs/<mission-id>/` with `mission.json`, `frontier.json`, `CHECKPOINT.md`, reviewed handoffs, and `latest.json` |
| Project Wiki | `.autors/<semantic-project>/wiki/` |
| Project Skills | project-state `skills/<role>/` or other semantic Project Skill paths |
| Shared profile Skills | `~/.argus-skill/skills/<role>/` |
