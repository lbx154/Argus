# Advisor tools by caller backend

The **caller backend** runs Manager, Planner, Engineer, or Reviewer. The
**Advisor backend/model** is configured independently for each project. Changing
the Advisor's model does not add tools to a caller that cannot expose them.

| Caller backend | Advisor available to a read-only role | Transport |
| --- | --- | --- |
| Pi | Yes | Native `consult_advisor` extension, added to the caller's existing tool allowlist |
| Copilot CLI | Yes | Scoped stdio MCP tool `argus_advisor-consult_advisor`, with explicit availability and permission |
| Codex, Claude, Qoder, Cursor, OpenCode, Grok, DSH | Not integrated | These adapters do not currently register and allowlist this Advisor tool for read-only calls |

Calls marked `disable_tools=True` expose no Advisor tool on any backend. Such
calls can still assess completed Advisor receipts provided in their evidence
snapshot. For other backends, a caller already allowed to use shell can use the
existing Advisor CLI. Argus never grants shell as a fallback for a read-only role.

Copilot retains `view`, `rg`, and `glob`, plus explicitly bound tools such as the
Advisor and an existing Reviewer report capability. The runtime preserves the
requested read-only policy even when the process default permits full access.
The tool does not accept project, role, model, parent-call, or budget overrides.
Those values remain bound by the actual host call.

The Copilot MCP configuration exists only for the duration of that provider
call, in a private temporary directory with a mode-0600 file. The CLI receives
an `@file` path; the bearer capability is absent from argv and prompts. The MCP
server receives only its bridge environment and the package import path.
Call-scoped tools use the ordinary CLI path instead of a warm ACP process that
cannot inherit the new capability. MCP cancellation forwards the same request
identity to the Advisor service. Host-call cleanup closes the bridge, cancels
remaining advice work, and removes the temporary configuration.

Validation covers the actual role gateway, admission options, generated Copilot
argv, real stdio MCP discovery/calls, the loopback bridge, cancellation, and
cleanup. Provider execution is replaced with fixtures; these tests do not make
real model requests. The installed Copilot CLI's help confirms support for
`--additional-mcp-config @file`, `--available-tools`, and `--allow-tool`.
