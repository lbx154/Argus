"""The detached 7x24 process that keeps one Project's supervisor running.

Layer: process

Belongs here: the life worker (``life_worker`` and its ``_life_worker_*``
phases: admission, boot, identity, runtime context, run), detached and
foreground process lifecycle (``process``, ``spawn_helper``), the durable
command protocol and status sidecar (``commands``, ``protocol``, ``state``),
blue/green handoff (``handoff``, ``config``) and semantic progress health
(``health``). The daemon owns process lifetime and admission; it decides
whether a supervisor runs, not what a mission does.

Does not belong here (and where it goes): mission execution (``life`` and
``apps._runtime*`` today, ``mission_runner/`` after phase 5), teammate pool
plumbing (``team``), and HTTP/TUI presentation (``webapi``, ``apps``).
``daemon.spawn_helper`` is re-entered by argv (``python -m``), so renaming a
module here without a shim strands every already-running daemon's restart.
"""
