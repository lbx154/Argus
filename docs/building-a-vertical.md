# Building a vertical

A vertical teaches Argus what "done" means in one field: the stages work moves
through, the checklist the Reviewer applies at each stage, and the skills the
roles read before they start. This page builds a small real one,
`lab_notebook`, from nothing to an installed entry in the Vertical Store. The
finished files are under [`examples/verticals/`](../examples/verticals/) and
every command below was run against this checkout.

Background reading: [the Vertical Store](vertical-store.md) for the store's
internals and hosted mode, and
[single-agent verticals](single-agent-vertical-20260915.md) for the design
discussion behind the contract.

## What a vertical is, in code

A vertical is a Python package whose `stages.py` declares a handful of
module-level names. There is no base class and no registration call: the
framework imports the module and validates the names through
`argus.core.vertical_contract.vertical_contract`, and (for anything not built
in) `argus.verticals._registry._validated_plugin`. The built-in seven are
listed by name in `argus/skills/vertical_select.py`; everything else arrives
through the store or a Python entry point in the group `argus.verticals`.

The names the contract reads:

| Name | Required | Meaning |
|---|---|---|
| `ARGUS_VERTICAL_API_VERSION` | yes, for non-built-ins | must be `1`; any other value hides the vertical |
| `VERTICAL_PURPOSE` | yes, for non-built-ins | one sentence; it is the line the Manager sees when choosing a vertical, so write it for routing, not as an abstract |
| `CHECKLIST_STAGE_ORDER` | yes | tuple of stage names, in order; no duplicates |
| `CHECKLIST_ITEMS` | yes | dict `stage -> tuple[ChecklistItem, ...]`; every non-optional stage needs a non-empty checklist |
| `completion_gate` | yes | `"none"`, `"metric"` or `"certified"` |
| `CHECKLIST_OPTIONAL_STAGES` | no | stages that may have no checklist |
| `STAGE_ALIASES` | no | `{"experiment": "measure"}`: other names the Manager may use for a stage |
| `WORKFLOW_MODE` | no | `"staged"`, `"direct"` or `"proportional"` |
| `MISSION_KIND` | no | `"custom"`, `"optimize"`, `"research"` or `"software"` |
| `REQUIRE_INDEPENDENT_REVIEW` | no | defaults to `True` |
| `role_banner(role)` | no | a paragraph each role reads before its task |
| `render_role_prompt_fragment`, `render_role_prompt_context`, `stage_completion_issues`, `prepare_mission`, `LIBRARY_PREPARER`, `EVIDENCE_SCHEMA`, `WORKFLOW_PROFILES` | no | hooks the larger built-ins use; not needed for a first vertical |
| `VERTICAL_SKILLS`, `VERTICAL_SKILL_PARENTS`, `VERTICAL_ROUTING_PATH` | no | an explicit skills root (a `skills/` directory next to `stages.py` is found automatically), verticals whose skills are seeded before yours, and a browsing category for the store |

A `ChecklistItem` (from `argus/skills/stage_machine.py`) has three fields:

```python
@dataclass(frozen=True)
class ChecklistItem:
    id: str
    statement: str
    evidence_hint: str
```

The `statement` is what must be true; the `evidence_hint` tells the Engineer
what to show and the Reviewer what to look for. This is the whole mechanism:
the Reviewer is read-only, so the quality of your checklist is the quality of
your review.

## Two built-ins to learn from

**`software`** (`argus/verticals/software/`) is the smallest useful vertical:
one stage, `delivery`, with three checklist items, a `role_banner`, and four
skill files. Its `stages.py` is worth reading in full; the module docstring
explains why the checklist exists ("code that does not build" was the failure
it was written against), and the first two item ids are marked protected so a
Planner may add items but never weaken them:

```python
STAGE_ORDER = ["delivery"]
CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)
CHECKLIST_OPTIONAL_STAGES: tuple[str, ...] = ()
completion_gate = "none"
MISSION_KIND = "software"
WORKFLOW_MODE = "staged"

PROTECTED_ITEM_IDS = frozenset({"delivery.builds", "delivery.tests-executed"})
```

Its skills sit at `skills/<role>/<name>.md`:

```
software/skills/engineer/software-change-implementation.md
software/skills/manager/software-project-grounding.md
software/skills/planner/software-project-grounding.md
software/skills/reviewer/software-change-review.md
```

**`research`** (`argus/verticals/research/`) is the largest: four stages,

