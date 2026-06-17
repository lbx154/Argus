"""Argus Agent Teams — domain-agnostic team plumbing (harness layer).

A *lead* engineer decomposes a mission into file-ownership-disjoint
subtasks and fans out autonomous *teammate* engineers that coordinate
through a shared task board and a per-recipient mailbox. This package is
the dumb pipe: atomic+locked storage, the task board, the mailbox, the
roster, and per-teammate git-worktree isolation. All research judgment
(whether to form a team, how to split, how to synthesise) lives in the
engineer skills, never here.
"""
from __future__ import annotations
