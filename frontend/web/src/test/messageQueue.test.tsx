import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { Store } from '../../../../packages/core/src/store';
import { conversationRows, projectRecord, type Live, type Recorded } from '../../../../packages/core/src/serve/views';
import record from '../../../../packages/core/test/fixtures/message-queue/record.json';
import { ChatBox } from '../components/ChatBox';
import { mergeConversationEvents, optimisticOperatorEvent } from '../lib/conversationEvents';
import { EventStream, roleSnapshot } from './serverSnapshot';

afterEach(() => vi.unstubAllGlobals());

it('renders local placeholders and refreshed rows through waiting, running and completion', () => {
  const home = mkdtempSync(join(tmpdir(), 'argus-queue-display-'));
  try {
    const project = new Store(home).create({ workdir: '', request: record.messages[0].text, backend: 'codex', model: null });
    project.status = 'stopped';
    project.forManager = [...record.messages];
    const snapshot = roleSnapshot([], { session: { id: project.id, display_name: '', objective: '', workdir: '', last_active: 1 }, work_status: undefined });
    const local = record.messages.flatMap(({ text, request_id }, index) => [
      optimisticOperatorEvent(project.id, request_id, text, 1000 + index),
    ]);
    const replay = record.events as Recorded[];
    const events = replay.slice(0, 3);
    const live: Live = { since: 1, calls: [{ role: 'manager', task: undefined, started_ts: 1, answers: 'r1' }], requests: ['r1', 'r2'] };
    const render = (rows = conversationRows(projectRecord(events), project, live)) => renderToStaticMarkup(
      <EventStream events={mergeConversationEvents(rows, local)} snapshot={snapshot} connected showReasoning={false} embedded />,
    );
    // Manager status appears only after the server accepts the messages.
    const initial = render([]);
    expect(initial).not.toContain('data-manager-reply=');
    expect(initial).not.toContain('data-tone="running"');
    // A recorded first request does not invent a status for the second local send.
    const partial = render(conversationRows(projectRecord(events.slice(0, 1)), project, live));
    expect(partial.match(/data-manager-reply="running"/g)).toHaveLength(1);
    expect(partial).not.toContain('data-manager-reply="waiting"');
    // Once both requests are recorded, the actual queue has one waiting row.
    for (const html of [render()]) {
      expect(html.match(/data-manager-reply="running"/g)).toHaveLength(1);
      expect(html.match(/data-manager-reply="waiting"/g)).toHaveLength(1);
      expect(html.match(/Waiting to be processed/g)).toHaveLength(1);
      expect(html.match(/data-tone="running"/g)).toHaveLength(1);
    }
    // The existing hand-off ends the first row; the next actual call owns the running state.
    events.push(...replay.slice(3, 6));
    live.calls[0].answers = 'r2';
    live.requests = ['r2'];
    project.forManager = [record.messages[1]];
    const second = render();
    expect(second).toContain('第一条已回答。');
    expect(second.match(/data-manager-reply="running"/g)).toHaveLength(1);
    expect(second).not.toContain('Waiting to be processed');
    events.push(...replay.slice(6));
    live.calls = [];
    live.requests = [];
    project.forManager = [];
    const ended = render();
    expect(ended).toContain('第一条已回答。');
    expect(ended).toContain('第二条已回答。');
    expect(ended).not.toContain('data-manager-reply=');
  } finally { rmSync(home, { recursive: true, force: true }); }
});

it('keeps waiting in the conversation after sending, while preserving the upload notice', async () => {
  vi.stubGlobal('document', { addEventListener() {}, removeEventListener() {}, activeElement: null });
  vi.stubGlobal('window', { addEventListener() {}, removeEventListener() {}, innerWidth: 800 });
  const props = { value: 'second', onChange() {}, onSend: vi.fn(async () => true), disabled: false, pending: true,
    attachments: [], onAttachmentsChange() {}, completionSelection: 0, onCompletionSelectionChange() {} };
  let view!: ReturnType<typeof create>;
  act(() => { view = create(<ChatBox {...props} />); });
  try {
    await act(async () => { view.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
    expect(props.onSend).toHaveBeenCalledWith('second', [], { whileRunning: true });
    expect(JSON.stringify(view.toJSON())).not.toContain('Queued');
    act(() => view.update(<ChatBox {...props} pendingNote="Waiting for uploads; sends automatically when they finish" />));
    expect(JSON.stringify(view.toJSON())).toContain('Waiting for uploads; sends automatically when they finish');
  } finally { act(() => view.unmount()); }
});
