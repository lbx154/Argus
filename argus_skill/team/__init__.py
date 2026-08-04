"""Argus Agent Teams — domain-agnostic rolling-pool plumbing.

A lead engineer writes independent tasks to a durable board.  The daemon's
resident Curator exclusively owns teammate process lifetime, while result
shards and a deterministic leaderboard carry measured outcomes back to the
lead.  Research judgment—whether to form a team, how to split it, and how to
synthesise the result—stays in the engineer skill rather than this package.
"""
from __future__ import annotations
