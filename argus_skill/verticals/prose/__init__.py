"""prose vertical — package marker.

The FOURTH literary vertical: literary/narrative prose (抒情散文/叙事散文/随笔/回忆,
zh or en). Consumes the same four shared contracts as the others. Its machine
layer is HONESTLY thin — a prose_state STRUCTURE schema plus declared hard
constraints (language/paragraph-count/banned-words). The things prose actually
lives or dies on — whether observation is concrete, whether fact and memory are
kept distinct, whether paragraphs move, whether the ending earns its close — are
LIVE-reviewer judgements, never mechanized.
"""
from __future__ import annotations
