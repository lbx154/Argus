import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { Flow } from '../../../../packages/core/src/flow';
import { Store } from '../../../../packages/core/src/store';
import { conversationRows, readRecord } from '../../../../packages/core/src/serve/views';
import type { CallAgent } from '../../../../packages/core/src/types';
import { EventStream } from './serverSnapshot';
import type { EventMsg } from '../api';

it.each(['planner', 'manager'] as const)('%s: actual Flow keeps every process across failure, stop, restart and accepted delivery, folded once delivered', async role => {
  const home = mkdtempSync(join(tmpdir(), 'argus-reply-resume-'));
  const store = new Store(home);
  const project = store.create({ request: '', workdir: home, backend: 'codex', model: null });
  let affected = 0;
  let initial = true;
  let controller: AbortController;
  let resumed!: () => void;
  const started = new Promise<void>(resolve => { resumed = resolve; });
  const callAgent: CallAgent = async request => {
    const result = (to?: string, letter = '') => ({ callId: 'controlled', threadId: 'session', completed: Boolean(to), error: to ? null : 'relay failed',
      handoff: to ? { form: request.form, to, letters: [letter] } : null, model: 'fixture', usage: null, ms: 1 });
    if (request.role === 'manager' && initial) { initial = false; return result(role === 'planner' ? 'planner' : 'engineer', 'Original brief'); }
    if (request.role === 'engineer') return result('manager', 'Team result');
    if (request.role !== role) return result('operator', 'Final answer');
    affected++;
    request.onStep?.({ kind: 'message', text: `Process ${affected}` });
    if (affected === 1) return result();
    if (affected === 2) {
      resumed();
      await new Promise<void>(resolve => request.signal!.addEventListener('abort', () => resolve(), { once: true }));
      return result();
    }
    return result(role === 'planner' ? 'manager' : 'operator', 'Final answer');
  };
  const idle = { since: null, calls: [], requests: [] };
  const rows = () => conversationRows(readRecord(store, project.id), store.load(project.id), idle);
  const shown = () => renderToStaticMarkup(<EventStream events={rows()} connected showReasoning={false} embedded />);
  const words = (row?: EventMsg) => [...row?.steps as string[] | undefined ?? [], ...row?.process as string[] | undefined ?? []];
  try {
    let flow = new Flow(store, callAgent);
    await flow.say(project.id, 'Do the work'); await flow.settled(project.id);
    const owner = rows().find(row => words(row).includes('Process 1'))!;
    expect(shown()).toContain('Process 1');
    controller = new AbortController();
    flow = new Flow(new Store(home), callAgent, { signal: controller.signal });
    flow.resume(project.id); await started;
    expect(shown()).toContain('Process 1'); expect(shown()).toContain('Process 2');
    flow.stop(project.id); controller.abort(); await flow.settled(project.id);
    for (const stage of ['stopped', 'fresh read']) {
      const snapshot = stage === 'stopped' ? rows() : conversationRows(readRecord(new Store(home), project.id), new Store(home).load(project.id), idle);
      const html = renderToStaticMarkup(<EventStream events={snapshot} connected showReasoning={false} embedded />);
      expect(html).toContain('Process 1'); expect(html).toContain('Process 2');
      expect(words(snapshot.find(row => row.row_id === owner.row_id))).toEqual(['Process 1', 'Process 2']);
    }
    flow = new Flow(new Store(home), callAgent); flow.resume(project.id); await flow.settled(project.id);
    const final = rows().find(row => row.row_id === owner.row_id)!;
    // Delivered, the turn's words are its process: kept, folded under the reply.
    expect(final.process).toEqual(['Process 1', 'Process 2', 'Process 3']); expect(final.order).toBe(owner.order);
    expect(final.steps).toBeUndefined();
    const html = shown(); for (const step of final.process as string[]) expect(html.slice(html.indexOf('data-manager-process'))).toContain(step);
    expect(html.match(/class="argus-reply-name"/g)).toHaveLength(2);
  } finally { rmSync(home, { recursive: true, force: true }); }
});
