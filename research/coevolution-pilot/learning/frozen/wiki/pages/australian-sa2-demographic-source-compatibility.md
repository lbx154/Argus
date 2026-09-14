---
title: Australian SA2 demographic source compatibility
description: Join coverage and suppressed-value limits in the supplied regional population and personal-income sources.
---

The regional population PDF at `/root/population.pdf` contains 2,454 distinct nine-digit SA2 codes. The `Data` sheet in `/root/income.xlsx` contains 2,450 distinct SA2 codes, all of which occur in the PDF. The four population-only records belong to Other Territories. Consequently, an income-led combined source has 2,450 records and does not represent Other Territories in state summaries.

The income sheet uses the text `np` in `EARNERS`, `MEDIAN_INCOME`, and `MEAN_INCOME` for 45 records. These values are publication suppressions, not zeros. Arithmetic and median-income quartile assignment should therefore leave the corresponding calculated values blank and derive boundaries from the 2,405 numeric median-income observations.

These facts were measured directly from `/root/population.pdf` and `/root/income.xlsx` during the bounded inspection recorded in observations `call_JGnwT2AbJ7Nu3Zr88GoeUJBB`, `call_LtoBgUS6VH8aBYbi9tcZXdC0`, and `call_lJuFGk61sEhLFUcUOV6Zi9LL`.
