"""The six figure types empirical business papers use. Each draws one panel from a spec dict and a CSV file.

Data always comes from a file the paper's own scripts wrote (the table sources), never typed into the spec,
so a figure and the tables cannot disagree.
"""
import csv
from pathlib import Path

import numpy as np

from .style import GREY, INK, LIGHT, LINE_PT, MARKER_PT, colors, series_style

Z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
REFERENCE = dict(color=GREY, linewidth=0.6, linestyle=(0, (3, 2)), zorder=0)


def read_rows(panel, base):
    with open(Path(base) / panel['data'], newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f'{panel["data"]} has no rows')
    return rows


def _num(value):
    return float(value) if value not in (None, '') else float('nan')


def _interval(row, z):
    coef = _num(row['coef'])
    if row.get('lo') not in (None, '') and row.get('hi') not in (None, ''):
        return coef, _num(row['lo']), _num(row['hi'])
    se = _num(row.get('se'))
    return coef, coef - z * se, coef + z * se


def _series(rows, panel):
    """Rows grouped by their 'series' column, in the order the spec names them; label for each."""
    names = list(dict.fromkeys(row.get('series', '') for row in rows))
    labels = panel.get('series') or {}
    order = [name for name in labels if name in names] + [name for name in names if name not in labels]
    return [(name, labels.get(name, name), [row for row in rows if row.get('series', '') == name]) for name in order]


def _marker(style):
    return dict(marker=style['marker'], markersize=MARKER_PT, markerfacecolor=style['fill'], markeredgecolor=style['color'],
                markeredgewidth=LINE_PT, color=style['color'], linewidth=LINE_PT)


def _labels(ax, panel):
    if panel.get('xlabel'):
        ax.set_xlabel(panel['xlabel'])
    if panel.get('ylabel'):
        ax.set_ylabel(panel['ylabel'])


def _legend(ax, groups, panel):
    if len(groups) > 1 and panel.get('legend', True) is not False:
        ax.legend(loc=panel.get('legend') if isinstance(panel.get('legend'), str) else 'best')


def event_study(ax, panel, rows):
    """Dynamic effects around an event: one estimate per relative period, base period normalised to zero."""
    z = Z[float(panel.get('ci', 0.95))]
    base = panel.get('base', -1)
    groups = _series(rows, panel)
    spread = 0.18 if len(groups) > 1 else 0
    periods = set()
    for index, (_, label, members) in enumerate(groups):
        style = series_style(index)
        points = {int(_num(row['k'])): _interval(row, z) for row in members}
        points.setdefault(base, (0.0, 0.0, 0.0))
        ks = sorted(points)
        periods.update(ks)
        coef = np.array([points[k][0] for k in ks])
        lo, hi = np.array([points[k][1] for k in ks]), np.array([points[k][2] for k in ks])
        x = np.array(ks) + (index - (len(groups) - 1) / 2) * spread
        if panel.get('style') == 'band' and len(groups) == 1:
            ax.fill_between(x, lo, hi, color=style['color'], alpha=0.18, linewidth=0, zorder=1)
            ax.plot(x, coef, label=label, zorder=3, **_marker(style))
        else:
            ax.errorbar(x, coef, yerr=[coef - lo, hi - coef], linestyle='none', elinewidth=LINE_PT, label=label, zorder=3,
                        **{key: value for key, value in _marker(style).items() if key != 'linewidth'})
    ax.axhline(0, **REFERENCE)
    if panel.get('event_line', True):
        ax.axvline(base + 0.5, color=GREY, linewidth=0.6, linestyle=':', zorder=0)
    ticks = sorted(periods)
    ax.set_xticks(ticks)
    labels = [f'${k}$' for k in ticks]
    if panel.get('binned_ends'):
        labels[0], labels[-1] = f'$\\leq\\!{ticks[0]}$', f'$\\geq\\!{ticks[-1]}$'
    ax.set_xticklabels(labels)
    _labels(ax, panel)
    _legend(ax, groups, panel)


