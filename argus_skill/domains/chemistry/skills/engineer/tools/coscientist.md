---
name: Coscientist Integration Reference
description: Use the public Coscientist repository as supporting evidence and an architecture reference, not as a portable autonomous laboratory.
category: chemistry-reference-coscientist
version: 1
---

Inspect <https://github.com/gomesgroup/coscientist>. The repository contains
supporting data and a simple implementation; it does not provide a complete,
portable robot-laboratory deployment.

Identify exactly which planning, search, code, or hardware-integration component
is reusable. Rebuild and validate local adapters against the authorized facility
rather than assuming paper hardware and services exist.

Physical commands require authenticated, pre-authorized instrument capabilities
with instrument-side limits and interlocks. Retain decisions, tool calls,
hardware commands, telemetry, failures, and human/facility authorization.
