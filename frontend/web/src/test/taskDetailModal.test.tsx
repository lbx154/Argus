import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { BacklogItem } from '../../../core/src/types';
import { TaskDetailModal } from '../components/TaskDetailModal';

let item: BacklogItem;
vi.mock('../hooks', () => ({ useBacklogItem: () => ({ data: item, isLoading: false, isError: false }) }));

const blocked = { execution_status: 'blocked', review_status: 'blocked', stage_certification: 'not_certified', interruption_kind: 'none', resumable: false };
const markup = () => renderToStaticMarkup(
  <TaskDetailModal sid="s" itemId="t" onClose={() => {}} onDone={() => {}} onSkip={() => {}} onStop={() => {}} busy={false} />,
);

describe('task details', () => {
  it('shows a resumed task without its earlier result, and never the routing tags', () => {
    // Trial s-934a4569: the running task still read "work cannot continue",
    // and the team's tags showed as #manager_direct #stage_closing ….
    item = { id: 't', title: 'Settle truncate', objective: 'Pick A or B', status: 'parked', priority: -1,
      tags: ['manager_direct', 'stage_closing', 'operator-reply'], outcome: blocked } as BacklogItem;
    expect(markup()).toContain('Work blocked');
    item = { ...item, status: 'running' };
    const running = markup();
    expect(running).not.toContain('Outcome');
    expect(running).not.toContain('Work blocked');
    expect(running).not.toContain('#manager_direct');
    expect(running).not.toContain('stage_closing');
  });

  it('says a planned task by its title, keeps its brief for the team and loops only once it has', () => {
    // Trial s-6f0e6213: a Planner task read "stop loop", "Iteration 0/0", "priority 100"
    // and the Engineer's brief as its objective.
    item = { id: 't', title: 'Add format_hours', objective: 'Engineer adds format_hours to core.py', status: 'running',
      priority: 100, plan_id: 'plan-1', iterate: true, iteration_cycles_done: 0, iteration_max_cycles: 0 } as BacklogItem;
    const planned = markup();
    expect(planned).toContain('<summary class="cursor-pointer select-none hover:text-ink">Instructions for the team</summary>');
    expect(planned).toContain('Engineer adds format_hours to core.py');
    for (const hidden of ['Objective', 'stop loop', 'Iteration', 'priority']) expect(planned).not.toContain(hidden);
    item = { ...item, iteration_cycles_done: 1 };
    expect(markup()).toContain('stop loop');
    // A task with no plan was asked for in the reader's own words.
    item = { ...item, plan_id: '', objective: 'Add an hours formatter' };
    expect(markup()).toContain('Objective');
  });

  it('names a superseded task as replaced and offers nothing to do with it', () => {
    item = { id: 't', title: 'Old plan', objective: 'Pick A or B', status: 'superseded', priority: -1 } as BacklogItem;
    const replaced = markup();
    expect(replaced).toContain('Replaced by a newer plan');
    expect(replaced).not.toContain('Status updated');
    expect(replaced).not.toContain('>done<');
  });
});
