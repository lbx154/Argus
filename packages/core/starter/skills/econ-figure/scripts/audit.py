#!/usr/bin/env python3
"""Audit a figure PDF against the house style of economics and management journals.

    python3 audit.py fig.pdf [more.pdf ...] [--width-cm 15.5]

Works on any PDF, not only ones this skill rendered. Prints PASS / WARN / FAIL lines; exits 2 on any FAIL.
Needs PyMuPDF (import pymupdf or fitz).
"""
import argparse
import re
import statistics
import sys

try:
    import pymupdf
except ImportError:  # older PyMuPDF
    import fitz as pymupdf

STATISTICS = re.compile(r'\*{1,3}|\bp\s*[<=>≤]|\bt\s*=|n\.\s?s\.|[tp] ?值')


def audit(path, width_cm=None):
    findings = []
    add = lambda level, code, message: findings.append((level, code, message))
    document = pymupdf.open(path)
    if len(document) != 1:
        add('FAIL', 'pages', f'{len(document)} pages; a figure is one page')
    page = document[0]
    width = page.rect.width / 72 * 2.54
    if width_cm and abs(width - width_cm) / width_cm > 0.03:
        add('FAIL', 'width', f'figure is {width:.1f} cm wide, the text block is {width_cm:.1f} cm: draw at the printed size, do not scale in LaTeX')

    for font in page.get_fonts(full=True):
        extension, kind, name = font[1], font[2], font[3]
        if kind == 'Type3':
            add('FAIL', 'type3-font', f'{name} is a Type 3 font; journals reject them (use pdf.fonttype 42 or this skill\'s renderer)')
        elif extension == 'n/a':
            add('FAIL', 'font-not-embedded', f'{name} is not embedded')

    spans = [span for block in page.get_text('dict')['blocks'] for line in block.get('lines', []) for span in line['spans']
             if span['text'].strip()]
    sizes = [span['size'] for span in spans]
    if sizes:
        median = statistics.median(sizes)
        if min(sizes) < 6.5:
            add('FAIL', 'text-too-small', f'smallest text prints at {min(sizes):.1f} pt; the floor is 6.5 pt, the house size 8.5 pt')
        elif min(sizes) < 7.5:
            add('WARN', 'text-small', f'smallest text prints at {min(sizes):.1f} pt; the house size is 8.5 pt')
        if max(sizes) - min(sizes) > 1.6:
            add('WARN', 'mixed-sizes', f'text runs from {min(sizes):.1f} to {max(sizes):.1f} pt; one size for the whole figure reads best')
        for span in spans:
            text = span['text'].strip()
            if span['bbox'][1] < page.rect.height * 0.12 and span['size'] >= 1.15 * median and len(text) > 4 and not text.startswith('('):
                add('FAIL', 'title-in-figure', f'"{text[:40]}" looks like a title; titles go in \\caption')
            if span['size'] > 10.5:
                add('FAIL', 'oversized-text', f'"{text[:40]}" prints at {span["size"]:.1f} pt')
            if STATISTICS.search(text):
                add('FAIL', 'statistics-on-plot', f'"{text[:40]}": coefficients, stars, t and p values belong in the tables')
            if 'bold' in span['font'].lower():
                add('WARN', 'bold-text', f'"{text[:40]}" is bold; journal figures use one regular weight')
    if page.get_images():
        add('WARN', 'raster', 'the PDF embeds raster images; data figures should be vector')
    unique = list(dict.fromkeys(findings))
    return unique or [('PASS', 'ok', 'no problems found')]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pdfs', nargs='+')
    parser.add_argument('--width-cm', type=float, help='the text block width the figure is included at')
    arguments = parser.parse_args()
    failed = False
    for path in arguments.pdfs:
        print(f'--- {path}')
        for level, code, message in audit(path, arguments.width_cm):
            print(f'  {level:4s} {code:20s} {message}')
            failed |= level == 'FAIL'
    return 2 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
