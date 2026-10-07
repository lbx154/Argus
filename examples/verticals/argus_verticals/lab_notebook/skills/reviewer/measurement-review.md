---
name: "Measurement Review"
description: "How to review a measurement task: open the raw output files, recompute one aggregate, and confirm the rerun command is complete before accepting."
---

# Measurement review

Do not accept the Engineer's summary as evidence. Open the raw output files
named in `NOTEBOOK.md` and recompute at least one aggregate yourself.

Confirm that the rerun command in `NOTEBOOK.md` names the script, its arguments
and the working directory. A command that cannot be pasted and run is
incomplete.

Return `continue` when a number has no raw file behind it, and `done` only when
every checklist item of the current stage has evidence you inspected.
