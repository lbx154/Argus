import type { ReactNode } from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EventMsg, Snapshot } from '../api';
import { emptyMissionView } from '../../../core/src/missionView';
import { CopyButton } from '../components/CopyButton';
import { EventStream } from '../components/EventStream';
import { referenceText } from '../map/presentation';

vi.mock('../lib/motion', () => ({ useGsapMotion: () => {} }));
vi.mock('../components/MarkdownContent', () => ({
  MarkdownContent: ({ children }: { children: ReactNode }) => <div data-markdown>{children}</div>,
}));

function scrollNode() {
  let top = 0;
  return {
    clientHeight: 300, scrollHeight: 900,
    get scrollTop() { return top; },
    set scrollTop(value: number) { top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
    scrollTo(options: ScrollToOptions) { this.scrollTop = options.top ?? 0; },
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  };
}

let renderer: ReactTestRenderer | undefined;
let frames: Map<number, FrameRequestCallback>;
let inner: ReturnType<typeof scrollNode>;
let outer: ReturnType<typeof scrollNode>;
let nextFrame: number;

beforeEach(() => {
  frames = new Map();
  nextFrame = 0;
  inner = scrollNode();
  outer = scrollNode();
  vi.stubGlobal('window', {
    requestAnimationFrame: (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame: (id: number) => frames.delete(id),
    setInterval: vi.fn(() => 1),
    clearInterval: vi.fn(),
  });
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  vi.unstubAllGlobals();
});

const nodeMock = (element: { props: { className?: string } }) => {
  const classes = element.props.className ?? '';
  return classes.includes('max-h-72') ? inner : classes.includes('overflow-y-auto pb-6') ? outer : null;
};
const progress = (id: number, text = `Recorded finding ${id}`): EventMsg => ({
  type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer', text,
  event_id: `progress-${id}`, ts: id,
});
const feed = (events: EventMsg[]) => <EventStream events={events} connected showReasoning={false} onToggleReasoning={() => {}} />;
function mount(events: EventMsg[]) {
  act(() => { renderer = create(feed(events), { createNodeMock: nodeMock }); });
}
function update(events: EventMsg[]) {
  act(() => renderer!.update(feed(events)));
}
function paint() {
  const pending = [...frames.values()];
  frames.clear();
  act(() => pending.forEach(callback => callback(0)));
}
function roleGroup() {
  return renderer!.root.findByProps({ 'data-role': 'engineer' });
}
function log() {
  return roleGroup().find(node => node.type === 'div' && String(node.props.className).includes('max-h-72'));
}
function scrollTo(top: number) {
  inner.scrollTop = top;
  act(() => log().props.onScroll({ currentTarget: inner }));
}
function jumpButtons() {
  return roleGroup().findAll(node => node.type === 'button' && node.children.includes('Jump to latest'));
}
function visibleText(node: ReactTestInstance): string {
  return node.children.map(child => typeof child === 'string' ? child : visibleText(child)).join('');
}

describe('role log scroll following', () => {
  it('follows new events at the bottom, preserves history during append and streaming growth, and resumes on request', () => {
    mount([progress(1)]);
    paint();
    expect(inner.scrollTop).toBe(600);
    inner.scrollHeight = 1200;
    update([progress(1), progress(2)]);
    paint();
    expect(inner.scrollTop).toBe(900);

    scrollTo(120);
    expect(jumpButtons()).toHaveLength(1);
    inner.scrollHeight = 1500;
    update([progress(1), progress(2), progress(3)]);
    paint();
    expect(inner.scrollTop).toBe(120);
    inner.scrollHeight = 1800;
    update([progress(1), progress(2), progress(3, 'The newest finding is still growing in the same event.')]);
    paint();
    expect(inner.scrollTop).toBe(120);

    act(() => jumpButtons()[0].props.onClick());
    expect(inner.scrollTop).toBe(1500);
    expect(jumpButtons()).toHaveLength(0);
    inner.scrollHeight = 2000;
    update([progress(1), progress(2), progress(3), progress(4)]);
    paint();
    expect(inner.scrollTop).toBe(1700);
  });

  it('does not execute a queued follow after the reader scrolls up, and detects manually returning to the bottom', () => {
    mount([progress(1)]);
    paint();
    inner.scrollHeight = 1400;
    update([progress(1), progress(2)]);
    scrollTo(80);
    paint();
    expect(inner.scrollTop).toBe(80);
    scrollTo(1100);
    expect(jumpButtons()).toHaveLength(0);
    inner.scrollHeight = 1600;
    update([progress(1), progress(2), progress(3)]);
    paint();
    expect(inner.scrollTop).toBe(1300);
  });

  it('keeps the reading position when the same role log is closed and reopened', () => {
    mount([progress(1)]);
    paint();
    scrollTo(150);
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    expect(roleGroup().props['data-open']).toBe('false');
    inner.scrollHeight = 1400;
    inner.scrollTop = 0; // A newly mounted scrolling element starts at the top.
    update([progress(1), progress(2)]);
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    paint();
    expect(inner.scrollTop).toBe(150);
    expect(jumpButtons()).toHaveLength(1);
  });
});

describe('conversation references', () => {
  it('shows task and step titles separately from the user text and keeps the complete reference available to copy', () => {
    const reference = { source: 'live:project-a', task_id: 'task-1', task_title: 'Check a special case',
      step_id: 'step-1', step_title: 'Compare the evidence', part: 2, event_ids: ['event-1', 'event-2'] };
    const message = referenceText(reference) + 'Please explain this in plain language.';
    mount([{ type: 'ui.operator', text: message, ts: 1 }]);
    const quote = renderer!.root.findByType('blockquote');
    expect(visibleText(quote)).toContain('Check a special case');
    expect(visibleText(quote)).toContain('Compare the evidence');
    expect(visibleText(quote)).toContain('Part 2');
    expect(visibleText(quote)).not.toContain('event-1');
    expect(renderer!.root.findByProps({ 'data-markdown': true }).children).toEqual(['Please explain this in plain language.']);
    expect(renderer!.root.findByType(CopyButton).props.text).toBe(message);
  });

  it.each([
    '[[Argus引用 broken]]\nDo not lose this text.',
    '[[Argus引用 {"source":"live:s","task_id":"t","task_title":{},"event_ids":[]}]]\nKeep the example.',
    '[[Argus引用 null]]\nThis is ordinary malformed text.',
    '[[Argus引用 {"source":"live:s","task_id":"t","task_title":"Example","event_ids":[3]}]]',
  ])('preserves malformed reference syntax as normal message text: %s', message => {
    mount([{ type: 'ui.operator', text: message, ts: 1 }]);
    expect(renderer!.root.findAllByType('blockquote')).toHaveLength(0);
    expect(renderer!.root.findByProps({ 'data-markdown': true }).children).toEqual([message]);
  });

  it('keeps multiple valid references while leaving malformed lines and normal text intact', () => {
    const first = { source: 'map', task_id: 'task-1', task_title: 'First task', event_ids: [] };
    const second = { ...first, task_id: 'task-2', task_title: 'Second task' };
    const body = '[[Argus引用 broken]]\nKeep my normal question.';
    mount([{ type: 'ui.operator', text: referenceText(first) + referenceText(second) + body, ts: 1 }]);
    expect(renderer!.root.findAllByType('blockquote').map(visibleText)).toEqual(['ReferenceFirst task', 'ReferenceSecond task']);
    expect(renderer!.root.findByProps({ 'data-markdown': true }).children).toEqual([body]);
  });
});

function runtimeFixture(alive = true) {
  const now = Date.now() / 1000;
  const missionView = emptyMissionView();
  Object.assign(missionView.mission, { id: 'task-a', title: 'Research task A', status: 'working', started_at: now - 20 });
  missionView.active_role = 'engineer';
  const snapshot: Snapshot = {
    session: { id: 'session', display_name: 'Research', objective: '', cwd: '/tmp', last_active: now },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: 30, backend: 'pi', global_daily_cap_usd: null },
    roles: [{ role: 'engineer', active: true, status: 'running', label: 'Working', age_s: 1, backend: 'pi', backend_label: 'Pi', model: 'model', effort: null }],
    backlog: [{ id: 'task-a', title: 'Research task A', objective: '', status: 'running', started_ts: now - 20, priority: 1 }],
    recent_events: [], mission_view: missionView,
  };
  const events = [{ ...progress(1), item_id: 'task-a', ts: now - 1 }];
  return { snapshot, missionView, events };
}

describe('project work status and conversation separation', () => {
  it('places work before the dialogue and offers a jump that pauses outer following until Jump to latest', () => {
    const events: EventMsg[] = [
      { type: 'ui.operator', text: 'Start research task A', ts: 1 },
      { ...progress(2), item_id: 'task-a' },
      { type: 'ui.argus', text: 'Latest answer', ts: 3 },
    ];
    mount(events);
    paint();
    const sections = renderer!.root.findAll(node => node.type === 'section'
      && (node.props['data-project-work'] || String(node.props.className).includes('conversation-thread')));
    expect(sections[0].props['data-project-work']).toBe(true);
    expect(visibleText(sections.at(-1)!)).toContain('Latest answer');
    expect(outer.scrollTop).toBe(600);
    const viewWork = renderer!.root.find(node => node.type === 'button' && node.children.includes('View work progress'));
    act(() => viewWork.props.onClick());
    expect(outer.scrollTop).toBe(0);
    outer.scrollHeight = 1200;
    update([...events, { ...progress(4), item_id: 'task-a' }]);
    paint();
    expect(outer.scrollTop).toBe(0);
    const resume = renderer!.root.findByProps({ 'aria-label': 'Jump to latest' });
    act(() => resume.props.onClick());
    paint();
    expect(outer.scrollTop).toBe(900);
    expect(renderer!.root.findAllByProps({ 'aria-label': 'Jump to latest' })).toHaveLength(0);
  });

  it('keeps ongoing task A work out of the later question B conversation and preserves the role log reading state', () => {
    const first: EventMsg[] = [
      { type: 'ui.operator', text: 'Start research task A', ts: 1 },
      { ...progress(2, 'Research task A first finding'), item_id: 'task-a' },
    ];
    mount(first);
    paint();
    scrollTo(140);
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    const latest = [...first,
      { type: 'ui.operator', text: 'Question B: explain this term', ts: 3 },
      { type: 'ui.argus', text: 'Answer to question B', ts: 4 },
      { ...progress(5, 'Research task A second finding'), item_id: 'task-a' },
    ];
    update(latest);
    const threads = renderer!.root.findAll(node => node.type === 'section' && String(node.props.className).includes('conversation-thread'));
    expect(threads).toHaveLength(2);
    expect(visibleText(threads[1])).toContain('Answer to question B');
    expect(visibleText(threads[1])).not.toContain('Research task A');
    expect(threads[1].findAllByProps({ 'data-role': 'engineer' })).toHaveLength(0);
    expect(renderer!.root.findAllByProps({ 'data-project-work': true })).toHaveLength(1);
    expect(roleGroup().props['data-open']).toBe('false');
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    paint();
    expect(inner.scrollTop).toBe(140);
    expect(visibleText(log())).toContain('Research task A first finding');
    expect(visibleText(log())).toContain('Research task A second finding');
  });

  it('preserves a task completion receipt in project work after a new question arrives', () => {
    const delivery = { schema_version: 1, delivery_id: 'delivery-a', kind: 'task_completed', item_id: 'task-a', title: 'Task A result', summary: 'Task A evidence', status: 'done', review_status: 'done', delivered_at: 4, primary_target: null, targets: [] };
    mount([
      { type: 'ui.operator', text: 'Question B', ts: 3 },
      { type: 'life.mission.completed', item_id: 'task-a', status: 'done', ts: 4, delivery },
    ]);
    const work = renderer!.root.findByProps({ 'data-project-work': true });
    expect(visibleText(work)).toContain('Task A result');
    const thread = renderer!.root.find(node => node.type === 'section' && String(node.props.className).includes('conversation-thread'));
    expect(visibleText(thread)).not.toContain('Task A result');
  });

  it('does not pulse paused or disconnected work, while keeping the latest role initially open', () => {
    const value = runtimeFixture(false);
    const show = (connected: boolean) => <EventStream {...value} connected={connected} showReasoning={false} onToggleReasoning={() => {}} />;
    act(() => { renderer = create(show(true), { createNodeMock: nodeMock }); });
    expect(roleGroup().props['data-active']).toBe('false');
    expect(roleGroup().props['data-open']).toBe('true');
    value.snapshot = { ...value.snapshot, daemon: { ...value.snapshot.daemon, alive: true } };
    act(() => renderer!.update(show(true)));
    expect(roleGroup().props['data-active']).toBe('true');
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    act(() => renderer!.update(show(false)));
    expect(roleGroup().props['data-active']).toBe('false');
    expect(roleGroup().props['data-open']).toBe('false');
    act(() => renderer!.update(show(true)));
    expect(roleGroup().props['data-open']).toBe('false');
  });

  it('gates legacy role and provider activity on the live connection too', () => {
    const events = [progress(1), { type: 'agent.io.start', call_id: 'call-1', ts: 2 }];
    act(() => { renderer = create(<EventStream events={events} connected={false} showReasoning={false} onToggleReasoning={() => {}} />, { createNodeMock: nodeMock }); });
    expect(roleGroup().props['data-active']).toBe('false');
    expect(visibleText(renderer!.root)).not.toContain('Argus is working in the background');
  });

  it('does not mark another task’s old role log active from the current task’s runtime', () => {
    const value = runtimeFixture();
    value.events = [{ ...value.events[0], item_id: 'task-b' }];
    act(() => { renderer = create(<EventStream {...value} connected showReasoning={false} onToggleReasoning={() => {}} />, { createNodeMock: nodeMock }); });
    expect(roleGroup().props['data-active']).toBe('false');
  });
});
