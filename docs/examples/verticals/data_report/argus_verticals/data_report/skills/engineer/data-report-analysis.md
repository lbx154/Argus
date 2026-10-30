---
name: "Data Report Analysis"
description: "How to analyze one dataset so that the report's numbers can be trusted and recomputed."
---
# Data report analysis

Look at the data before you summarize it. Open the real file, count rows, and
read a sample of raw values; profiling output you have not looked at is not
understanding.

When a number surprises you, suspect the pipeline first: an encoding, a wrong
delimiter, duplicated rows, a unit mismatch, or a filter that silently dropped
data explains more surprises than a real effect does.

Choose the analysis by the question the operator asked, not by habit. A mean
is the wrong summary for a skewed column; a correlation says nothing about
cause. Say why the method you chose fits this data.

Keep every quoted number reproducible: write it from a script, save the output,
and reference that output in the report. If a number cannot be recomputed, do
not report it.

Report what the data cannot answer. Missing segments, small samples, and
measurement limits belong in the report as plainly as the findings do.
