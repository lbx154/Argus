import { describe, expect, it } from 'vitest';

import {
  counterexampleTimestampMs,
  deriveCounterexampleProgress,
  formatCounterexampleUpdate,
  type CounterexampleConjecture,
} from '../lib/counterexampleProgress';

describe('counterexample progress helpers', () => {
  it('derives progress, current stage, and evidence totals from live stage data', () => {
    const conjecture: CounterexampleConjecture = {
      id: 'jacobian',
      title: 'Jacobian conjecture',
      status: 'active',
      live: true,
      updatedAt: '2026-08-27T12:00:00Z',
      stages: [
        { id: 'align', label: 'Align statement', status: 'completed' },
        { id: 'construct', label: 'Construct witness', status: 'running', progress: 40 },
        { id: 'review', label: 'Independent review', status: 'pending' },
      ],
      evidence: [
        { id: 'paper', title: 'Original paper', status: 'verified' },
        { id: 'run', title: 'Jacobian run', status: 'candidate' },
      ],
    };

    const result = deriveCounterexampleProgress(
      conjecture,
      Date.parse('2026-08-27T12:00:30Z'),
    );

    expect(result.progress).toBeCloseTo(46.666, 2);
    expect(result.currentStage?.id).toBe('construct');
    expect(result.completedStages).toBe(1);
    expect(result.totalStages).toBe(3);
    expect(result.verifiedEvidenceCount).toBe(1);
    expect(result.active).toBe(true);
    expect(result.live).toBe(true);
  });

  it('honors explicit progress and marks old live data as stale', () => {
    const result = deriveCounterexampleProgress({
      id: 'heat',
      title: 'Heat transform conjecture',
      progress: 140,
      live: true,
      updatedAt: 1_700_000_000,
    }, 1_700_000_500_000, 120_000);

    expect(result.progress).toBe(100);
    expect(result.live).toBe(false);
    expect(counterexampleTimestampMs(1_700_000_000)).toBe(1_700_000_000_000);
  });

  it('formats stable relative update labels', () => {
    const now = Date.parse('2026-08-27T12:00:00Z');
    expect(formatCounterexampleUpdate('2026-08-27T11:58:20Z', now)).toBe('1m ago');
    expect(formatCounterexampleUpdate('2026-08-27T12:00:03Z', now)).toBe('just now');
    expect(formatCounterexampleUpdate(null, now)).toBe('No updates yet');
  });
});
