"""House style for economics, finance, accounting and management journal figures.

Every constant here follows the journals' published figures (JFE, TAR, RFS, JF, 管理世界, 经济研究):
the body font, one text size, no titles inside the figure, no statistics printed on the plot. Colour is free;
the default palette is a restrained one, and a spec may set its own with figure.colors.
"""
import os
import tempfile
from pathlib import Path

PT_PER_CM = 72.27 / 2.54          # TeX points per centimetre
INK = '#000000'                   # axes and text
GREY = '#6E6E6E'                  # reference lines, secondary labels
LIGHT = '#BDBDBD'                 # bands, histogram fills, separators
PALETTE = ['#1F3A68', '#B2432F', '#3A7D44', '#6E6E6E', '#C08A2B']   # navy, brick, green, grey, ochre
FONT_PT = 8.5                     # labels, ticks, legend; one size for the whole figure
LINE_PT = 0.8
AXIS_PT = 0.6
MARKER_PT = 3.6

# Series also differ in marker and line style, so a figure still reads when printed in black and white.
MARKERS = [('o', True, '-'), ('s', False, '--'), ('^', True, ':'), ('D', False, '-.'), ('v', True, (0, (5, 1)))]
colors = list(PALETTE)

FONTS = {
    # The fonts the paper templates set: Times-like Latin, Song for Chinese, matching maths.
    'zh': r'\usepackage{fontspec}\setmainfont{TeX Gyre Termes}'
          r'\usepackage{xeCJK}\setCJKmainfont{FandolSong-Regular.otf}[BoldFont=FandolSong-Bold.otf]'
          r'\usepackage{unicode-math}\setmathfont{TeX Gyre Termes Math}',
    'en': r'\usepackage{fontspec}\setmainfont{TeX Gyre Termes}'
          r'\usepackage{unicode-math}\setmathfont{TeX Gyre Termes Math}',
}


def prepare_environment():
    """Caches TeX and matplotlib need must be writable even in a sandbox whose home is hidden. Call before importing pyplot."""
    cache = Path(os.environ.get('ECONFIG_CACHE') or Path(tempfile.gettempdir()) / 'econfig-cache')
    for key, sub in (('MPLCONFIGDIR', 'mpl'), ('TEXMFVAR', 'texmf-var'), ('XDG_CACHE_HOME', 'xdg')):
        if not os.environ.get(key) or not os.access(os.environ[key], os.W_OK):
            (cache / sub).mkdir(parents=True, exist_ok=True)
            os.environ[key] = str(cache / sub)


def apply(language='zh', preamble=''):
    """Text is typeset by XeLaTeX through the pgf backend: the figure uses the paper's own fonts and maths."""
    import matplotlib
    matplotlib.use('pgf')
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        'pgf.texsystem': 'xelatex', 'pgf.rcfonts': False, 'pgf.preamble': FONTS[language] + preamble,
        'font.family': 'serif', 'font.size': FONT_PT, 'axes.labelsize': FONT_PT, 'axes.titlesize': FONT_PT,
        'xtick.labelsize': FONT_PT, 'ytick.labelsize': FONT_PT, 'legend.fontsize': FONT_PT,
        'axes.linewidth': AXIS_PT, 'axes.edgecolor': INK, 'axes.labelcolor': INK, 'text.color': INK,
        'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': False,
        'xtick.color': INK, 'ytick.color': INK, 'xtick.direction': 'out', 'ytick.direction': 'out',
        'xtick.major.size': 2.5, 'ytick.major.size': 2.5, 'xtick.major.width': AXIS_PT, 'ytick.major.width': AXIS_PT,
        'lines.linewidth': LINE_PT, 'lines.markersize': MARKER_PT, 'errorbar.capsize': 1.5,
        'legend.frameon': False, 'legend.handlelength': 1.8, 'legend.borderaxespad': 0.2,
        'axes.unicode_minus': True, 'savefig.transparent': False, 'savefig.facecolor': 'white',
        'figure.facecolor': 'white', 'axes.facecolor': 'white',
    })
    return plt


def set_colors(values):
    """Use the spec's own colours (any matplotlib colour) instead of the default palette."""
    colors[:] = list(values) if values else list(PALETTE)


def series_style(index):
    marker, filled, linestyle = MARKERS[index % len(MARKERS)]
    color = colors[index % len(colors)]
    return dict(color=color, marker=marker, fill=color if filled else 'white', linestyle=linestyle)
