---
name: "Measurement Record"
description: "How to run a small measurement so the result can be checked and rerun: save raw output, repeat, report the spread, and record the exact command."
---

# Measurement record

Run the measurement from a script saved in the work directory, never from an
interactive shell you cannot show later. Write the raw output to a file under
`results/` before computing any aggregate.

Repeat the run. Report the median (or mean) together with the spread (min/max or
standard deviation) and the number of repeats. A single run is not a result.

Record in `NOTEBOOK.md`:

- what was measured and on which hardware
- the exact command to rerun it
- the aggregate, the spread and the number of repeats
- the path of the raw output every number came from
