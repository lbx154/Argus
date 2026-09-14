---
title: Argus-Pi bounded data inspection runtime
description: Verified input/output contract, behavior, provenance, assumptions, and
  limits of the learned read-only CSV, XLSX, and PDF inspector.
---

# Argus-Pi bounded data inspection runtime

## Source and version

These facts are derived from the supplied runtime source `/learning/pi/inspect_data.py` and the supplied activation metadata under `pi/history`: **version 1**, activated at `2026-09-14T06:55:57.750Z`, tool call `call_doPrexf97QXoIrATacMfhQ5u`. The activation contract records `passed: true`, formats `.csv`, `.xlsx`, and `.pdf`, missing-input error handling, and read-only operation. Its evidence references are `call_xVGyQEhWtShWTqZwQdA1MgwC`, `call_4afoHoxS1IoCKfnTJqYkk70r`, and `call_mh7Y5QSixISNOrJ89RBicpb3`. No more specific history-file path was supplied, so none is asserted here.

## Invocation and output contract

Invoke the script with one positional file path and optional `--limit N`; the default limit is 20. A negative limit is rejected by `argparse` before inspection.

The program writes one JSON object to standard output using Unicode-preserving serialization and returns:

- exit `0` for a supported file successfully inspected;
- exit `2` for a missing/non-file path or unsupported extension, with `{"kind":"error","items":[],"path":...,"error":...}`;
- exit `1` for an exception raised during a supported reader, using the same error shape and an error string prefixed by the exception class.

Supported formats are selected only by a case-insensitive filename suffix: `.csv`, `.xlsx`, and `.pdf`. Successful results include `kind`, `items`, and `path`, plus format-specific metadata. The item limit is global per file, not per worksheet or page. `--limit 0` returns no items while still opening/parsing enough of the source to produce metadata according to the selected reader.

## Format behavior

### CSV

- Opens with `utf-8-sig`, so a UTF-8 BOM is tolerated.
- Uses Python `csv.DictReader` with its default dialect assumptions.
- Treats the first parsed row as field names.
- Returns `columns` and bounded row items shaped as `{"source_id":"row:<physical-data-row>","row":{...}}`; the first data row is labeled `row:2`.
- String values longer than 4,000 characters are truncated and suffixed with an ellipsis.

### XLSX

- Uses `openpyxl.load_workbook(..., read_only=True, data_only=True)` and closes the workbook in a `finally` block.
- Returns all workbook sheet names in `sheets`, but row items only until the file-wide limit is reached.
- Assumes the first row of every nonempty worksheet is a header row. Header values are stringified; blank header cells become `column_<1-based-index>`.
- Data rows are labeled `<sheet-title>!<row-number>` and include the sheet title.
- Because `data_only=True` is used, formula cells expose cached formula results, not formula text. If a producer has not stored a cached value, inspection may return `null` even when a formula exists.
- Duplicate header text is not disambiguated; later cells with the same dictionary key can overwrite earlier values in a returned row.

### PDF

- Uses `pypdf.PdfReader` and `page.extract_text()`.
- Returns total page count in `pages` and bounded items shaped as `{"source_id":"page:<n>","page":n,"text":...}`.
- Empty or unextractable page text becomes an empty string.
- Extracted text longer than 4,000 characters per page is truncated and suffixed with an ellipsis.

## Resource and safety properties

Before optional spreadsheet dependencies are imported, the runtime sets `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and `NUMEXPR_NUM_THREADS` to `1`. The activation rationale states this avoids oversized numerical-library thread pools in constrained inspection environments. The implementation performs no write or save operation on inspected sources and was activated with a read-only contract.

## Limitations and assumptions

- Bounded output is a sample, not proof about unreturned rows, sheets, or pages. The XLSX limit can be exhausted on an early sheet, and the PDF limit selects leading pages only.
- CSV handling assumes UTF-8-compatible text and the default `csv` dialect; encoding detection, delimiter selection, malformed-row reporting, and non-CSV text are unsupported.
- XLSX handling assumes first-row headers and cached formula values. It does not inspect formulas, styles, charts, pivots, macros, external links, hidden-state semantics, or workbook recalculation behavior.
- Only `.xlsx` is supported; `.xls`, `.xlsm`, `.ods`, and similarly named formats are rejected by suffix.
- PDF text extraction is not OCR. Scanned pages may yield empty text, and reading order, tables, columns, and glyph mapping may be inaccurate.
- The runtime clips only string values; JSON serialization converts unsupported scalar types through `default=str`, which can lose type precision.
- File type is trusted from the suffix and then parsed; content sniffing is not performed.
- The supplied activation verifies the recorded contract, not every malformed, encrypted, very large, duplicate-header, formula-cache, or cross-library edge case.
