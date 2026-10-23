---
name: "Authorized Engagement Planning"
description: "Plan an offensive mission strictly inside the declared engagement: window, targets, methods, and impact flags."
---

# Authorized Engagement Planning

Plan red-team work as an authorization problem first and a technical problem
second. Nothing planned may exceed the declaration.

## Method

1. Read `<project>/SECURITY_ENGAGEMENT.json` before proposing any task. The
   `authorize` stage cannot close without it, and the gate re-checks it
   mechanically: operator, current window, every mission target covered by a
   declared entry (`source_audit` or `host` kind, with ref and authorization),
   known method categories, and impact flags explicitly matching the methods.
2. Map each planned work item to a declared target and method. An item that
   touches an undeclared target, an expired window, or a method whose impact
   flag is false is out of scope: route it back to the operator, never plan
   around the gate.
3. Schedule evidence by canary as the default proof of access for every
   target: a harmless demonstrated action (read a planted marker, list a
   directory). A destructive demonstration requires the operator's explicit
   impact flag, and even then plan it last, smallest, and reversible.
4. Split the mission across the stages honestly: `authorize` (declaration
   valid and targets confirmed reachable), `attack` (findings with
   reproductions), `report` (independent reproduction and calibrated
   severity).

## Evidence

The declaration gate output, the authorization reference per target, and the
planned method list — nothing that depends on targets or methods the
declaration does not name.
