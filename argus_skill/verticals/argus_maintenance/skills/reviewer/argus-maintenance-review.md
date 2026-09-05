---
name: "Argus Maintenance Review"
description: "Review an Argus maintenance patch for real simplification, reuse, decoupling, and verified behavior."
---

# Argus maintenance review

- Read the complete diff and affected callers.
- Reject renaming or moving code that does not simplify behavior.
- Reject new wrappers, knobs, registries, or fallback paths without a current user.
- Check that core depends on the vertical contract rather than a concrete vertical.
- Reject any pip install of the framework package or into user site-packages /
  `~/.local/bin`; extra dependencies belong in a task-local venv or `--target` directory.
- Run the decisive tests and build checks.
- Judge behavior and ownership, not warning-count reduction.
