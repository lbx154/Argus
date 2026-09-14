"""``python -m argus_skill``: the pre-rename spelling of ``python -m argus``.

With the alias finder installed by :mod:`argus_skill` the interpreter normally
resolves ``argus_skill.__main__`` to :mod:`argus.__main__` directly; this file
covers the paths that bypass the finder (running the file as a script, or a
frozen build that lists ``argus_skill.__main__`` as a hidden import).
"""
from __future__ import annotations

import sys

from argus.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
