"""Post-freeze artifact audit. These fixtures are not benchmark tasks or scores."""
import csv
import json
import subprocess
import tempfile
from pathlib import Path

import openpyxl

def inspect(path, limit=5):
    result = subprocess.run(['python', '/runtime/inspect_data.py', str(path), '--limit', str(limit)],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    multiline = root / 'multiline.csv'
    multiline.write_text('id,note\n0007,"two\nlines"\n0008,end\n')
    preview = inspect(multiline)
    physical = []
    with multiline.open(newline='') as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            physical.append({'id': row['id'], 'physical_end_line': reader.line_num})
    duplicate = root / 'duplicate.csv'
    duplicate.write_text('id,value,value\nA,0,9\n')
    duplicate_preview = inspect(duplicate)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = 'Values'
    sheet.append(['id', 'formula', 'zero'])
    sheet.append(['A', '=2+3', 0])
    second = workbook.create_sheet('Later')
    second.append(['id', 'value'])
    second.append(['LATER_SENTINEL', 8])
    xlsx = root / 'formulas.xlsx'
    workbook.save(xlsx)
    workbook_preview = inspect(xlsx, 1)
    result = {
        'purpose': 'Post-freeze functional/knowledge audit, never included in benchmark score or fed to evaluation agents',
        'csv_multiline': {'preview': preview, 'physical_positions': physical,
                          'finding': 'source_id is a logical record index, not a physical CSV line number; the generated Wiki wording is too strong'},
        'csv_duplicate_headers': {'preview': duplicate_preview,
                                  'finding': 'Duplicate column names overwrite an earlier value, including zero; the Wiki explicitly notes this for XLSX but not CSV'},
        'xlsx_formula_and_sheet_limit': {'preview': workbook_preview,
                                        'finding': 'Uncached formulas appear as null, zero is preserved, and later-sheet row data is omitted at the global limit; these limits are documented in the Wiki'},
    }
    Path('/audit/quality-probes.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'audit_written': True, 'benchmark_scores_modified': False}))
