#!/usr/bin/env python3
"""Bounded, read-only inspection of CSV, XLSX, and text PDFs."""
import argparse, csv, json, os, sys
from pathlib import Path


def csv_items(path, limit):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        items = []
        for row_no, row in enumerate(reader, start=2):
            if len(items) >= limit:
                break
            # Keep empty strings and textual zeros exactly as parsed.
            items.append({'source_id': f'row:{row_no}', 'row': {k: row.get(k, '') for k in fields}})
    return {'kind': 'csv', 'items': items, 'columns': fields}


def xlsx_items(path, limit):
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError('XLSX support requires openpyxl') from exc
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    items = []
    sheets = []
    try:
        for ws in wb.worksheets:
            sheets.append(ws.title)
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if header is None:
                continue
            columns = [str(v) if v is not None else f'column_{i}' for i, v in enumerate(header, 1)]
            for row_no, values in enumerate(rows, start=2):
                if len(items) >= limit:
                    break
                values = tuple(values)
                items.append({'source_id': f'{ws.title}!{row_no}', 'sheet': ws.title,
                              'row': {columns[i]: (values[i] if i < len(values) else None)
                                      for i in range(len(columns))}})
            if len(items) >= limit:
                break
    finally:
        wb.close()
    return {'kind': 'xlsx', 'items': items, 'sheets': sheets}


def pdf_items(path, limit):
    texts = []
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, 1):
                if len(texts) >= limit:
                    break
                texts.append({'source_id': f'page:{page_no}', 'page': page_no,
                              'text': page.extract_text() or ''})
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError('PDF support requires pdfplumber or pypdf') from exc
        reader = PdfReader(str(path))
        for page_no, page in enumerate(reader.pages, 1):
            if len(texts) >= limit:
                break
            texts.append({'source_id': f'page:{page_no}', 'page': page_no,
                          'text': page.extract_text() or ''})
    return {'kind': 'pdf', 'items': texts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be non-negative')
    path = Path(args.path)
    if not path.is_file():
        print(json.dumps({'kind': 'error', 'items': [], 'error': f'file not found: {path}'}, ensure_ascii=False))
        return 2
    suffix = path.suffix.lower()
    loaders = {'.csv': csv_items, '.xlsx': xlsx_items, '.pdf': pdf_items}
    if suffix not in loaders:
        print(json.dumps({'kind': 'error', 'items': [], 'error': f'unsupported format: {suffix or "(none)"}'}, ensure_ascii=False))
        return 2
    try:
        result = loaders[suffix](path, args.limit)
        result['path'] = str(path)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({'kind': 'error', 'items': [], 'error': f'{type(exc).__name__}: {exc}', 'path': str(path)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
