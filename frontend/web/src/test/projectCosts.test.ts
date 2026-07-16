import { describe, expect, it } from 'vitest';

import { mergeProjectCosts } from '../lib/projectCosts';
import type { ProjectCostRow, ProjectRow } from '../api';

describe('mergeProjectCosts', () => {
  it('adds cost feed values without disturbing project identity', () => {
    const projects: ProjectRow[] = [{
      id: 's-one',
      label: 'One',
      objective: '',
      last_active: 1,
      daemon_alive: true,
      daemon_pid: 1,
      uptime_seconds: 10,
    }];
    const costs: ProjectCostRow[] = [{
      id: 's-one',
      spend_usd: 1.25,
      known_cost_usd: 1.25,
      spend_status: 'priced',
      usage_calls: 5,
      premium_requests: 2,
      updated_at: 99,
    }];

    expect(mergeProjectCosts(projects, costs)[0]).toMatchObject({
      id: 's-one',
      label: 'One',
      spend_usd: 1.25,
      usage_calls: 5,
      cost_updated_at: 99,
    });
  });
});
