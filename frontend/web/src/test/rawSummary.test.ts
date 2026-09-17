import { describe, expect, it } from 'vitest';
import { lastMeaningfulLine, metricsFigures, outputSummary, plainLogLine } from '../lib/rawSummary';

describe('lastMeaningfulLine', () => {
  it('returns the last line that says something, without its timestamp and level', () => {
    const tail = [
      '2026-09-11 22:07:01,123 INFO argus.daemon: round 3 started',
      '2026-09-11 22:07:04,000 ERROR argus.daemon: Copilot CLI exited with code 1.',
      '',
      '----',
    ].join('\n');
    expect(lastMeaningfulLine(tail)).toBe('Copilot CLI exited with code 1.');
  });

  it('is empty for an empty tail and clips a long line', () => {
    expect(lastMeaningfulLine('')).toBe('');
    expect(lastMeaningfulLine(`x${'y'.repeat(400)}`, 40)).toHaveLength(40);
  });

  it('strips bracketed timestamps and levels but keeps the message', () => {
    expect(plainLogLine('[2026-09-11T22:07:01Z] [WARNING] budget nearly spent')).toBe('budget nearly spent');
    expect(plainLogLine('plain message with 2026-09-11 inside')).toBe('plain message with 2026-09-11 inside');
  });
});

describe('outputSummary', () => {
  it('gives the first line and the line count', () => {
    expect(outputSummary('research-idea-playbook\nwriting-checklist\n\nsource-index')).toEqual({ first: 'research-idea-playbook', lines: 3 });
    expect(outputSummary('')).toEqual({ first: '', lines: 0 });
  });
});

describe('metricsFigures', () => {
  it('picks the numbers a person watching a run cares about', () => {
    expect(metricsFigures({
      provider: { completed: 40, errors: 2, denied: 0, success_rate: 0.95, p95_duration_ms: 8200 },
      web: { requests: 900, errors_5xx: 0 },
      cost_control: { active_reservations: 1, in_flight_cost_usd: 0.42 },
    })).toEqual([
      { key: 'calls', value: '42' },
      { key: 'failed', value: '2' },
      { key: 'slowest', value: '8.2s' },
      { key: 'inFlight', value: '$0.42' },
    ]);
  });

  it('falls back to reservations when no cost is known, and to nothing without metrics', () => {
    expect(metricsFigures({ provider: { completed: 0, errors: 0 }, cost_control: { active_reservations: 3 } })).toEqual([
      { key: 'calls', value: '0' },
      { key: 'failed', value: '0' },
      { key: 'reservations', value: '3' },
    ]);
    expect(metricsFigures(null)).toEqual([]);
  });
});
