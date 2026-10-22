# data_report

Turns one tabular dataset (CSV, Excel, Parquet) into a short analysis report whose
every number can be recomputed from a script in the project.

- **Stages:** `understand` → `analyze` → `report`.
- **Does not:** build dashboards, train predictive models, or combine several
  unrelated datasets; use `research` or `software` for those.
- **Skills:** `engineer/data-report-analysis.md`, `reviewer/data-report-review.md`.

This is the worked example for the
[Build your own vertical](../../../../site/build-a-vertical.html) tutorial.

```bash
python -m pip install -e .          # into the environment that runs Argus
argus verticals info data_report
```
