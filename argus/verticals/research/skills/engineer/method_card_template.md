---
name: "METHOD.md template"
description: "Template body for the project-root METHOD.md method card. Copy everything below the front matter to METHOD.md and fill it from the selected route; see engineer/method-card.md for how. Only the method itself is written by hand; status, tests, reused code, hyperparameters and history are derived by the host."
---

# <Method name>

<One paragraph: the method as the paper will claim it. What it takes in,
what it changes relative to the strongest existing method, and what the
paper will say it achieves. Quote the selected route where it states this.>

## Components

| Component | The idea prescribes | Notes |
|---|---|---|
| <component 1> | "<quoted sentence from the route section>" | <empty, or a deliberate simplification and why> |
| <component 2> | "<quoted sentence>" | |

Spell each component name exactly as the `@pytest.mark.component("<name>")`
marker on its tests under `tests/spec` spells it; the host joins the two.

## Protocol

Copied from the selected route; every deviation is named on its own line.

- Datasets: <as the route names them, with splits>
- Baselines: <as the route names them; official implementation under `third_party/`>
- Seeds or repeats: <count and how chosen>
- Metrics: <as the route names them; evaluator and version>
- Configs: <`configs/<file>.yaml` the claim-bearing runs use>
- Deviations: <none> / <what differs, why, and what it costs the claim>

## What would falsify the claim

<The observation, on which data and at which scale, that would make the
paper's central claim false. Name the knockout or comparison that would
show it.>

<!-- Nothing below this line is written by hand. Implementation status per
component, the tests that prove it, reused code with pinned revisions,
hyperparameters with their `# why:` reasons, and the change history are
derived by the host from the code, the markers on tests/spec, the config
files and git, and shown beside this card in Atlas and to the Reviewer. -->
