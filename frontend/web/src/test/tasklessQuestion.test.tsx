import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { Store } from '../../../../packages/core/src/store';
import { snapshot } from '../../../../packages/core/src/serve/views';
import type { Snapshot } from '../api';
import { api } from '../api';
import { PendingBanner } from '../components/PendingBanner';
import { usePendingReplySession } from '../usePendingReplySession';
import * as i18n from '../i18n';

vi.mock('../api', () => ({ api: { resolveDecision: vi.fn() } }));
let renderer: ReactTestRenderer | undefined;
let home: string | undefined;
afterEach(() => {
  act(() => renderer?.unmount());
  if (home) rmSync(home, { recursive: true, force: true });
  vi.restoreAllMocks();
});

it('keeps a Planner question taskless from the server snapshot through the banner and delivery notice', async () => {
  home = mkdtempSync(join(tmpdir(), 'argus-taskless-question-'));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '确认研究范围', backend: 'codex', model: null });
  const question = '请确认研究范围。';
  project.status = 'asking';
  project.questions = [{ role: 'planner', task: null, question }];
  store.save(project);
  store.append(project.id, { type: 'handoff', role: 'planner', to: 'operator', letter: question });
  const snap = snapshot(store, project, { since: null, calls: [], requests: [] });
  expect(snap.pending_questions?.[0]).toMatchObject({
    id: 'planner', title: 'The Planner asks',
    operator_decision: { id: 'planner', item_id: '', question },
  });
  vi.spyOn(i18n, 'useI18n').mockReturnValue({ locale: 'zh-CN', setLocale: vi.fn(), t: (key, vars) => i18n.translate(key, vars, 'zh-CN') });
  vi.mocked(api.resolveDecision).mockResolvedValue({ resolved: true });
  const notify = vi.fn();
  let session!: ReturnType<typeof usePendingReplySession>;
  function QuestionPath({ snap }: { snap: Snapshot }) {
    session = usePendingReplySession({ activeSid: snap.session.id, pendingQuestions: snap.pending_questions,
      backlog: snap.backlog, notify, refetchSnapshot: async () => {} });
    return session.pendingReply ? <PendingBanner reply={session.pendingReply} busy={session.pendingReplyBusy}
      error={session.pendingReplyError} note={session.pendingReplyNote} onNoteChange={session.setPendingReplyNote}
      onSubmit={session.answerPendingReply} /> : null;
  }
  act(() => { renderer = create(<QuestionPath snap={snap} />); });
  expect(session.pendingReply.item_id).toBe('');
  expect(session.pendingReply.task_title).toBeUndefined();
  const words = () => renderer!.root.findAll(node => typeof node.type === 'string')
    .flatMap(node => node.children.filter(child => typeof child === 'string')).join('');
  expect(words()).toContain(`规划者问：${question}`);
  expect(words()).not.toContain('The Planner asks');
  expect(words()).not.toContain('Argus');
  act(() => renderer!.root.findByType('button').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: '只研究已有数据。' } }));
  await act(async () => renderer!.root.findByType('form').props.onSubmit({ preventDefault: () => {} }));
  expect(api.resolveDecision).toHaveBeenCalledExactlyOnceWith(project.id, 'planner', 'custom', '只研究已有数据。');
  expect(notify).toHaveBeenCalledExactlyOnceWith('success', '答复已交给规划者。');
});
