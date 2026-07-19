# Chart verification receipt

`verify-charts: 01_argus_teaser.svg | type=heterogeneous KPI-card small multiples | mode=manual-verify | scales=0–100%; .960–.966 BPB; .984–.989 BPB; 2+5 placements; 79.5–80.5 s; 0–30 gap | formulas=checked×7 | svg=unchanged`

The page contains seven independently scaled mini-bars rather than one shared plot. Bar lengths were checked against the visible track widths:

- SWE-Bench Pro: `105×0.59=61.95`, `105×0.78=81.90`.
- AARRI-Bench: `105×0.768=80.64`, `105×0.683=71.72`.
- nanochat B200: `105×(value−0.960)/(0.966−0.960)` gives `63.00` and `80.50`.
- nanochat H100: `105×(value−0.984)/(0.989−0.984)` gives `31.50` and `81.90`.
- SOL-ExecBench: `145×2/7=41.43`, `145×5/7=103.57`.
- nanoGPT speedrun: `145×(value−79.5)/(80.5−79.5)` gives `39.15` and `98.60`.
- Math-reasoning data: `99×value/30` gives `92.40`, `68.74`, `27.49`, and `20.63`.

Lower-is-better cards use a shorter bar for the better result and print the truncated axis range directly in the card.
