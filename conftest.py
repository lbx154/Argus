"""Worktree-local conftest: ensure tests import from *this* worktree.

The shared venv installs ``_ArgusLatestFinder`` in ``sys.meta_path``, which
intercepts ``import argus_skill`` and routes it to the main checkout
(``argus-skill-latest/``).  When running tests inside a worktree we need the
worktree's own source, so we remove that interceptor and prepend the worktree
root to ``sys.path`` before any test collection.
"""
from __future__ import annotations

import sys
from pathlib import Path

_WORKTREE_ROOT = str(Path(__file__).parent.resolve())

# Remove the main-checkout meta-path interceptor so PYTHONPATH / sys.path
# resolution takes over.
sys.meta_path = [
    f for f in sys.meta_path if type(f).__name__ != "_ArgusLatestFinder"
]

# Invalidate any previously cached argus_skill imports (e.g. from site init).
for key in list(sys.modules):
    if key == "argus_skill" or key.startswith("argus_skill."):
        del sys.modules[key]

# Prepend the worktree root so it wins over the path entry added by the
# editable .pth file.
if _WORKTREE_ROOT not in sys.path:
    sys.path.insert(0, _WORKTREE_ROOT)
