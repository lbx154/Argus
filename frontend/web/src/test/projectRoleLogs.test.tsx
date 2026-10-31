import { readFileSync } from 'node:fs';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { projectRecord, type Recorded } from '../../../../packages/core/src/serve/views';
import { ProjectRoleLogs } from '../components/EventStream';
import { I18nProvider } from '../i18n';
import * as feed from '../lib/feedSteps';
import { roleClient, roleSnapshot } from './serverSnapshot';

const record = readFileSync(new URL('./fixtures/home/projects/p-c855623c/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line) as Recorded);
const events = projectRecord(record).roleEvents;
let view: ReactTestRenderer | undefined;
afterEach(() => { act(() => view?.unmount()); view = undefined; vi.restoreAllMocks(); });

it('skips saved log formatting while hidden and can show work or its saved details on the same mount', () => {
  const render = vi.spyOn(feed, 'renderFeedRows');
  const snapshot = roleSnapshot(events);
  const show = (connected: boolean, details = false, taskId?: string) =>
    <QueryClientProvider client={roleClient}><ProjectRoleLogs snapshot={snapshot} connected={connected} details={details} taskId={taskId} /></QueryClientProvider>;
  act(() => { view = create(show(false)); });
  expect(view!.toJSON()).toBeNull();
  expect(render).not.toHaveBeenCalled();
  act(() => view!.update(show(true, false, 'inactive-task')));
  expect(view!.toJSON()).toBeNull();
  expect(render).not.toHaveBeenCalled();
  act(() => view!.update(show(true)));
  expect(view!.root.findAllByProps({ 'data-project-work': true })).toHaveLength(1);
  expect(render).toHaveBeenCalledOnce();
  snapshot.work_status = { ...snapshot.work_status!, state: 'idle', roles: [] };
  render.mockClear();
  act(() => view!.update(show(true)));
  expect(view!.toJSON()).toBeNull();
  expect(render).not.toHaveBeenCalled();
  act(() => view!.update(show(false, true)));
  expect(render).toHaveBeenCalledOnce();
  expect(render.mock.results[0].value.list.length).toBeGreaterThan(0);
  expect(view!.root.findAllByProps({ 'data-project-work': true })).toHaveLength(1);
});

it('says, on a folded role, the heading of what it last thought and when; nothing on one that has not', () => {
  const last = events.at(-1)!;
  const thought = { type: 'engineer.progress', agent_layer: 'engineer', kind: 'reasoning', text: '**Checking the parser**\n\nBoth formats parse.',
    event_id: Number(last.event_id) + 1, ts: Number(last.ts) + 1 };
  const snapshot = roleSnapshot([...events, thought]);
  act(() => { view = create(<QueryClientProvider client={roleClient}><ProjectRoleLogs snapshot={snapshot} connected details /></QueryClientProvider>); });
  const shown = view!.root.findAll(node => node.type === 'section' && typeof node.props['data-role'] === 'string')
    .map(group => [group.props['data-role'], group.findAll(node => node.props['data-role-thought'] === true).map(node => node.children.join(''))]);
  expect(shown).toContainEqual(['engineer', ['Checking the parser']]);
  expect(shown.filter(([role]) => role !== 'engineer').every(([, said]) => said.length === 0)).toBe(true);
});

it('real Manager messages remain separate and ordered around commands, including a repeated sentence', () => {
  const gpu = readFileSync(new URL('./fixtures/home/projects/p-0444b8d3/events.jsonl', import.meta.url), 'utf8')
    .trim().split('\n').slice(0, 22).map(line => JSON.parse(line) as Recorded);
  const first = gpu.find(event => event.type === 'step' && event.kind === 'message')!;
  // Repeat the recorded sentence after the recorded command tail.
  const repeated = [...gpu, { ...first, ts: gpu.at(-1)!.ts }];
  const snapshot = roleSnapshot(projectRecord(repeated).roleEvents);
  act(() => { view = create(<QueryClientProvider client={roleClient}><ProjectRoleLogs snapshot={snapshot} connected details /></QueryClientProvider>); });
  const manager = view!.root.findByProps({ 'data-role': 'manager' });
  act(() => manager.findAllByType('button')[0]!.props.onClick());
  act(() => manager.findByProps({ 'data-command-toggle': true }).props.onClick());
  while (manager.findAllByProps({ 'data-role-earlier': true }).length) {
    act(() => manager.findByProps({ 'data-role-earlier': true }).props.onClick());
  }
  const entries = manager.findAll(node => typeof node.type === 'function' && node.type.name === 'EventRow').map(node => node.props.ev);
  const messages = entries.filter(event => event.kind === 'agent_message');
  expect(messages.map(event => event.text)).toEqual(repeated.filter(event => event.type === 'step' && event.kind === 'message').map(event => (event as Extract<Recorded, { type: 'step' }>).text));
  expect(messages).toHaveLength(4);
  expect(messages[0].text).toBe(messages[3].text);
  for (let index = 1; index < messages.length; index++) {
    const before = entries.indexOf(messages[index - 1]);
    const after = entries.indexOf(messages[index]);
    expect(entries.slice(before + 1, after).some(event => event.kind === 'command_execution')).toBe(true);
  }
  expect(manager.findAllByProps({ 'data-role-log': true })).toHaveLength(1);
});

it('uses an existing Chinese role action for an English-only thought in a collapsed log', () => {
  vi.stubGlobal('localStorage', { getItem: () => 'zh-CN' });
  vi.stubGlobal('document', { documentElement: {} });
  const snapshot = roleSnapshot([...events, { type: 'engineer.progress', agent_layer: 'engineer', kind: 'reasoning',
    text: '**Validating snapshot data and cutoff handling**', event_id: 'r23', ts: Date.now() / 1000 }]);
  try {
    act(() => { view = create(<QueryClientProvider client={roleClient}><I18nProvider><ProjectRoleLogs snapshot={snapshot} connected details /></I18nProvider></QueryClientProvider>); });
    const role = view!.root.findAll(node => node.type === 'section' && node.props['data-role'] === 'engineer')[0]!;
    expect(role.findByProps({ 'data-role-thought': true }).children.join('')).toBe('正在处理当前任务');
  } finally { vi.unstubAllGlobals(); }
});
