import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { BacklogItem } from '../../../core/src/types';
import { TaskDetailModal } from '../components/TaskDetailModal';

let item: BacklogItem;
vi.mock('../hooks', () => ({ useBacklogItem: () => ({ data: item, isLoading: false, isError: false }) }));
const markup = () => renderToStaticMarkup(<TaskDetailModal sid="s" itemId="t" onClose={() => {}} />);

describe('task details', () => {
  it('shows the brief, recorded times, letters and last error from the detail response', () => {
    item = { id: 't', title: 'Sales chart', objective: 'Plot **sales** per region into chart.png.', status: 'blocked', priority: 0,
      deps: ['t0'], started_ts: 1793275200, finished_ts: null,
      notes: 'Engineer to the Reviewer: Chart drawn from sales.csv', last_error: 'codex exited with code 1' };
    const html = markup();
    for (const text of ['Sales chart', 'Objective', 'Waiting on you', 'started', 'finished', 'Chart drawn from sales.csv', 'Last error', 'codex exited with code 1']) expect(html).toContain(text);
    expect(html).toMatch(/<strong[^>]*>sales<\/strong>/);
    for (const absent of ['Outcome', 'Iteration', 'Instructions for the team', 'Which years?']) expect(html).not.toContain(absent);
  });

  it('shows the recorded task status and brief without requiring snapshot-only data', () => {
    item = { id: 't', title: 'Add format_hours', objective: 'Add an **hours** formatter', status: 'in_progress', priority: 0, deps: [] };
    const html = markup();
    expect(html).toContain('Add format_hours');
    expect(html).toContain('Objective');
    expect(html).toMatch(/<strong[^>]*>hours<\/strong>/);
  });
});
