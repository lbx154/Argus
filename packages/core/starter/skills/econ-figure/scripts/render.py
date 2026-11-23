#!/usr/bin/env python3
"""Render a figure spec (YAML or JSON) to PDF and PNG in the house style, then audit it.

    python3 render.py spec.yaml -o paper/figures/fig_event        # writes fig_event.pdf and fig_event.png
    python3 render.py spec.yaml -o out/fig --png-dpi 600 --no-audit

Paths to data files in the spec are relative to the spec file. Exits 2 when the audit finds a FAIL.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from econfig import style  # noqa: E402

HEIGHT = {'event-study': 0.45, 'coefplot': None, 'binscatter': 0.62, 'trend': 0.42, 'distribution': 0.45, 'framework': 0.42}


def load(path):
    text = Path(path).read_text(encoding='utf-8')
    if path.endswith('.json'):
        return json.loads(text)
    import yaml
    return yaml.safe_load(text)


def width_cm(figure):
    if figure.get('width_cm'):
        return float(figure['width_cm'])
    if figure.get('width_pt'):
        return float(figure['width_pt']) / style.PT_PER_CM
    raise SystemExit('figure.width_cm (or width_pt) is required: measure \\textwidth of the paper template first, see references/house-style.md')


def render(spec, base, out, png_dpi=300):
    style.prepare_environment()
    figure, panels = spec['figure'], spec['panels']
    plt = style.apply(figure.get('language', 'zh'), figure.get('preamble', ''))
    style.set_colors(figure.get('colors'))
    from econfig.archetypes import DRAW, read_rows

    rows, columns = figure.get('layout') or [1, len(panels)]
    width = width_cm(figure)
    ratio = figure.get('height_ratio')
    if not ratio:
        first = panels[0]
        if first['archetype'] == 'coefplot':   # one row per estimate plus one per group heading
            data = read_rows(first, base)
            ratio = min(0.9, 0.1 + 0.045 * (len({row['label'] for row in data}) + len({row.get('group') for row in data} - {None, ''})))
        else:
            ratio = HEIGHT[first['archetype']]
        ratio *= rows
    fig = plt.figure(figsize=(width / 2.54, width * ratio / 2.54), layout='constrained')
    fig.get_layout_engine().set(w_pad=0.02, h_pad=0.02, wspace=0.06, hspace=0.06)
    axes = fig.subplots(rows, columns, squeeze=False).ravel()
    for index, (ax, panel) in enumerate(zip(axes, panels)):
        if panel['archetype'] not in DRAW:
            raise SystemExit(f'unknown archetype {panel["archetype"]!r}; one of {", ".join(DRAW)}')
        DRAW[panel['archetype']](ax, panel, None if panel['archetype'] == 'framework' else read_rows(panel, base))
        if len(panels) > 1:
            ax.set_title(f'({chr(97 + index)}) {panel.get("subtitle", "")}'.rstrip(), loc='left', pad=3, fontsize=style.FONT_PT)
    for ax in axes[len(panels):]:
        ax.set_axis_off()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = out.with_suffix('.pdf')
    fig.savefig(pdf, metadata={'Creator': None, 'Producer': None, 'Author': None})
    plt.close(fig)
    if png_dpi:
        subprocess.run(['pdftoppm', '-png', '-singlefile', '-r', str(png_dpi), str(pdf), str(out)], check=True)
    return pdf, width


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('spec')
    parser.add_argument('-o', '--out', required=True, help='output path without extension')
    parser.add_argument('--png-dpi', type=int, default=300, help='0 to skip the PNG preview')
    parser.add_argument('--no-audit', action='store_true')
    arguments = parser.parse_args()
    spec = load(arguments.spec)
    pdf, width = render(spec, Path(arguments.spec).resolve().parent, arguments.out, arguments.png_dpi)
    print(f'wrote {pdf} ({width:.1f} cm wide, {len(spec["panels"])} panel(s))')
    if arguments.no_audit:
        return 0
    from audit import audit
    findings = audit(str(pdf), width)
    for level, code, message in findings:
        print(f'  {level:4s} {code:20s} {message}')
    return 2 if any(level == 'FAIL' for level, _, _ in findings) else 0


if __name__ == '__main__':
    sys.exit(main())
