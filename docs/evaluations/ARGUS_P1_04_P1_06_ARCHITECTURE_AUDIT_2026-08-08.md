# P1-04 / P1-06 architecture and runtime simplification audit

Date: 2026-08-08  
Baseline: private `origin/main` at `952911ef`

## Vertical boundary inventory

| Surface | Before | Classification | Result |
| --- | --- | --- | --- |
| `core/project_api.py` | imported `verticals._base` to resolve completion | true dependency inversion | core now receives the validated `required_gate`; no core module imports `argus.verticals` |
| `core/integrity_gate.py` | paper citation/scorer policy in core | true research leak | moved to `verticals/research/integrity_gate.py` |
| Planner/Reviewer prompts | imported research canonical stage order | true research leak | use the active `VerticalContract.stage_order` |
| mission setup | imported `kernel_engineering.baseline_workspace` | true vertical leak | calls the optional `prepare_mission` contract hook |
| Manager stage planning | silently used research stages for an incomplete provider | wrong compatibility fallback | incomplete/missing verticals fail visibly |
| persisted data-domain completion names | `full_emnlp` / `full_paper` | compatibility adapter | read only in `_data_domain.py`, normalized to generic `certified` |
| research/venue events and persisted target fields | already on disk and externally readable | compatibility/event vocabulary | retained; they do not select another vertical or change core completion policy |

`argus/core/vertical_contract.py` is the only framework contract. A vertical declares
stage order, checklist items, completion strength, optional role guidance/evidence schema,
independent-review requirement, workflow mode, and optional mission/search hooks. The
minimal non-research fixture in `tests/core/test_vertical_contract.py` runs without paper,
venue, or research-target symbols. Invalid plugins are rejected during registration.

## Reviewed runtime paths

| Path | Removed accidental complexity | Boundary intentionally retained |
| --- | --- | --- |
| Web Manager dispatch | removed 55 alias/re-export lines and the `_bridge()` monkeypatch indirection; state, dispatch, and pending-question calls now import their owner | per-session lock, authorization, cancellation, and transcript idempotency |
| Pending question API | removed two pass-through wrappers in `mission_items.py` | Manager authority/CAS remains in `manager_pending_question.py` |
| Role sessions | removed eight inert constructor/config fields and two dead environment knobs | role isolation, atomic capsule write, turn/token limits, backend/branch/objective rotation |
| Completion | removed core-side vertical resolution and paper-specific source/gate names | fail-closed evidence ranking and atomic DONE write |
| Vertical loader | removed research fallback and broad hook exception swallowing | trusted plugin API version, project-local domain adapter, visible import/contract failure |

## Static deltas on the reviewed files

`(lines, branches, functions)`:

| File | Before | After |
| --- | ---: | ---: |
| `verticals/_base.py` | `(284, 22, 14)` | `(166, 7, 16)` |
| `webapi/manager_bridge.py` | `(566, 48, 7)` | `(514, 48, 7)` |
| `webapi/mission_items.py` | `(573, 33, 26)` | `(534, 33, 24)` |
| `engineer/round_config.py` | `(303, 17, 7)` | `(278, 17, 7)` |
| `core/project_api.py` | `(288, 9, 4)` | `(270, 8, 3)` |

The pending-question call path changed from
`server → mission_items wrapper → manager_bridge alias → manager_pending_question`
to `server → manager_pending_question`. The completion path no longer enters the
vertical package from core. These are behavior-preserving depth reductions; the new
frontier and vertical contracts add real state/policy rather than forwarding layers.

## Regression boundary

The cleanup did **not** remove authentication, sandboxing, secret redaction, operator
authority, baseline isolation, idempotent decisions, atomic persistence, crash recovery,
or backend failure escalation. Focused tests cover each retained boundary, followed by
the full Python suite and frontend type/build tests.
