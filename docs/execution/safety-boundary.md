# Execution safety review unit A

Base: `310bc3bc2681a16356d96748a52989033fb90d37` (PR130-only). Local review only;
not an installed-runtime change or authorization to activate.

## Ownership

Execution owns `dispatch_safety.py`, its authenticated quiesce routes, provider
shared execution leases, persisted `dispatch-safety.json` plus project-local
`dispatch-state.lock`, scheduler claim selection/origin aliases and propagation
of unsuccessful work. It does not decode usage, reconcile bills, settle debt or
write finalizer obligations. Quiesce uses no cost-state lock or accounting read:
it can stop new dispatch while accounting history is damaged or locked.

`safety_io.py` contains only strict JSON decoding and durable atomic JSON IO.
These mechanical primitives carry no financial/execution permission. Execution
errors have a distinct `DispatchSafetyError`, not an accounting error type.

Pause is an explicit authenticated project-scoped epoch CAS. No clear operation,
expiry, wait wake, changed file, policy flag or successful bill acceptance clears
it. A shared execution lease already acquired before quiesce may finish; the
response says `quiescent:false`, never that the daemon stopped. Claims and debt
are untouched. This protocol does not contain an old-code daemon.

## Small admission-result contract

`core/dispatch_admission.py:AccountingAdmission` is consumed by the real backend:

- `allowed`: only permission to proceed to subsequent checks; never work success,
  permission to unpause or a scheduler retry transition.
- `reservation`: existing opaque accounting handle consumed by finalization and
  budget monitoring; execution scheduling does not interpret its ledger state.
- `reason`: denial diagnostic. Exceptions also deny before transport and do not
  write a denied receipt into an untrusted accounting source.
- `before_dispatch()`: accounting's second-phase check, inside the independent
  project execution guard. A failure prevents provider transport.
- `execution_failed(reason)`: accounting's obligation-retention notification for
  exceptional exits; must not swallow/replace the original execution exception.
- `report_budget_events`: presentation flag only, not an integrity bypass.

The implementation lives in backend `_accounting_admission.py`. A's adapter
preserves baseline monetary admission/settings and baseline finalization; its
no-op late-check/failure hooks explicitly mean baseline has **no** durable
finalizer protocol. This is a compatibility prerequisite, **not** a deployable
accounting repair. B replaces only this adapter and accounting-owned persistence
and finalization. No policy was disabled. Tests also inject a small synthetic
adapter, without loading or duplicating repaired accounting internals.

## Failure versus retries versus bills

Original ENOSPC provider/execution errors remain errors. Failed accounting
persistence cannot turn work into success; it adds its diagnostic without erasing
the original execution error. A does not infer success from an accepted bill.
Scheduler duplicate suppression separately retains running/paused origin IDs,
node-key aliases, owner and attempt, including explicit parallel override.
A legitimate successor needs an actual origin transition; time or accounting
acceptance alone cannot requeue it. No historical retry is performed here.

## Tests and non-goals

`tests/core/test_execution_separation.py` exercises the real backend with fake
transport, policy-on/off pause, between-admission/spawn pause, simple adapter
allow/deny/error/late-deny, original ENOSPC plus accounting-error propagation,
persisted pause/schema/auth/CAS, real wait wake and 1801-second suppression expiry,
inflight leases, locked/damaged accounting-independent quiesce and explicit claim
transitions. Existing backend and scheduler tests remain gates. All fixtures are
synthetic. No provider, network, dependency installation or live-state mutation.

Historical billing repair, complete provider receipts, debt-preserving old-owner
cutover, unique-owner continuation recovery, storage readiness and separately
reviewed/authorized activation remain blockers. A PASS alone authorizes none.

## A2 ownership and result-contract correction

