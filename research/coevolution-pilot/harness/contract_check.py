"""Small transparent API checks for an evolved inspection tool, not benchmark scoring."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl
from reportlab.pdfgen.canvas import Canvas

helper = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='pi-inspector-contract-') as temporary:
    root = Path(temporary)
    csv = root / 'sample.csv'
    csv.write_text('record_id,amount,note\n0007,3.50,alpha\n0008,0,beta\n0009,,gamma\n')
    xlsx = root / 'sample.xlsx'
    workbook = openpyxl.Workbook()
    workbook.active.title = 'ContractSheet'
    workbook.active.append(['identifier', 'value'])
    workbook.active.append(['XLSX_SENTINEL', 0])
    workbook.save(xlsx)
    pdf = root / 'sample.pdf'
    canvas = Canvas(str(pdf))
    canvas.drawString(30, 800, 'PDF_SENTINEL 123.40')
    canvas.save()
    checked = []
    for path, expected in [(csv, ['0007', 'alpha']), (xlsx, ['ContractSheet', 'XLSX_SENTINEL']),
                           (pdf, ['PDF_SENTINEL'])]:
        before = path.read_bytes()
        result = subprocess.run([sys.executable, str(helper), str(path), '--limit', '3'],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr[-1500:]
        value = json.loads(result.stdout)
        assert isinstance(value, dict) and isinstance(value.get('items'), list)
        assert value.get('kind') in ['csv', 'xlsx', 'pdf', 'text']
        assert len(result.stdout) < 12000, 'A preview tool must bound its output'
        for token in expected:
            assert token in result.stdout, f'Preview lost expected source content: {token}'
        assert path.read_bytes() == before, 'Inspection modified its input'
        checked.append(path.suffix)
    missing = subprocess.run([sys.executable, str(helper), str(root / 'missing.csv'), '--limit', '3'],
                             capture_output=True, text=True, timeout=15)
    assert missing.returncode != 0, 'Missing input must not be reported as a successful empty preview'
    print(json.dumps({'passed': True, 'formats': checked, 'missing_input_error': True, 'read_only': True}))
