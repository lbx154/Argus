"""Per-project declarative knowledge wiki.

See docs/IDEA_WIKI_DESIGN.md for the design. Module surface:

- schema:    dataclasses + frontmatter (de)serialization
- store:     file I/O for sources/ and pages/
- index:     regenerate queries/ from frontmatter
- validate:  schema + link integrity
- bootstrap: initialize .autors/<project>/wiki/ from templates
"""
from __future__ import annotations

from .ingest import ingest_lit_matrix, ingest_refs_bib  # noqa: F401
