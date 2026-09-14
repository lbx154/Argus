---
name: Native pivot workbooks with openpyxl
description: Create refreshable Excel pivot definitions from a regular source table when a desktop spreadsheet application is unavailable.
---

## Procedure

1. Write the source records to one rectangular worksheet range and add an Excel table with stable, unique column names.
2. Compute and write the visible summaries before adding pivot definitions. These stored cells let readers and data-only consumers use the report without refreshing it.
3. Build one `openpyxl.pivot.cache.CacheDefinition` whose worksheet source is the source-table range. Give categorical row and column fields ordered `SharedItems`; include a `Missing` item when blanks occur.
4. Build each `TableDefinition` with a unique name, a `Location` matching the stored summary, row and column `PivotField` items aligned to the cache's shared-item indices, and the requested `DataField` aggregation.
5. Assign the same cache to related pivots and attach each definition with `worksheet.add_pivot`. Set the cache to refresh on load while retaining the visible summaries as the immediate result.
6. Validate the saved file in a fresh process: reopen it normally and data-only, check worksheet names and stored values, confirm one pivot definition per requested pivot sheet, verify every pivot points to the source range, and test the ZIP container. Do not save the workbook again after this check unless a change is required, because unsupported readers may discard or rewrite pivot metadata.