def coefplot(ax, panel, rows):
    """Point estimates with confidence intervals for subgroups, outcomes or specifications."""
    z = Z[float(panel.get('ci', 0.95))]
    horizontal = panel.get('orientation', 'horizontal') == 'horizontal'
    groups = _series(rows, panel)
    items = list(dict.fromkeys((row.get('group', ''), row['label']) for row in rows))
    positions, ticks, separators, headings, position = {}, [], [], [], 0.0
    current = None
    for group, label in items:
        if group != current:
            if group:
                if ticks:
                    separators.append(position - 0.3)
                    position += 0.4
                headings.append((position, group))
                position += 1
            current = group
        positions[(group, label)] = position
        ticks.append((position, label))
        position += 1
    spread = 0.22 if len(groups) > 1 else 0
    for index, (_, name, members) in enumerate(groups):
        style = series_style(index)
        for row in members:
            coef, lo, hi = _interval(row, z)
            at = positions[(row.get('group', ''), row['label'])] + (index - (len(groups) - 1) / 2) * spread
            kwargs = dict(linestyle='none', elinewidth=LINE_PT, zorder=3,
                          **{key: value for key, value in _marker(style).items() if key != 'linewidth'})
            if horizontal:
                ax.errorbar(coef, at, xerr=[[coef - lo], [hi - coef]], **kwargs)
            else:
                ax.errorbar(at, coef, yerr=[[coef - lo], [hi - coef]], **kwargs)
        ax.plot([], [], label=name, linestyle='none', **{k: v for k, v in _marker(style).items() if k != 'linewidth'})
    line = ax.axvline if horizontal else ax.axhline
    line(0, **REFERENCE)
    reference = panel.get('reference')
    if reference:
        line(float(reference['value']), color=GREY, linewidth=0.6, linestyle=':', zorder=0)
        if reference.get('label'):
            if horizontal:
                ax.annotate(reference['label'], (float(reference['value']), 1), xycoords=('data', 'axes fraction'),
                            xytext=(2, -1), textcoords='offset points', ha='left', va='top', color=GREY)
            else:
                ax.annotate(reference['label'], (1, float(reference['value'])), xycoords=('axes fraction', 'data'),
                            xytext=(-1, 2), textcoords='offset points', ha='right', va='bottom', color=GREY)
    places, names = zip(*ticks)
    for at, name in headings:        # a group name on its own row, flush with the plot's left edge
        if horizontal:
            ax.annotate(name, (0, at), xycoords=('axes fraction', 'data'), xytext=(2, 0), textcoords='offset points', ha='left', va='center')
        else:
            ax.annotate(name, (at, 1), xycoords=('data', 'axes fraction'), ha='center', va='bottom')
    for at in separators:            # a hairline between groups; the group name sits on its own row without an estimate
        (ax.axhline if horizontal else ax.axvline)(at, color=LIGHT, linewidth=0.4, zorder=0)
    if horizontal:
        ax.set_yticks(places)
        ax.set_yticklabels(names)
        ax.tick_params(axis='y', length=0)
        ax.spines['left'].set_visible(False)
        ax.set_ylim(position - 0.4, -0.6)
    else:
        ax.set_xticks(places)
        ax.set_xticklabels(names)
        ax.set_xlim(-0.6, position - 0.4)
    _labels(ax, panel)
    _legend(ax, groups, panel)


