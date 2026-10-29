import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import type { NodeProps } from '@xyflow/react';
import { Store } from '../../../../packages/core/src/store';
import { conversationRows, projectRecord, progressMap, readEvents, snapshot } from '../../../../packages/core/src/serve/views';
import { I18nProvider, type Locale } from '../i18n';
import { TopBar } from '../components/TopBar';
import { DaemonManageModal } from '../components/DaemonManageModal';
import { EventStream } from './serverSnapshot';
import { buildMap, type MapEvent } from '../map/model';
import { layoutScene } from '../map/submap';
import { MacroTaskNode, type MacroNode } from '../map/MacroTaskNode';

vi.mock('@xyflow/react', async (original) => ({
  ...await original<typeof import('@xyflow/react')>(), Handle: () => null,
}));
afterEach(() => vi.unstubAllGlobals());

const store = new Store(fileURLToPath(new URL('./fixtures/home', import.meta.url)));
const original = store.load('p-c855623c');
const reading = projectRecord(readEvents(store, original.id));
const idle = { since: null, calls: [], requests: [] };

it.each<Locale>(['en', 'zh-CN'])('replays the same waiting, stopped and closed meanings in %s', locale => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  for (const [phase, words] of [
    ['asking', locale === 'en' ? 'Waiting for your answer' : '等你回答'],
    ['stopped', locale === 'en' ? 'Stopped' : '已停止'],
    ['closed', locale === 'en' ? 'Ended' : '已结束'],
    ['abandoned', locale === 'en' ? 'Ended' : '已结束'],
  ] as const) {
    // The host records closure for both acceptance and abandonment; the letter says which.
    const closed = phase === 'closed' || phase === 'abandoned';
    const task = { ...original.tasks[0]!, status: closed ? 'closed' as const : 'open' as const,
      note: { from: 'manager' as const, text: phase === 'abandoned' ? '这项任务放弃。' : '结果见信。' } };
    const work = { ...original, status: phase === 'asking' ? 'asking' as const : 'stopped' as const, tasks: [task],
      questions: phase === 'asking' ? [{ role: 'engineer' as const, task: task.id, question: '请确认。' }] : [], sessions: {} };
    const rows = conversationRows(reading, work, idle).filter(row => row.type === 'ui.task' && row.item_id === task.id);
    expect(rows[0]?.state).toBe(closed ? 'done' : phase === 'asking' ? 'asking' : 'paused');
    const chat = renderToStaticMarkup(<I18nProvider><EventStream snapshot={snapshot(store, work, idle)} events={rows} connected showReasoning={false} /></I18nProvider>);
    expect(chat).toContain(words);
    const map = progressMap(store, work, 0);
    const scene = layoutScene(buildMap(map.tasks.filter(item => item.id === task.id)), map.events as unknown as MapEvent[], locale === 'zh-CN');
    const card = scene.cards.find(card => card.task.id === task.id)!;
    const props: NodeProps<MacroNode> = {
      id: card.id, type: 'task', zIndex: 0, draggable: false, selected: false, dragging: false,
      selectable: true, deletable: false, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0,
      data: { ...card, layout: scene.layouts[card.id], frame: scene.frames[card.id],
        canvasSize: { width: 1440, height: 960 }, zh: locale === 'zh-CN', focused: false, detailed: false,
        paused: phase === 'stopped', readOnly: false, source: 'fixture', open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn() },
    };
    expect(renderToStaticMarkup(<MacroTaskNode {...props} />)).toContain(words);
    if (!closed) {
      const snap = snapshot(store, work, idle);
      const top = renderToStaticMarkup(<I18nProvider><TopBar snap={snap} streamOk onStart={vi.fn()} onStop={vi.fn()} onManage={vi.fn()} busy={false} /></I18nProvider>);
      expect(top).toContain(words);
      if (phase === 'stopped') expect(top).toContain(locale === 'en' ? 'Resume' : '继续运行');
    }
  }
});

it('keeps management to run details, naming and deletion, with running controlled in the header', () => {
  const html = renderToStaticMarkup(<DaemonManageModal open sid="p-fixture" name="Fixture" alive={false} busy={false}
    snapshot={snapshot(store, original, idle)} onClose={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} />);
  expect(html).toContain('Run details');
  expect(html).toContain('Display name');
  expect(html).toContain('Delete');
  for (const text of ['Background work', 'Resume', 'Stopped', 'manage.executor']) expect(html).not.toContain(text);
});
