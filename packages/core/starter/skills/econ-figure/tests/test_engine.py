"""Every template renders and passes the audit; a conference-style figure fails it for the right reasons.

    python3 -m pytest skills/econ-figure/tests -q      (or run this file directly)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'scripts'))
from audit import audit  # noqa: E402


def test_templates_render_and_pass():
    with tempfile.TemporaryDirectory() as out:
        for spec in sorted((HERE / 'templates').glob('*.yaml')):
            result = subprocess.run([sys.executable, str(HERE / 'scripts/render.py'), str(spec), '-o', f'{out}/{spec.stem}', '--png-dpi', '0'],
                                    capture_output=True, text=True)
            assert result.returncode == 0, f'{spec.name}:\n{result.stdout}\n{result.stderr[-2000:]}'
            assert 'PASS' in result.stdout, f'{spec.name}: {result.stdout}'


def test_conference_style_figure_fails():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['pdf.fonttype'] = 42
    with tempfile.TemporaryDirectory() as out:
        fig, ax = plt.subplots(figsize=(6, 2.5))
        ax.bar(['A', 'B'], [0.4, 0.7], color='#E07B39', hatch='//')
        ax.set_title('Overview of Our Method', fontsize=14, fontweight='bold')
        ax.text(1, 0.72, '0.70***', ha='center')
        fig.savefig(f'{out}/bad.pdf')
        codes = {code for level, code, _ in audit(f'{out}/bad.pdf', width_cm=16.0) if level == 'FAIL'}
    assert {'title-in-figure', 'statistics-on-plot', 'width', 'oversized-text'} <= codes, codes
    assert 'colour' not in codes   # colour is the author's choice


if __name__ == '__main__':
    test_templates_render_and_pass()
    test_conference_style_figure_fails()
    print('ok')