```python
CANONICAL_STAGE_ORDER: tuple[str, ...] = ("idea", "experiment", "paper", "review")
STAGE_ALIASES = {"research": "idea", "plan": "experiment", ..., "submission": "review"}
```

with `completion_gate = "certified"`, `WORKFLOW_MODE = "proportional"`, role
prompts in `prompt_policy.py`, a `prepare_mission` hook in `mission_brief.py`,
and some forty engineer skills plus seven reviewer skills. It shows where a
vertical can grow; it is not where one should start.

The directory shape both share, and the store expects:

```
<name>/
  __init__.py          docstring only
  stages.py            the contract
  skills/
    engineer/*.md
    reviewer/*.md
    manager/*.md       (optional)
    planner/*.md       (optional)
    engineer/references/   supporting files, not skills (optional)
    engineer/*_scripts/    .py/.json/.sh copied verbatim (optional)
```

## Step 1: decide the stages and the checklist

`lab_notebook` is for small measurement tasks: run something on this machine,
then write it up so someone else can rerun it. Two stages follow from that,
`measure` and `report`, and each gets the two or three things a reviewer
would actually check.

Write the checklist before anything else, and write it as statements a
read-only reviewer can verify from files. "The measurement was repeated" is
checkable (there are N raw files); "the measurement is good" is not.

`examples/verticals/argus_verticals/lab_notebook/stages.py`:

```python
from argus.skills.stage_machine import ChecklistItem

ARGUS_VERTICAL_API_VERSION = 1
VERTICAL_PURPOSE = (
    "small measurement tasks on this machine: run the measurement, record how "
    "it was run, and write a notebook entry another person can reproduce"
)

CHECKLIST_STAGE_ORDER: tuple[str, ...] = ("measure", "report")
CHECKLIST_OPTIONAL_STAGES: tuple[str, ...] = ()
STAGE_ALIASES = {"experiment": "measure", "writeup": "report"}

completion_gate = "none"
WORKFLOW_MODE = "staged"
MISSION_KIND = "custom"
REQUIRE_INDEPENDENT_REVIEW = True

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "measure": (
        ChecklistItem(
            id="measure.ran-here",
            statement=("The measurement was executed on this machine in this project, "
                       "and its raw output is saved in the work directory."),
            evidence_hint="the command that was run and the path of its raw output",
        ),
        ChecklistItem(
            id="measure.repeated",
            statement=("The measurement was repeated, and the reported number is a "
                       "median or mean with its spread; a single run is not a result."),
            evidence_hint="number of repeats, the aggregate and the spread",
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.notebook-entry",
            statement=("NOTEBOOK.md in the work directory states what was measured, "
                       "how, the result with its spread, and the exact command to rerun it."),
            evidence_hint="the NOTEBOOK.md section and the rerun command",
        ),
        ChecklistItem(
            id="report.numbers-traceable",
            statement="Every number in NOTEBOOK.md can be traced to a saved raw output file.",
            evidence_hint="raw file paths next to each number",
        ),
    ),
}


def role_banner(role: str) -> str:
    return (
        "LAB NOTEBOOK VERTICAL: measure first, then write. A number without a "
        "saved raw output and a rerun command is not a result. The Reviewer "
        "checks the notebook entry against the raw files, not against the "
        "Engineer's summary."
    )
```

Why these particular choices:

- `completion_gate = "none"` means the project is complete once the last
  stage's checklist is met. `"metric"` is for verticals whose completion is a
  number reaching a target; `"certified"` adds an explicit certification step,
  which the research vertical uses for papers. A notebook entry needs neither.
- `WORKFLOW_MODE = "staged"` makes the Manager move through the stages in
  order. `"direct"` skips staging for one-shot work; `"proportional"` lets the
  Manager scale the process to the task. Start with `staged`; the aliases let
  a Manager that says "experiment" land on `measure`.
- `MISSION_KIND = "custom"` because the other three values switch on
  behaviour written for optimization loops, research campaigns and repository
  changes.
- The `role_banner` says one thing, in the field's own terms. It is read by
  every role, so it is the place for the rule that ties the stages together.

The contract check refuses, with a message naming the problem, a stage with no
checklist, a checklist for a stage that is not in the order, a duplicate stage,
an item without an `id` or `statement`, a repeated item id within a stage, and
an unknown `completion_gate` value. Run it directly while you iterate:

