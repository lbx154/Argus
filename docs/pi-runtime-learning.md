# Project tool learning during Pi tasks

Normal Argus tasks using the Pi backend receive project runtime tools automatically.
There is no separate experiment command, model provider, reviewer or learning daemon.
The ordinary provider call retains its existing admission, budget, timeout and
cancellation handling. Existing Skill recall and end-of-task Wiki/Skill maintenance
remain responsible for knowledge reuse and revision.

For example, an Engineer repeatedly normalizing textual identifiers can turn the
operation into `def run(value): return [item.zfill(4) for item in value]`. The model
supplies observed evidence, explicit input/expected cases, a Skill explaining when
to use it, and a Wiki page describing verified behavior and limits. The tool is
tested in that same task, then used on current task data. Successful use publishes
the tool and its knowledge for the next task in the same project.

## Available tools

| Tool | Behavior |
| --- | --- |
| `list_learned_tools` | Lists project tools, current revisions, descriptions and source record paths. |
| `run_learned_tool` | Runs a named JSON transformation. Successful use of a pending candidate publishes its code, Skill and Wiki. |
| `evolve_runtime` | Proposes a new tool or revises an existing name with its expected revision, Python source, cases and documents. |
| `rollback_runtime` | Restores the owning role's previous code and documents, or withdraws its first revision. |

Engineer, writable Manager and ordinary writable SELF calls can propose tools.
Reviewers and other read-only roles can reuse tools but cannot publish or roll back.
`ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING=0` disables new runtime learning along with
the existing role-memory maintenance switch. Tools-disabled calls, nested advisor
calls, supervisor calls and isolated framework-maintenance calls receive no new
runtime extension. Other backends retain their existing behavior.

## Execution contract

Source defines a synchronous `run(value)` function that consumes and returns JSON.
It supports ordinary control flow, selected builtins and explicit `from math`,
`from re` or `from json` imports of the pure functions listed in
[`runtime_worker.py`](../argus_skill/skills/runtime_worker.py). General imports,
interpreter reflection, decorators, classes and file/network/process APIs are
unavailable. Use existing task tools to read files and write outputs, passing
bounded JSON values to the learned function. The pilot's general-purpose Python
file inspector is not automatically installed into user projects.

The worker runs as a separate `python -I -S` process in an empty temporary directory,
with no provider credentials, role-tool capabilities or user import paths in its
environment. Source validation, limited builtins and a Python audit hook restrict
the API; a four-second wall timeout and bounded JSON input/output apply. POSIX
workers also have CPU, address-space and file-size limits. This deliberately
restricted transformation runner is not a general Python security sandbox.
It does not change the permissions or recursive-spawn behavior of ordinary Bash
tools outside this feature.

A candidate requires three ordinary tool observations and two distinct cited IDs,
two to eight distinct test inputs, and valid bounded Skill/Wiki fields. Previously
recorded contract cases must still pass, up to 16 accumulated cases. At most three
proposals and one publication are admitted per role call. Case success alone does
not publish anything: the current task must successfully call the candidate.
Failed use or cancellation discards the pending candidate and retains the active
version. Tests and real input are chosen by the model; these checks establish
execution behavior, not the truth of its interpretation or general task accuracy.

## Storage and lifecycle

State uses the host-bound project root, not an agent-supplied filesystem path:

```text
<project-state>/runtime-tools/<semantic-name>.json
<project-skills>/<owning-role>/runtime-<semantic-name>.md
<project-state>/.autors/runtime/wiki/pages/<semantic-name>.md
```

The Skill enters the normal role library and the Wiki enters normal Wiki discovery.
The tool record preserves source, regression cases, evidence IDs, originating call
and up to three previous versions. Tools are project scoped and capped at 32 names.
Updates use an expected revision and project lock. Changes to Skill/Wiki during
validation cause a conflict instead of overwriting the newer documents. Publication
restores the prior documents if persistence fails. Native tool calls and their
results use the normal agent I/O evidence path.

No startup run executes learned code; tools run only when the current task calls
them. Pending candidates are bound to one provider call and discarded when it ends.
The same bundled Pi extension API is used as the existing advisor and experience
tools, so this feature ships with the Argus package and needs no separate Pi fork.

## Verification

```bash
python -m pytest -q tests/skills/test_runtime_tools.py tests/skills/test_runtime_tools_context.py
ARGUS_PI_TEST_CLI=/path/to/pi/packages/coding-agent/dist/bundle/cli.js \
  python -m pytest -q tests/skills/test_runtime_tools_pi.py tests/advisor/test_pi_extension_loading.py
```

The first pair tests publication, cross-turn/project reuse, regression preservation,
rollback, conflicts, scope, cancellation and the ordinary backend entry point.
The second pair exercises the actual Pi loop against a local scripted provider and
checks extension discovery from both source and an installed wheel. No test uses
paid model endpoints.