def binscatter(ax, panel, rows):
    """Binned scatter of y on x (both residualised on the controls and fixed effects by the paper's script) with a linear fit."""
    groups = _series(rows, panel)
    bins = int(panel.get('bins', 20))
    for index, (_, label, members) in enumerate(groups):
        style = series_style(index)
        if 'x_bin' in members[0]:
            bx = np.array([_num(row['x_bin']) for row in members])
            by = np.array([_num(row['y_bin']) for row in members])
            fit_x, fit_y = bx, by
        else:
            x = np.array([_num(row['x']) for row in members])
            y = np.array([_num(row['y']) for row in members])
            order = np.argsort(x)
            chunks = np.array_split(order, bins)
            bx = np.array([x[chunk].mean() for chunk in chunks])
            by = np.array([y[chunk].mean() for chunk in chunks])
            fit_x, fit_y = x, y
        ax.plot(bx, by, linestyle='none', label=label, zorder=3, **{k: v for k, v in _marker(style).items() if k != 'linewidth'})
        if panel.get('fit', 'linear') == 'linear':
            slope, intercept = np.polyfit(fit_x, fit_y, 1)
            span = np.array([bx.min(), bx.max()])
            ax.plot(span, intercept + slope * span, color=style['color'], linewidth=LINE_PT, linestyle=style['linestyle'], zorder=2)
    _labels(ax, panel)
    _legend(ax, groups, panel)


def trend(ax, panel, rows):
    """Yearly values (means, shares) of one to three series."""
    groups = _series(rows, panel)
    years = set()
    for index, (_, label, members) in enumerate(groups):
        style = series_style(index)
        points = sorted((_num(row['year']), _num(row['value'])) for row in members)
        years.update(int(year) for year, _ in points)
        ax.plot([p[0] for p in points], [p[1] for p in points], label=label, linestyle=style['linestyle'], **_marker(style))
    if panel.get('event_year') is not None:
        at = float(panel['event_year'])
        ax.axvline(at, color=GREY, linewidth=0.6, linestyle=':', zorder=0)
        if panel.get('event_label'):
            ax.annotate(panel['event_label'], (at, 1), xycoords=('data', 'axes fraction'), xytext=(2, -1),
                        textcoords='offset points', ha='left', va='top', color=GREY)
    step = int(panel.get('year_step', 2 if len(years) > 8 else 1))
    first = min(years)
    ax.set_xticks([year for year in sorted(years) if (year - first) % step == 0])
    ax.xaxis.set_major_formatter(lambda value, _: f'{int(value)}')
    _labels(ax, panel)
    _legend(ax, groups, panel)


def distribution(ax, panel, rows):
    """Distribution of estimates, e.g. a placebo test, with the actual estimate marked; or densities of groups."""
    groups = _series(rows, panel)
    values = [(label, np.array([_num(row['value']) for row in members])) for _, label, members in groups]
    if panel.get('mode', 'hist') == 'hist' and len(values) == 1:
        ax.hist(values[0][1], bins=int(panel.get('bins', 30)), color=colors[0], alpha=0.35, edgecolor='white', linewidth=0.4, zorder=1)
    else:
        lowest = min(v.min() for _, v in values)
        highest = max(v.max() for _, v in values)
        grid = np.linspace(lowest, highest, 200)
        for index, (label, data) in enumerate(values):
            style = series_style(index)
            width = 1.06 * data.std() * len(data) ** -0.2
            density = np.exp(-0.5 * ((grid[:, None] - data[None, :]) / width) ** 2).sum(1) / (len(data) * width * np.sqrt(2 * np.pi))
            ax.plot(grid, density, color=style['color'], linestyle=style['linestyle'], linewidth=LINE_PT, label=label)
    if panel.get('actual') is not None:
        at = float(panel['actual'])
        ax.axvline(at, color=colors[1 % len(colors)], linewidth=1.0, zorder=3)
        if panel.get('actual_label'):
            ax.annotate(panel['actual_label'], (at, 1), xycoords=('data', 'axes fraction'), xytext=(-3, -1),
                        textcoords='offset points', ha='right', va='top')
    if panel.get('zero_line', True):
        ax.axvline(0, **REFERENCE)
    _labels(ax, panel)
    _legend(ax, [(None, label, None) for label, _ in values], panel)


