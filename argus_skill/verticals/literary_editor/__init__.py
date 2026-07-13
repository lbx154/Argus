"""literary_editor vertical — package marker.

Not a new creative genre: an EDITING service over existing literary text. It reuses
the framework Reviewer + revise capability (no new reviewer agent) and exposes the
editing task types the shared Task Envelope already defines — rewrite / expand /
polish / proofread / critique — each of which the envelope already requires to carry
a source reference. It consumes the same shared contracts (Task Envelope / Review /
Artifact / Provenance).

Its machine layer is EDIT DISCIPLINE, which is genuinely deterministic: segments the
operator marked must-not-break must survive verbatim; a critique must not silently
rewrite; a proofread must not become a rewrite; an expand must actually add. Whether
the edit is GOOD is live-reviewer.
"""
from __future__ import annotations
