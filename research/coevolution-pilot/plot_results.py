import json

import matplotlib
from paths import ROOT

matplotlib.use('Agg')
import matplotlib.pyplot as plt

root = ROOT
data = json.loads((root / 'results.json').read_text())
assert data['heldout_complete'], 'Do not plot unfinished runs as zero-valued results'
keys = data['protocol']['conditions']
labels = ['Baseline', 'Skill + Wiki', 'Pi runtime', 'Joint']
colors = ['#71819a', '#8061b0', '#299b8e', '#3478c7']
fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
metrics = [('tasks_passed', 'Heldout tasks passed', 'tasks / 3'),
           ('cost_usd', 'Heldout inference cost', 'estimated USD'),
           ('tokens', 'Context and output volume', 'million tokens')]
for ax, (metric, title, unit) in zip(axes, metrics):
    values = [data['aggregate'][key][metric] / (1e6 if metric == 'tokens' else 1) for key in keys]
    bars = ax.bar(labels, values, color=colors, width=.62)
    for bar, value in zip(bars, values):
        ax.annotate(f'{value:.0f}' if metric == 'tasks_passed' else f'{value:.3f}',
                    (bar.get_x()+bar.get_width()/2, bar.get_height()), xytext=(0, 5),
                    textcoords='offset points', ha='center', fontsize=9)
    ax.set_title(title, fontsize=11)
    ax.set_ylabel(unit)
    ax.tick_params(axis='x', rotation=22, labelsize=9)
    ax.spines[['top', 'right']].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(axis='y', alpha=.2)
    ax.set_ylim(0, 3.6 if metric == 'tasks_passed' else max(values)*1.22)
    if metric == 'tasks_passed':
        ax.set_yticks([0, 1, 2, 3])
fig.suptitle('Argus / Argus-Pi co-evolution pilot — SkillsBench subset', fontsize=13)
fig.text(.5, .01, '3 tasks, one run/condition; original verifier score includes a reference inconsistency. See audit. No significance claim.', ha='center', fontsize=8, color='#555555')
fig.tight_layout(rect=(0, .06, 1, .92))
fig.savefig(root / 'ablation.png', dpi=180)
fig.savefig(root / 'ablation.svg')
print(root / 'ablation.png')
