---
name: ChemCrow Integration Reference
description: Inspect or reuse public ChemCrow tool patterns without claiming the public package reproduces the published chemistry-agent system.
category: chemistry-reference-chemcrow
version: 1
---

Use <https://github.com/ur-whitelab/chemcrow-public>. The public package may be
installed with `pip install chemcrow`, but its README states that API restrictions
omit tools used in the paper.

Treat it as a tool-composition reference. Before execution, inventory available
tools, API credentials and terms, network access, prompt/model versions, and
sandbox boundaries; run one non-destructive probe per enabled tool.

Retain tool calls, raw responses, failures, model/backend settings, and costs.
Never cite public-package behavior as reproduction of the reported system or
allow a language model to bypass chemistry or laboratory safety controls.
