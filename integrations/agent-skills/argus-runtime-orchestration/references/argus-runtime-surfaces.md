# Argus runtime surfaces

**Evidence level:** source-inspected on `argus 0.1.1` at revision `009f7a19`; conditional on other versions.

- `argus doctor` is the routine read-only preflight; `argus doctor --deep` adds available backend authentication probes and should be used only when needed. It is not universal proof of a live route: heed unchecked-token/offline/unreachable findings and use a backend-specific non-mutating readiness probe when required. `argus --doctor` remains a legacy deep-diagnostic alias. `argus --status` inspects the selected project. Top-level `--help` is intentionally terse and does not enumerate every accepted automation flag.
- Local parser `argus/apps/cli/_parser.py` defines `--daemon`, `--daemon-fg`, `--daemon-stop`, `--status`, `--continuous`, `--objective`, `--bounded`, `--notify`, `--follow`, cockpit/Web, resume, and related controls. Local README documents the two-party operating model: an outer operator supervises Argus; any provider CLI Argus invokes internally is configuration, not a third role.
- Local route registrations in `argus/webapi/routes/projects.py`, `workitems.py`, and `manager.py` define project snapshot/status, item answer, decision resolve, and Manager message endpoints.
- Local `argus/webapi/project_state.py` and `mission_items.py` expose `pending_questions`; Manager dispatch code routes a normal reply only when exactly one question is pending and points multiple questions to item-specific Needs you prompts.
- Local operator-decision and pending-question code/tests establish durable `pending_question`/`operator_decision` resolution and continuation semantics used by the core loop.

For any other Argus build, keep terminal/Web cockpit plus `--status` as the baseline only after a safe probe. Before using daemon, notify, HTTP routes, payload fields, or resume semantics, verify that build's docs/source/API schema. A missing entry in terse top-level help is not by itself proof that a flag is unsupported.
