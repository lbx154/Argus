# Results

- Source smoke batch summary (12 rows across 6 codex / 6 argus trials) was generated from the ignored scratch run root and mirrored here for durability. The export normalizes prompt-only rows that had `needs_human=False` but blank or failed rescue metadata so they do not remain falsely marked as non-zero-touch.
- Batch-level aggregate from `benchmarks.prompt_only_tb2.summarize_runs`:
condition,rows,accepted_rate,mean_reward,needs_human_rate,avg_cost_usd,avg_wall_minutes,timeouts
argus,6,0.833,0.833,0.000,0.149376,7.14,0
codex,6,1.000,1.000,0.167,0.084199,4.16,0

- Per-trial data lives in summary.tsv; job transcripts and verifier logs live under jobs/raw/.
- The summary JSON and verification summary reflect the normalized zero-touch bookkeeping, so rows without a real manual intervention keep `zero_touch_success=True`.
