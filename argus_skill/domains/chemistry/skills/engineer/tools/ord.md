---
name: Open Reaction Database I/O
description: Read and validate Open Reaction Database records with ord-schema while preserving reaction provenance and outcome structure.
category: chemistry-tool-ord
version: 1
---

`ord-schema` provides protobuf schemas and I/O; public records are distributed
separately in `ord-data`. Use the official repositories:
<https://github.com/open-reaction-database/ord-schema> and
<https://github.com/open-reaction-database/ord-data>.

Install the schema package in an isolated environment only after checking its
current Python compatibility. Probe by importing `ord_schema.proto.reaction_pb2`
and parsing one retained official example.

Preserve dataset commit/release, record ID, source, reaction roles, quantities,
units, conditions, workup, products, yields, and parser errors. A recorded yield
is retrospective evidence under its original conditions, not validation of a
newly proposed reaction.