def framework(ax, panel, rows=None):
    """Research framework: constructs as boxes, hypotheses as arrows labelled H1, H2a ... and nothing else.

    Boxes sit in columns (left: explanatory variable, middle: channels, right: outcome) and rows. An arrow between
    columns leaves the right side of one box and enters the left side of the other. An arrow with route: over / under
    goes round the middle column above or below it; a moderator (to: "a->b") points at the middle of that arrow's
    horizontal run.
    """
    from matplotlib.patches import Rectangle
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    nodes = {node['id']: node for node in panel['nodes']}
    columns = max(node['col'] for node in nodes.values()) + 1
    rows_count = float(panel.get('rows') or max(node.get('row', 0) for node in nodes.values()) + 1)
    width = float(panel.get('box_width', 0.62 / columns))
    height = float(panel.get('box_height', 0.62 / rows_count))
    boxes = {}
    for key, node in nodes.items():
        cx = (node['col'] + 0.5) / columns
        cy = 1 - (node.get('row', 0) + 0.5) / rows_count
        boxes[key] = (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
        ax.add_patch(Rectangle((cx - width / 2, cy - height / 2), width, height, facecolor=node.get('fill', 'white'), edgecolor=INK, linewidth=0.6, zorder=2))
        ax.text(cx, cy, node['text'], ha='center', va='center', linespacing=1.3, zorder=3)
    gap = 0.5 / rows_count             # how far a routed arrow runs above or below the boxes

    def side(box, other):
        x0, y0, x1, y1 = box
        if other[0] > x1:
            return x1, (y0 + y1) / 2
        if other[0] < x0:
            return x0, (y0 + y1) / 2
        return (x0 + x1) / 2, (y1 if other[1] > y1 else y0)

    def centre(box):
        return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    def arrow(points, dashed):
        style = dict(color=INK, linewidth=0.7, linestyle='--' if dashed else '-', zorder=1)
        for a, b in zip(points[:-2], points[1:-1]):
            ax.plot([a[0], b[0]], [a[1], b[1]], solid_capstyle='butt', **style)
        ax.annotate('', xy=points[-1], xytext=points[-2], arrowprops=dict(arrowstyle='-|>', mutation_scale=7, shrinkA=0, shrinkB=0,
                                                                          color=INK, linewidth=0.7, linestyle=style['linestyle']), zorder=1)

    runs = {}
    for edge in panel.get('edges', []):
        source = boxes[edge['from']]
        if '->' in edge['to']:
            (a, b) = runs[edge['to']]
            end = ((a[0] + b[0]) / 2, a[1])
            start = (end[0], source[1] if source[1] > end[1] else source[3])
            points = [start, end]
        else:
            target = boxes[edge['to']]
            if edge.get('route') in ('over', 'under'):
                over = edge['route'] == 'over'
                level = (max(source[3], target[3]) if over else min(source[1], target[1])) + (gap if over else -gap)
                start = (centre(source)[0], source[3] if over else source[1])
                end = (centre(target)[0], target[3] if over else target[1])
                points = [start, (start[0], level), (end[0], level), end]
                runs[f'{edge["from"]}->{edge["to"]}'] = ((start[0], level), (end[0], level))
            else:
                start, end = side(source, centre(target)), side(target, centre(source))
                points = [start, end]
                runs[f'{edge["from"]}->{edge["to"]}'] = (start, end)
        arrow(points, edge.get('dashed'))
        if edge.get('label'):
            longest = max(zip(points, points[1:]), key=lambda pair: abs(pair[1][0] - pair[0][0]) + abs(pair[1][1] - pair[0][1]))
            share = 0.3 if len(points) > 2 else 0.5   # a routed arrow may carry a moderator at its midpoint
            mid = (longest[0][0] + share * (longest[1][0] - longest[0][0]), longest[0][1] + share * (longest[1][1] - longest[0][1]))
            if '->' in edge['to']:
                mid = (mid[0], (points[0][1] + points[1][1]) / 2)
            ax.text(*mid, edge['label'], ha='center', va='center', zorder=3,
                    bbox=dict(facecolor='white', edgecolor='none', boxstyle='square,pad=0.15'))


DRAW = {'event-study': event_study, 'coefplot': coefplot, 'binscatter': binscatter, 'trend': trend,
        'distribution': distribution, 'framework': framework}