```bash
python -c "
import importlib.util, pathlib
from argus.core.vertical_contract import vertical_contract
p = pathlib.Path('examples/verticals/argus_verticals/lab_notebook/stages.py')
spec = importlib.util.spec_from_file_location('lab_notebook_stages', p)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
c = vertical_contract('lab_notebook', m)
print(c.stage_order, c.completion_gate, c.workflow_mode)
"
```

## Step 2: write the skills

A skill is a Markdown file with exactly two front-matter fields, `name` and
`description`, both quoted. The runtime does not parse skill bodies: the
roles receive the library paths and read the files themselves, so the
description is a routing line (it is what an agent reads to decide whether to
open the file; the repository's own test caps it at 1200 characters) and the
body is written for a reader who will act on it.

`skills/engineer/measurement-record.md`:

```markdown
---
name: "Measurement Record"
description: "How to run a small measurement so the result can be checked and rerun: save raw output, repeat, report the spread, and record the exact command."
---

# Measurement record

Run the measurement from a script saved in the work directory, never from an
interactive shell you cannot show later. Write the raw output to a file under
`results/` before computing any aggregate.

Repeat the run. Report the median (or mean) together with the spread (min/max or
standard deviation) and the number of repeats. A single run is not a result.

Record in `NOTEBOOK.md`:

- what was measured and on which hardware
- the exact command to rerun it
- the aggregate, the spread and the number of repeats
- the path of the raw output every number came from
```

`skills/reviewer/measurement-review.md` tells the Reviewer to open the raw
files and recompute one aggregate before accepting, and to return `continue`
for any number without a file behind it. The two skills and the two checklists
say the same thing from three sides; that redundancy is deliberate, because
each role reads only its own.

Rules the loader applies: skills live at `skills/<role>/` where the role is
`engineer`, `reviewer`, `planner` or `manager` (anything else is filed as
general); a `references/` directory is copied as supporting material rather
than as skills; `*_scripts/` directories are copied verbatim; names starting
with `_` or `.` are skipped. When the roles start a task with this vertical,
those files are seeded into the project's skill library in the order project,
vertical, global, so a project's own edits win.

## Step 3: package it for the store

The store installs a zip whose members sit under `argus_verticals/<name>/`,
described by a `catalog.json`. The catalog entry the store validates
(`argus/verticals/store.py`, `_validate_entry`) needs:

| Field | Constraint |
|---|---|
| `name` | equals the key; `^[a-z][a-z0-9_]{0,47}$` |
| `version` | non-empty, no slashes |
| `module` | `argus_verticals.<name>.stages`, and it must live in `paths[0]` |
| `paths` | at least `["argus_verticals/<name>"]` |
| `purpose` | non-empty |
| `archive.file`, `archive.url`, `archive.sha256`, `archive.size` | the zip's name, location, digest and exact byte size; a non-`https` URL is only accepted when the catalog itself is local |

Optional: `purpose_zh`, `requires` (other verticals to install first),
`shared` (helper trees outside the vertical's own directory),
`python_requirements` (shown, never installed), `tags`, `skill_parents`,
`has_skills`, `routing_path`, `min_argus`, `maintainers`.

`examples/verticals/build_local_catalog.py` produces both files for a directory
under `examples/verticals/argus_verticals/`:

```bash
python examples/verticals/build_local_catalog.py lab_notebook --version 0.1.0 --out /tmp/lab-store
```

```
archive : /tmp/lab-store/lab_notebook-0.1.0.zip (3099 bytes)
catalog : /tmp/lab-store/catalog.json
install : ARGUS_VERTICAL_CATALOG=/tmp/lab-store/catalog.json argus verticals install lab_notebook
```

The catalog it wrote:

```json
{
  "schema": 1,
  "verticals": {
    "lab_notebook": {
      "name": "lab_notebook",
      "version": "0.1.0",
      "module": "argus_verticals.lab_notebook.stages",
      "paths": ["argus_verticals/lab_notebook"],
      "purpose": "small measurement tasks on this machine: run the measurement, record how it was run, and write a notebook entry another person can reproduce",
      "has_skills": true,
      "archive": {
        "file": "lab_notebook-0.1.0.zip",
        "url": "file:///tmp/lab-store/lab_notebook-0.1.0.zip",
        "sha256": "5b36…b8e0",
        "size": 3099
      }
    }
  }
}
```

The `sha256` and `size` are checked byte-for-byte at install; regenerate the
catalog whenever the zip changes.

## Step 4: install it and confirm Argus sees it

Point the store at the local catalog. A local catalog turns its directory into
an offline mirror: an archive named in the catalog and sitting next to it is
used without any download.

```bash
export ARGUS_VERTICAL_CATALOG=/tmp/lab-store/catalog.json
argus verticals install lab_notebook
argus verticals list
argus verticals info lab_notebook
```

What this checkout printed (the install was done into a throwaway
`ARGUS_SKILL_HOME` so the machine's real store stayed untouched):

```
lab_notebook: install started
  [  0%] lab_notebook: starting
  [100%] lab_notebook: install finished
lab_notebook: install finished
```

```
name                       kind       version  installed  enabled  update  purpose
...
lab_notebook               installed  0.1.0    0.1.0      yes      -       small measurement tasks on this machine: run the measurement
```

```
name                 lab_notebook
kind                 installed
purpose              small measurement tasks on this machine: run the measurement, record how it was run, and write a notebook entry another person can reproduce
version              0.1.0
installed_version    0.1.0
enabled              True
actions              disable, uninstall
operation            install done: install finished
```

The files landed at `<ARGUS_SKILL_HOME>/verticals/argus_verticals/lab_notebook/`
next to the store's `registry.json`, and the registry advertises the vertical
with origin `store`, its two skills found automatically next to `stages.py`:

```
advertised: True
origin: store | skills_root: .../verticals/argus_verticals/lab_notebook/skills
skills: ['engineer/measurement-record.md', 'reviewer/measurement-review.md']
stages: ('measure', 'report') | gate: none | mode: staged
```

From here the Manager can choose `lab_notebook` for a task whose text matches
its purpose line; there is no flag to force a vertical (`ARGUS_SKILL_VERTICAL`
is a legacy name with no authority), and the choice is saved per project in
`.argus/PIPELINE_STATE.json`. To try it, start a project with an objective in
the vertical's own words, for example "measure how long `python -c 'import
torch'` takes on this machine, repeat it, and write a notebook entry with the
rerun command", and check `pipeline : vertical=lab_notebook` in
`argus --status`.

`argus verticals disable lab_notebook` hides it again without removing the
files; `argus verticals remove lab_notebook` deletes them (it refuses while a
local project's `PIPELINE_STATE.json` still names the vertical, unless you
pass `--force`).

## Publishing to the shared store

The default catalog is the `catalog.json` attached to the latest release of
[Argus-AiTeam/argus-verticals](https://github.com/Argus-AiTeam/argus-verticals);
each vertical is one directory in that repository, and its release workflow
builds the zips and the catalog (`scripts/build_catalog.py` there). Publishing
therefore means opening a pull request that adds
`argus_verticals/<name>/` to that repository, with the same layout as here. The
store on every user's machine only accepts archives from an allow-listed host
(`github.com` and its release asset hosts), so a catalog you host elsewhere
must be reached through `ARGUS_VERTICAL_CATALOG` as a local file, which is what
the steps above do.

<!-- unverified: the exact contribution procedure of the argus-verticals repository (branch names, review rules, the fields its build_catalog.py derives automatically) was not checked against that repository; it is described only from what this repository's store code and docs/vertical-store.md say about the catalog it consumes. -->

Two other ways exist to run a vertical without the store, useful during
development of a bigger one: `pip install` a package that declares the entry
point `argus.verticals` (`name = "argus_verticals.<name>.stages"`), which wins
over a store copy of the same name; or install it as a managed workbench
plugin. A name that collides with a built-in is ignored from every source.

## Checks before you share it

- The contract check in step 1 passes.
- Every skill header has quoted `name` and `description` values. The
  repository's own skill writer (`argus/skills/store.py`) emits exactly that
  shape, JSON-quoted, because an unquoted description containing a colon does
  not survive a YAML parse; `tests/skills/test_skill_frontmatter_integrity.py`
  checks the in-tree verticals for the same reason.
- `argus verticals install` from a local catalog succeeds and
  `argus verticals list` shows the row as `installed` and enabled.
- One real task ran through it and the Reviewer's verdicts referred to your
  checklist ids (they appear in the round events in `events.jsonl`).

The example under `examples/verticals/` is not scanned by
`tests/skills/test_vertical_plugins.py` (that file builds synthetic plugins in
memory), so adding a vertical there cannot change the test's result. On this
machine one test in that file fails before and after these changes for a
local reason: a pip-installed `argus_verticals` package in the venv makes
`test_store_verticals_are_discovered_with_origin_store` see two package paths
instead of one.
