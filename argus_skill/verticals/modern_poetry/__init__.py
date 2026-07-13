"""modern_poetry vertical — package marker.

The THIRD literary vertical: modern free verse / prose poems (zh or en). It
consumes the same four shared contracts as fiction and classical_poetry, but it
has NO metrical machine layer — free verse is not bound by 平仄/韵. Its
deterministic layer is therefore HONESTLY THIN: only declared HARD CONSTRAINTS
(line count, banned-cliché list, language, non-empty) are machine-checked; imagery,
lineation, tone and cliché-beyond-the-list are live-reviewer judgements, never
mechanized.
"""
from __future__ import annotations
