"""literary_editor vertical — package marker.

Not a new creative genre: an EDITING service over existing literary text. It reuses
the framework Reviewer + revise capability (no new reviewer agent) and exposes the
editing task types the shared Task Envelope already defines — rewrite / expand /
polish / proofread / critique — each of which the envelope already requires to carry
a source reference. It consumes the same shared contracts (Task Envelope / Review /
Artifact / Provenance).

Its machine layer checks only non-empty output and explicit must-not-break segments.
Whether the edit is good or exceeded its semantic mandate is live Reviewer judgment.
"""
from __future__ import annotations
