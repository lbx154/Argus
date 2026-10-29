import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { EventMsg, Snapshot } from '../../../core/src/types';

vi.mock('../i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return {
    ...actual,
    useI18n: () => ({ locale: 'zh-CN', setLocale: vi.fn(), t: (key: string, variables?: Record<string, string | number>) => actual.translate(key, variables, 'zh-CN') }),
  };
});

const { progressHealth } = await import('../lib/progressHealth');
const { foldedContinuationRuns } = await import('../components/EventStream');
const { searchEntries, visibleEntries } = await import('../research-workbench/workspaceApi');
const { ProgressHealthBanner } = await import('../components/ProgressHealthBanner');

function snapshot(backlog: Snapshot['backlog'], alive = true, startedAt = 0): Pick<Snapshot, 'backlog' | 'daemon'> {
  return { backlog, daemon: { alive, pid: alive ? 1 : null, uptime_seconds: null, backend: null,
    started_at_iso: startedAt ? new Date(startedAt * 1000).toISOString() : null } };
}
const task = (id: string, status: string, finished: number, extra: Record<string, unknown> = {}) =>
  ({ id, title: `任务 ${id}`, objective: '', status, priority: 1, started_ts: finished - 100, finished_ts: finished, ...extra });

describe('progress health', () => {
  it('flags several failed tasks in a row and names the latest reason', () => {
    const health = progressHealth(snapshot([
      task('a', 'done', 100), task('b', 'failed', 200), task('c', 'failed', 300),
      task('d', 'failed', 400, { last_error: 'CUDA out of memory' }),
    ]), 500);
    expect(health).toMatchObject({ problem: 'repeated_failures', failures: 3, key: 'failures:d' });
    expect(health?.latestFailure?.reason).toBe('CUDA out of memory');
  });

  it('flags a long active stretch without a reviewed result, and nothing when idle', () => {
    const quiet = snapshot([task('a', 'done', 1000), task('b', 'running', 0, { started_ts: 2000, finished_ts: null })]);
    expect(progressHealth(quiet, 1000 + 13 * 3600)).toMatchObject({ problem: 'no_accepted_result', hoursWithoutAccepted: 13 });
    expect(progressHealth(quiet, 1000 + 3600)).toBeNull();
    expect(progressHealth(snapshot([task('a', 'done', 1000)], false), 1000 + 30 * 3600)).toBeNull();
  });

  it('counts only the time work ran: a project restarted an hour ago after two stopped days is not 45 hours quiet', () => {
    // Trial s-efe9deac: last reviewed result 10-24 10:30, stopped, started again 10-26 06:52; the banner said 45 hours.
    const accepted = 1792751429, restarted = 1792997548;
    const backlog = [task('a', 'done', accepted), task('b', 'running', 0, { started_ts: restarted + 60, finished_ts: null })];
    expect(progressHealth(snapshot(backlog, true, restarted), restarted + 3600)).toBeNull();
    expect(progressHealth(snapshot(backlog, true, restarted), restarted + 13 * 3600))
      .toMatchObject({ problem: 'no_accepted_result', hoursWithoutAccepted: 13 });
    // A task a stopped daemon left marked running is not work going on.
    expect(progressHealth(snapshot(backlog, false, restarted), accepted + 45 * 3600)).toBeNull();
  });

  it('renders a dismissible banner with the two remedies', () => {
    const html = renderToStaticMarkup(<ProgressHealthBanner sid="s-1" onPause={() => undefined} onAdjust={() => undefined}
      health={{ problem: 'repeated_failures', key: 'failures:d', failures: 3, hoursWithoutAccepted: 0, latestFailure: { title: '训练', reason: 'OOM' } }} />);
    expect(html).toContain('最近 3 个任务都没有完成');
    expect(html).toContain('最近一次：训练——OOM');
    expect(html).toContain('当前任务完成后暂停');
    expect(html).toContain('知道了');
  });
});

describe('conversation folding', () => {
  const row = (key: string, continued: boolean) => ({ key, ev: { type: 'ui.argus', mission_result: true, campaign_continues: continued } as EventMsg });
  it('keeps the newest of three or more continued steps and folds the rest', () => {
    const folded = foldedContinuationRuns([row('a', true), row('b', true), row('c', true), row('d', false), row('e', true), row('f', true)]);
    expect([...folded.keys()]).toEqual(['a', 'b']);
    expect(folded.get('b')).toEqual({ id: 'a', keys: ['a', 'b'] });
  });
  it('recognises continued steps from older transcripts by their message id', () => {
    const old = (key: string) => ({ key, ev: { type: 'ui.argus', mission_result: true, message_id: `mission-result-${key}-continued` } as EventMsg });
    expect([...foldedContinuationRuns([old('x'), old('y'), old('z')]).keys()]).toEqual(['x', 'y']);
  });
});

describe('workspace explorer', () => {
  const entries = [
    { path: 'results', name: 'results', type: 'directory' as const, size: 0, mtime: 0, extension: '' },
    { path: 'results/table.csv', name: 'table.csv', type: 'file' as const, size: 1, mtime: 0, extension: '.csv' },
    { path: '.argus', name: '.argus', type: 'directory' as const, size: 0, mtime: 0, extension: '' },
    { path: '.argus/team/log.json', name: 'log.json', type: 'file' as const, size: 1, mtime: 0, extension: '.json' },
    { path: 'models', name: 'models', type: 'directory' as const, size: 0, mtime: 0, extension: '', skipped: true },
  ];
  it('hides internal state until asked and searches file names', () => {
    expect(visibleEntries(entries, false).map((entry) => entry.path)).toEqual(['results', 'results/table.csv']);
    expect(visibleEntries(entries, true)).toHaveLength(5);
    expect(searchEntries(visibleEntries(entries, true), 'LOG').map((entry) => entry.path)).toEqual(['.argus/team/log.json']);
    expect(searchEntries(entries, '  ')).toEqual([]);
  });
});
