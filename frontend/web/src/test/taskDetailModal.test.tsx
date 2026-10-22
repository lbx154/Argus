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
});