Execution resolves `dispatch_ownership.resolve_dispatch_project` before plugin
setup, log migration, slots and monetary admission, then rechecks the same owner
before the provider lease. `_ExecContext.execution_project_root` is the fence
owner; `usage_project_root` remains the accounting adapter's routing input. A log
path, including `ARGUS_SKILL_AGENT_IO_LOG`, never grants or selects execution
permission. The legacy unscoped usage/log routing is deliberately not a billing
repair in A.

- `set_usage_context(project_root=...)` binds a trusted runtime's canonical
  session state, not its workdir. Existing explicit legacy state roots and
  boot-time not-yet-created state directories remain supported. It is an
  internal binding API, not authentication for arbitrary caller-supplied paths.
- In a worker subprocess, `ARGUS_SKILL_SESSION_ROOT` and optional matching
  `ARGUS_SKILL_SESSION_ID` preserve the actual session (not a newly calculated
  cwd fingerprint). Conflicting explicit/inherited identity, a missing inherited
  owner, malformed metadata, or a workdir inconsistent with bound metadata deny.
- Unbound workdir calls resolve an existing registered session by its canonical
  workdir (including descendants), or an existing legacy fingerprint directory.
  Multiple candidates deny; callers must bind the actual session rather than
  choose the newest/unpaused one. Workspace symlink aliases resolve normally;
  session-state/metadata/fence aliases and provider-lock symlinks deny on Linux.
  These checks do not claim security against a hostile local process swapping
  directories after checks or an old daemon ignoring this protocol.
- Generic standalone `AgentCliBackend` calls retain the explicit **standalone
  compatibility policy** (`require_project=False`, the public default): when
  there is no explicit/inherited/registered project evidence, they are unscoped,
  not automatically registered. No ambient cwd or log directory becomes an
  authority. This is for setup/doctor/general standalone turns, not a project
  pause guarantee. Registered or conflicting project evidence cannot be bypassed
  with this flag. Project-only callers must require an owner.
- The real paper reviewer fallback uses `require_project=True` and explicitly
  binds the resolved owner before the gateway. Both language and infrastructure
  review callers reach it. The supervisor's old cwd-only guessed binding now
  resolves the actual session and rejects unregistered workdirs. Main/engineer/
  reviewer/planner/curator/manager, advisor, peer and map paths retain their
  explicit bindings. Forks preserve the binding and project requirement.
- An explicitly bound no-tools call may use a detached scratch workdir (the
  existing map-generation path); it still holds its owning project's fence.
  Tools-enabled calls do not receive this detached-workdir exception.
- API quiesce keeps its authenticated server-selected project, never inherits a
  worker session environment. No accounting dependency is added to quiesce.

`AccountingAdmission.allowed` must be a literal `bool`. Missing or non-callable
`before_dispatch` / `execution_failed` hooks fail closed at consumption (also
rechecked just before transport); defaults are `None`, not implicit permission.
The baseline adapter alone explicitly supplies named legacy no-op hooks. They
still do not implement B's financial integrity or durable debt protocol.

`prepare_result` centralizes existing known-secret redaction for fatal error,
messages, stdout and stderr, and stamps call/session identity and timing.
`finalize_without_accounting` additionally closes I/O without ledger, settlement,
metric or finalizer writes. Admission exceptions use it; original typed escaping
provider exceptions are still re-raised, even if failure notification fails.

### B integration handoff (not applied here)

B must continue to provide a literal bool and both real hooks; no field names or
hook signatures changed. Its replacement `_exec_finalize.py` must retain/import
A2's `prepare_result` and `finalize_without_accounting` helpers and keep the
non-accounting denial path separate from B's evidence/finalizer writes. Preserve
A2's `execution_project_root` routing and ownership checks when reconciling source.
No change to `cost_control.py` or `usage.py` is made or requested by A2. Integration
and B revalidation belong to the parent; neither B's branch nor its financial
logic was edited or merged. Raw model-API/image transports outside the agent-CLI
execution unit are not newly wrapped or certified by this revision.
