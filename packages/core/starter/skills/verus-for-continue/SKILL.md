---
name: verus-for-continue
description: Reproduce Verus for-loop continue rejection separately from iterator-model and contract failures.
---
Run `python3 check.py VERUS OUTPUT_DIRECTORY`. The two tiny primitive loops differ only by a conditional continue versus an else block, and both promise exactly three counted iterations. The script requires the literal unsupported-feature diagnostic for continue and a checked positive else control. On a future toolchain supporting continue, this classifier intentionally fails: re-evaluate the implementation rather than recording an old blocker.

Case: t25's actual Base64 decode_suffix padding branch uses continue in a slice-enumeration loop. After an isolated vstd Enumerate interface built with 2059 verified and no errors, the actual body still hit this rejection in vir/src/sst_to_air.rs. The range-loop control reproduces it without Enumerate, tables or decoding contracts. Removing the continue via an else block is a diagnostic program change; these controls do not establish correspondence, license rewriting a production body, prove the suffix contract, or justify disabling the compiler rejection. A support remedy must preserve ghost iterator advancement and loop-entry/decreases checks on continuation edges.
