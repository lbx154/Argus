# RunExec Gateway

Application code must invoke model backends through
`argus_skill.core.run_gateway.run_exec`.

The gateway owns the stable application request shape and supplies one future
interception point for tracing, metrics, retry policy, release fencing, and
provider-independent limits. Provider subprocess details and usage extraction
remain inside adapters.

Only two raw `.run_exec()` calls are allowed:

1. `RunExecGateway` delegating to a `RunnerBackend`.
2. `AgentCliBackend` delegating to the vendored provider CLI runner.

A source-audit test scans the package AST and rejects any new bypass.

The gateway preserves compatibility with lightweight judge/test backends that
return duck objects or strings. It normalizes call identity and timing only for
real `RunnerResult` instances. It also preserves the semantic distinction
between omitting `resume_thread_id` and explicitly passing `None` for a fresh
session.
