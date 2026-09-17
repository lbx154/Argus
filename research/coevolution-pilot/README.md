# Co-evolution pilot archive

The 2026-09-14 SkillsBench pilot and its full, immutable evidence are archived at
commit [`bfeb218d2`](https://github.com/lbx154/Argus/tree/bfeb218d289b572f0357906580908dbaf5251cc5/research/coevolution-pilot).
Read its [report](https://github.com/lbx154/Argus/blob/bfeb218d289b572f0357906580908dbaf5251cc5/research/coevolution-pilot/report.md).

The original result remains: all four conditions passed 1/3 heldout tasks. Joint
use reduced calls from 33 to 23, tokens by 2.8%, and estimated cost by 0.9%.
The reference inconsistency and defects in the generated Skills/Wiki were retained;
this small experiment did not establish an accuracy improvement.

Daily Pi tasks now use the [production runtime integration](../../docs/pi-runtime-learning.md).
The experimental controller, subscription gateway, report generators and duplicated
artifacts were removed from the current tree. The historical commit preserves the
original protocol, source, generated artifacts, scores and losslessly compressed
trajectories for reproduction. The production implementation has its own tests;
the historical benchmark scores do not evaluate its newer execution contract.
