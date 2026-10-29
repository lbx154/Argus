import { roleSnapshot, setRoleLog } from './serverSnapshot';
import type { ReactNode } from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EventMsg, Snapshot } from '../api';
import { CopyButton } from '../components/CopyButton';
import { EventStream } from './serverSnapshot';
import { referenceText } from '../map/presentation';

vi.mock('../lib/motion', () => ({ useGsapMotion: () => {} }));
vi.mock('../components/MarkdownContent', () => ({
  MarkdownContent: ({ children }: { children: ReactNode }) => <div data-markdown>{children}</div>,
}));

function scrollNode() {
  let top = 0;
  return {
    clientHeight: 300, clientWidth: 1000, scrollHeight: 900,
    get scrollTop() { return top; },
    set scrollTop(value: number) { top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
    scrollTo(options: ScrollToOptions) { this.scrollTop = options.top ?? 0; },
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  };
}

let renderer: ReactTestRenderer | undefined;
let frames: Map<number, FrameRequestCallback>;
let outer: ReturnType<typeof scrollNode>;
let content: object;
let observers: { targets: Set<object>; notify: () => void }[];
let nextFrame: number;

beforeEach(() => {
  frames = new Map();
  nextFrame = 0;
  outer = scrollNode();
  content = {};
  observers = [];
  vi.stubGlobal('ResizeObserver', class {
    record: typeof observers[number];
    constructor(notify: () => void) {
      this.record = { targets: new Set(), notify };
      observers.push(this.record);
    }
    observe(target: object) { this.record.targets.add(target); }
    disconnect() { this.record.targets.clear(); }
  });
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

const nodeMock = (element: { props: { className?: string; 'data-event-stream-content'?: boolean } }) => {
  if (element.props['data-event-stream-content']) return content;
  const classes = element.props.className ?? '';
  return classes.includes('overflow-y-auto pb-6') ? outer : null;
};
const progress = (id: number, text = `Recorded finding ${id}`): EventMsg => ({
  type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer', text,
  event_id: `progress-${id}`, ts: id,
});
const feed = (events: EventMsg[]) => <EventStream events={events} snapshot={roleSnapshot(events.filter(event => event.type === 'engineer.progress' || String(event.type).startsWith('round.')))} connected showReasoning={false} />;
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
function resize(target: object) {
  const matching = observers.filter(observer => observer.targets.has(target));
  expect(matching.length).toBeGreaterThan(0);
  act(() => matching.forEach(observer => observer.notify()));
}
function scrollConversation(top: number) {
  outer.scrollTop = top;
  const listener = outer.addEventListener.mock.calls.find(([name]) => name === 'scroll')![1];
  act(() => listener());
}
function timelineSections() {
  return renderer!.root.findAll(node => node.type === 'section'
    && (node.props['data-task-work'] || String(node.props.className).includes('conversation-thread')));
}
function streamOrder() {
  return timelineSections().map(section => section.props['data-task-work'] ? `task ${section.props['data-task-work']}` : 'thread');
}
function work() {
  return renderer!.root.findByProps({ 'data-project-work': true });
}
function visibleText(node: ReactTestInstance): string {
  return node.children.map(child => typeof child === 'string' ? child : visibleText(child)).join('');
}

describe('role logs in the main conversation scroll', () => {
  it('stays collapsed during a run and as new logs arrive', () => {
    mount([progress(1)]);
    expect(roleGroup().props['data-open']).toBe('false');
    update([progress(1), progress(2)]);
    expect(roleGroup().props['data-open']).toBe('false');
  });
  it('shows five recent rows, expands earlier rows on request, and has no inner scroller', () => {
    mount(Array.from({ length: 14 }, (_, i) => progress(i + 1)));
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    const log = () => roleGroup().findByProps({ 'data-role-log': true });
    expect(visibleText(log())).toContain('Recorded finding 14');
    expect(visibleText(log())).not.toContain('Recorded finding 9');
    expect(log().findAll(node => String(node.props.className).includes('overflow-y-auto'))).toHaveLength(0);
    act(() => roleGroup().findByProps({ 'data-role-earlier': true }).props.onClick());
    expect(visibleText(log())).toContain('Recorded finding 5');
    act(() => roleGroup().findByProps({ 'data-role-earlier': true }).props.onClick());
    expect(visibleText(log())).toContain('Recorded finding 1');
    expect(roleGroup().findAllByProps({ 'data-role-earlier': true })).toHaveLength(0);
  });
});

describe('conversation scroll following during reflow', () => {
  const events: EventMsg[] = [{ type: 'ui.argus', text: 'A recorded answer with wrapping text.', ts: 1 }];
  const resumeButtons = () => renderer!.root.findAllByProps({ 'aria-label': 'Jump to latest' });

  it('keeps following when growing history fires a scroll event without an upward reader movement', () => {
    mount(events);
    paint();
    scrollConversation(600);
    outer.scrollHeight = 1800;
    resize(content);
    // Layout/scroll anchoring can dispatch scroll before the queued resize frame.
    scrollConversation(600);
    paint();
    expect(outer.scrollTop).toBe(1500);
    expect(resumeButtons()).toHaveLength(0);
  });

  it('stays at the latest answer after viewport and content size changes without new events', () => {
    mount(events);
    paint();
    expect(outer.scrollTop).toBe(600);
    outer.clientWidth = 390;
    outer.clientHeight = 168;
    outer.scrollHeight = 1800;
    resize(outer);
    paint();
    expect(outer.scrollTop).toBe(1632);
    expect(resumeButtons()).toHaveLength(0);

    // Markdown or a disclosure can grow while the scroll viewport stays fixed.
    outer.scrollHeight = 2100;
    resize(content);
    paint();
    expect(outer.scrollTop).toBe(1932);
    expect(resumeButtons()).toHaveLength(0);
  });

  it('keeps the manual history position through reflow and resumes resize following on request', () => {
    mount(events);
    paint();
    scrollConversation(120);
    expect(resumeButtons()).toHaveLength(1);
    outer.clientWidth = 390;
    outer.clientHeight = 168;
    outer.scrollHeight = 1800;
    resize(outer);
    resize(content);
    paint();
    expect(outer.scrollTop).toBe(120);
    expect(resumeButtons()).toHaveLength(1);

    act(() => resumeButtons()[0].props.onClick());
    paint();
    expect(outer.scrollTop).toBe(1632);
    expect(resumeButtons()).toHaveLength(0);
    outer.clientHeight = 300;
    resize(outer);
    paint();
    expect(outer.scrollTop).toBe(1500);
  });

  it('lets a manual scroll cancel a queued resize follow and cleans up on unmount', () => {
    mount(events);
    paint();
    outer.scrollHeight = 1800;
    resize(content);
    scrollConversation(80);
    paint();
    expect(outer.scrollTop).toBe(80);
    expect(resumeButtons()).toHaveLength(1);

    act(() => resumeButtons()[0].props.onClick());
    paint();
    resize(outer);
    expect(frames.size).toBeGreaterThan(0);
    act(() => renderer!.unmount());
    renderer = undefined;
    expect(frames.size).toBe(0);
    expect(observers.every(observer => observer.targets.size === 0)).toBe(true);
  });
});

describe('conversation history readiness', () => {
  it('distinguishes loading, failed history and a confirmed empty conversation', () => {
    const retry = vi.fn();
    const view = (historyStatus: 'loading' | 'error' | 'ready') => <EventStream
      events={[]} connected showReasoning={false}
      historyStatus={historyStatus} onRetryHistory={retry} />;
    act(() => { renderer = create(view('loading'), { createNodeMock: nodeMock }); });
    expect(visibleText(renderer!.root)).toContain('Loading conversation history');
    expect(visibleText(renderer!.root)).not.toContain('Argus is ready');
    act(() => renderer!.update(view('error')));
    expect(visibleText(renderer!.root)).toContain('Could not load conversation history');
    expect(visibleText(renderer!.root)).not.toContain('Argus is ready');
    const button = renderer!.root.findAllByType('button').find(node => visibleText(node) === 'Retry')!;
    act(() => button.props.onClick());
    expect(retry).toHaveBeenCalledTimes(1);
    act(() => renderer!.update(view('ready')));
    expect(visibleText(renderer!.root)).toContain('Argus is ready');
  });

  it('retains loaded messages when refreshing their history fails', () => {
    act(() => { renderer = create(<EventStream
      events={[{ type: 'ui.argus', text: 'A previously loaded answer.', ts: 1 }]}
      connected showReasoning={false} historyStatus="error"
      onRetryHistory={() => {}} />, { createNodeMock: nodeMock }); });
    expect(visibleText(renderer!.root)).toContain('A previously loaded answer.');
    expect(visibleText(renderer!.root)).toContain('Could not load conversation history');
    expect(visibleText(renderer!.root)).not.toContain('Argus is ready');
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
  const snapshot: Snapshot = {
    session: { id: 'session', display_name: 'Research', objective: '', workdir: '/tmp', last_active: now },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: 30, backend: 'pi'},
    roles: [{ role: 'engineer', active: true, status: 'running', label: 'Working', age_s: 1, backend: 'pi', backend_label: 'Pi', model: 'model', effort: null }],
    backlog: [{ id: 'task-a', title: 'Research task A', objective: '', status: 'running', started_ts: now - 20, priority: 1 }],

    work_status: { state: alive ? 'running' : 'idle', roles: alive ? ['engineer'] : [], role: '', taskId: '', title: '', reason: '', foreground: false, activityAt: null, label: { 'zh-CN': '', en: '' } },
  };
  const events = [{ ...progress(1), item_id: 'task-a', ts: now - 1 }];
  setRoleLog(snapshot.session.id, events);
  return { snapshot, events };
}

it('restores an opted-in conversation reading position without jumping to the latest reply', () => {
  const { snapshot } = runtimeFixture(false);
  const setItem = vi.fn();
  vi.stubGlobal('localStorage', {
    getItem: () => JSON.stringify({ top: 140, following: false }),
    setItem,
  });
  act(() => { renderer = create(<EventStream events={[{ type: 'ui.argus', text: 'Earlier result', ts: 1 }]}
    snapshot={snapshot} connected showReasoning={false} rememberPosition embedded />,
    { createNodeMock: nodeMock }); });
  paint();
  expect(outer.scrollTop).toBe(140);
  outer.scrollTop = 180;
  const onScroll = outer.addEventListener.mock.calls.find(call => call[0] === 'scroll')![1];
  act(() => onScroll());
  expect(setItem).toHaveBeenCalledWith('argus.conversation.position.v1:session', JSON.stringify({ top: 180, following: false }));
});

it('waits for ready history before choosing search over the saved position, then keeps it through updates', () => {
  const { snapshot } = runtimeFixture(false);
  vi.stubGlobal('localStorage', { getItem: () => JSON.stringify({ top: 140, following: false }), setItem: vi.fn() });
  const target = { dataset: {} as Record<string, string>, closest: () => null,
    scrollIntoView: vi.fn(() => { outer.scrollTop = 260; }) };
  content = { querySelector: () => target, querySelectorAll: () => [] };
  const events: EventMsg[] = [{ type: 'ui.argus', text: 'Search hit', ts: 1 }];
  const show = (ready: boolean, rows = events) => <EventStream events={rows} snapshot={snapshot} connected showReasoning={false}
    requestedMessageTs={1} historyStatus={ready ? 'ready' : 'loading'} rememberPosition embedded />;
  act(() => { renderer = create(show(false), { createNodeMock: nodeMock }); });
  resize(content); paint();
  expect(target.scrollIntoView).not.toHaveBeenCalled();
  expect(outer.scrollTop).toBe(0);
  act(() => renderer!.update(show(true)));
  resize(content); paint();
  expect(target.scrollIntoView).toHaveBeenCalledOnce();
  expect(outer.scrollTop).toBe(260);
  expect(target.dataset.searchHit).toBe('true');
  outer.scrollHeight = 1200;
  act(() => renderer!.update(show(true, [...events, { type: 'ui.argus', text: 'New content', ts: 2 }])));
  resize(content); paint();
  expect(target.scrollIntoView).toHaveBeenCalledOnce();
  expect(outer.scrollTop).toBe(260);
});

it('clears a consumed search without restoring again, and can locate the same row on a later search', () => {
  const target = { dataset: {}, closest: () => null, scrollIntoView: vi.fn(() => { outer.scrollTop = 260; }) };
  content = { querySelector: () => target, querySelectorAll: () => [] };
  const located = vi.fn();
  const show = (requestedMessageTs?: number) => <EventStream events={[]} connected showReasoning={false}
    requestedMessageTs={requestedMessageTs} onMessageLocated={located} />;
  act(() => { renderer = create(show(1), { createNodeMock: nodeMock }); });
  expect(located).toHaveBeenCalledOnce();
  act(() => renderer!.update(show()));
  resize(content); paint();
  expect(outer.scrollTop).toBe(260);
  scrollConversation(180);
  act(() => renderer!.update(show(1)));
  expect(target.scrollIntoView).toHaveBeenCalledTimes(2);
  expect(located).toHaveBeenCalledTimes(2);
  expect(outer.scrollTop).toBe(260);
});

it('loads older conversation pages until the requested visible row exists and opens a folded brief', () => {
  const load = vi.fn();
  const brief = { open: false };
  const target = { dataset: {}, closest: () => brief, scrollIntoView: vi.fn() };
  let found = false;
  content = { querySelector: () => found ? target : null, querySelectorAll: () => [] };
  const show = (loadingOlder: boolean) => <EventStream events={[]} connected showReasoning={false}
    requestedMessageTs={1} hasOlder loadingOlder={loadingOlder} onLoadOlder={load} />;
  act(() => { renderer = create(show(false), { createNodeMock: nodeMock }); });
  expect(load).toHaveBeenCalledOnce();
  act(() => renderer!.update(show(true)));
  expect(target.scrollIntoView).not.toHaveBeenCalled();
  found = true;
  act(() => renderer!.update(show(false)));
  expect(brief.open).toBe(true);
  expect(target.scrollIntoView).toHaveBeenCalledOnce();
});

it('uses the bottom only once when history becomes ready without a search or a saved position', () => {
  const events: EventMsg[] = [{ type: 'ui.argus', text: 'Completed result', ts: 1 }];
  const show = (ready: boolean) => <EventStream events={events} connected showReasoning={false} historyStatus={ready ? 'ready' : 'loading'} />;
  act(() => { renderer = create(show(false), { createNodeMock: nodeMock }); });
  resize(content); paint();
  expect(outer.scrollTop).toBe(0);
  act(() => renderer!.update(show(true)));
  expect(outer.scrollTop).toBe(600);
  scrollConversation(160);
  act(() => renderer!.update(show(false)));
  act(() => renderer!.update(show(true)));
  resize(content); paint();
  expect(outer.scrollTop).toBe(160);
});

it('preserves a recorded reply and its copy action, including runtime-like research text', () => {
  const text = '\nResult: dimension 6.\nInfo: Operation cancelled by user\nMILESTONE_STATUS=literal research sample\n';
  mount([{ type: 'ui.argus', text, ts: 1 }]);
  expect(renderer!.root.findByProps({ 'data-markdown': true }).children).toEqual([text]);
  expect(renderer!.root.findByType(CopyButton).props.text).toBe(text);
});

describe('server conversation rows and role channel', () => {
  it('keeps server question, answer and completed task in their supplied order', () => {
    const { snapshot } = runtimeFixture();
    setRoleLog(snapshot.session.id, [progress(5, 'This round only')]);
    const events: EventMsg[] = [
      { type: 'ui.question', question_id: 'q1', item_id: 'task-a', agent_layer: 'engineer', text: 'Which option?', row_id: 'q1', ts: 1 },
      { type: 'ui.operator', answer_to: 'q1', text: 'Option A', row_id: 'a1', ts: 3 },
      { type: 'ui.task', item_id: 'task-a', title: 'Research task A', state: 'done', row_id: 't1', ts: 4 },
    ];
    act(() => { renderer = create(<EventStream events={events} snapshot={snapshot} connected showReasoning={false} />, { createNodeMock: nodeMock }); });
    expect(visibleText(renderer!.root)).toContain('Engineer asks about “Research task A”: Which option?');
    expect(streamOrder()).toEqual(['thread', 'task task-a']);
    expect(visibleText(renderer!.root.findByProps({ 'data-task-work': 'task-a' }))).toBe('Ended: Research task A');
    expect(visibleText(work())).not.toContain('This round only');
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    expect(visibleText(work())).toContain('This round only');
  });

  it('replaces the role panel with the next server round, including removing inactive roles', () => {
    const before = roleSnapshot([progress(1, 'Old finding')]);
    const events: EventMsg[] = [{ type: 'ui.operator', text: 'New request', ts: 3 }];
    const show = (snapshot: Snapshot) => <EventStream events={events} snapshot={snapshot} connected showReasoning={false} />;
    act(() => { renderer = create(show(before), { createNodeMock: nodeMock }); });
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    expect(visibleText(work())).toContain('Old finding');
    act(() => renderer!.update(show(roleSnapshot([{ ...progress(4, 'Current answer'), event_id: 4, agent_layer: 'manager' }]))));
    expect(visibleText(work())).not.toContain('Old finding');
    expect(renderer!.root.findAllByProps({ 'data-role': 'engineer' })).toHaveLength(0);
    act(() => renderer!.root.findByProps({ 'data-role': 'manager' }).findAllByType('button')[0].props.onClick());
    expect(visibleText(work())).toContain('Current answer');
  });

  it('folds the conversation role bar when work ends or disconnects, while keeping the saved log', () => {
    const value = runtimeFixture();
    const show = (connected: boolean) => <EventStream {...value} connected={connected} showReasoning={false} />;
    act(() => { renderer = create(show(true), { createNodeMock: nodeMock }); });
    expect(roleGroup().props['data-active']).toBe('true');
    act(() => renderer!.update(show(false)));
    expect(renderer!.root.findAllByProps({ 'data-project-work': true })).toHaveLength(0);
    value.snapshot.work_status = { ...value.snapshot.work_status!, state: 'idle', roles: [] };
    act(() => renderer!.update(show(true)));
    expect(renderer!.root.findAllByProps({ 'data-project-work': true })).toHaveLength(0);
  });
});

describe('project work progress in plain words', () => {
  let ts = 0;
  const reviewer = (kind: string, text: string): EventMsg => ({ type: 'engineer.progress', kind, agent_layer: 'reviewer', text, event_id: `r${++ts}`, ts });
  const engineer = (kind: string, text: string, extra: Record<string, unknown> = {}): EventMsg => ({ type: 'engineer.progress', kind, agent_layer: 'engineer', text, event_id: `e${++ts}`, ts, ...extra });
  const header = (role: string) => visibleText(renderer!.root.findByProps({ 'data-role': role }).findAllByType('button')[0]).trim();
  const toggles = (role: string) => renderer!.root.findByProps({ 'data-role': role }).findAll(node => node.type === 'button' && node.props['data-command-toggle']);

  it('keeps the role header free of duplicate progress and folds readable process records', () => {
    // Trial s-7d7b7d22: the Reviewer's header read `"python - <<'PY'` and its last message ended in a bare "continue".
    const heredoc = `/usr/bin/bash -lc "python - <<'PY'\nfrom pathlib import Path`;
    mount([
      reviewer('agent_message', 'The survey covers every requirement; one record still names the wrong source.\n\ncontinue'),
      reviewer('command_execution', heredoc),
      reviewer('command_execution', "/usr/bin/bash -lc 'cat research-plan.md'"),
    ]);
    expect(header('reviewer')).toBe('Reviewer');
    const group = renderer!.root.findByProps({ 'data-role': 'reviewer' });
    expect(visibleText(group)).not.toMatch(/python|research-plan|continue/);
    act(() => group.findAllByType('button')[0].props.onClick());
    const [toggle] = toggles('reviewer');
    expect(toggle.props['aria-expanded']).toBe(false);
    expect(visibleText(toggle)).toBe('Show commands');

    act(() => toggle.props.onClick());
    expect(visibleText(toggles('reviewer')[0])).toBe('Hide commands');
    const commandRow = group.find(node => node.props.title === heredoc);
    expect(visibleText(commandRow)).toBe("python - <<'PY'");
    expect(visibleText(group)).toContain('cat research-plan.md');
    // Expanding the process does not add a second progress summary.
    expect(header('reviewer')).not.toMatch(/python|cat /);
  });

  it('groups tool steps and repeated commands once the commands are shown', () => {
    const view = (path: string) => engineer('tool_use', `view: ${JSON.stringify({ path })}`, { tool_name: 'view', status: 'running' });
    mount([view('a.py'), view('b.py'), view('c.py'), engineer('command_execution', 'pytest -q'), engineer('command_execution', 'pytest -q')]);
    // A collapsed role shows only its name; its process is available inside.
    expect(header('engineer')).toBe('Engineer');
    act(() => roleGroup().findAllByType('button')[0].props.onClick());
    act(() => toggles('engineer')[0].props.onClick());
    const group = renderer!.root.findByProps({ 'data-feed-group': 'read' });
    expect(group.props['data-open']).toBe('false');
    expect(visibleText(group)).toContain('Read 3 files: a.py, b.py, c.py');
    expect(group.findAllByProps({ title: 'Show each step' })[0].props['aria-expanded']).toBe(false);
    expect(renderer!.root.findAllByProps({ 'data-repeat': 2 })).toHaveLength(1);
  });

  it('says a verdict in one line with the rest folded, and the host’s climb record as written', () => {
    mount([
      { type: 'round.review.completed', status: 'replan_requested', review_source: 'unchanged_return', round_index: 2, item_id: 't',
        reason: 'This path is making no new progress, so the team is trying a different approach.', event_id: 'v1', ts: 1 },
      { type: 'round.review.completed', status: 'continue', review_source: 'reviewer', round_index: 1, item_id: 't',
        reason: 'The content passed; one internal record still needs a fix.', next_action: 'Correct the attribution in source-verification.md.', event_id: 'v2', ts: 2 },
    ]);
    const group = renderer!.root.findByProps({ 'data-role': 'reviewer' });
    act(() => group.findAllByType('button')[0].props.onClick());
    expect(visibleText(group)).toContain('This path is making no new progress, so the team is trying a different approach.');
    expect(visibleText(group)).not.toMatch(/replan_requested|\bcontinue\b/);
    expect(header('reviewer')).toBe('Reviewer');
    expect(group.findAllByProps({ 'data-full-text': true })).toHaveLength(0);
    const fullToggles = group.findAll(node => node.type === 'button' && node.props['data-full-toggle']);
    act(() => fullToggles[fullToggles.length - 1].props.onClick());
    expect(visibleText(group.findByProps({ 'data-full-text': true }))).toContain('Next: Correct the attribution in source-verification.md.');
  });
});

describe('result cards in the conversation', () => {
  const delivery = {
    schema_version: 1, delivery_id: 'delivery:task-a:task_completed:1', kind: 'task_completed', item_id: 'task-a',
    title: 'Sales chart', summary: '', status: 'done', review_status: 'done', delivered_at: 30,
    primary_target: { path: 'out/figure.png', label: 'figure.png', source: 'solo_output', },
    targets: [{ path: 'out/figure.png', label: 'figure.png', source: 'solo_output', }],
  };
  const events: EventMsg[] = [
    { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer', item_id: 'task-a', text: 'Drew the task A chart', event_id: 'a1', ts: 10 },
    { type: 'ui.argus', text: 'Here is the chart.', ts: 30, message_id: 'reply-a', delivery } as EventMsg,
    { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer', item_id: 'task-b', text: 'Working on task B now', event_id: 'b1', ts: 40 },
  ];

  it('lists an image it cannot preview', () => {
    mount(events);
    expect(visibleText(renderer!.root.findByProps({ 'data-delivery-kind': 'task_completed' }))).toContain('figure.png');
  });
});

it("shows a brief the Manager hands the team by its title, folded, and a reply to the operator as it is", () => {
  mount([
    { type: 'ui.operator', text: 'Compare the two sorts', ts: 1, message_id: 'm0-operator' },
    { type: 'ui.argus', agent_layer: 'manager', to: 'planner', text: '# Compare the two sorts\nSplit it into two tasks that run side by side.', ts: 2, message_id: 'm0-argus' },
    { type: 'ui.operator', text: 'hi', ts: 3, message_id: 'm2-operator' },
    { type: 'ui.argus', agent_layer: 'manager', to: 'operator', text: 'Hello.', ts: 4, message_id: 'm2-argus' },
  ]);
  const briefs = renderer!.root.findAllByProps({ 'data-testid': 'conversation-brief' });
  expect(briefs).toHaveLength(1);
  expect(briefs[0]!.type).toBe('details');
  const summary = briefs[0]!.findByType('summary');
  expect(summary.children.join('')).toContain('Handed to the Planner: ');
  expect(summary.findByType('span').children).toEqual(['Compare the two sorts']);
});

it('renders a stopped request and the current Manager sentence without a phase-step display', () => {
  mount([{ type: 'ui.operator', text: 'Keep my request', ts: 1 }, { type: 'ui.argus', status: 'stopped', text: '', ts: 1 }]);
  expect(visibleText(renderer!.root)).toContain('Stopped. Click Resume to continue the reply.');
  update([{ type: 'ui.argus', text: '', ts: 1, live: true, status: 'running' },
    { type: 'engineer.progress', agent_layer: 'manager', kind: 'agent_message', text: 'The latest recorded sentence', ts: 2 }]);
  expect(visibleText(renderer!.root)).toContain('The latest recorded sentence');
  expect(renderer!.root.findAllByProps({ 'aria-label': 'View work progress' })).toHaveLength(0);
});

it('shows the task of each record after a running role is opened', () => {
  const events = [progress(1, 'Checking primes.py.'), progress(2, 'Checking fib.py.')].map((event, i) => ({ ...event, item_id: `t${i + 1}` }));
  const snapshot = roleSnapshot(events, {
    daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'codex' },
    work_status: { state: 'running', roles: ['engineer'], role: '', taskId: '', title: '', reason: '', foreground: false, activityAt: null, label: { 'zh-CN': '', en: '' } },
    roles: [{ role: 'engineer', active: true, status: 'running', label: 'Engineer', age_s: null, backend: 'codex', backend_label: 'Codex', model: '', effort: null, tasks: ['t1', 't2'] }],
    backlog: ['primes', 'fib'].map((name, i) => ({ id: `t${i + 1}`, title: `Write ${name}.py`, objective: '', status: 'in_progress', priority: 1 })),
  });
  act(() => { renderer = create(<EventStream events={[]} snapshot={snapshot} connected showReasoning={false} />, { createNodeMock: nodeMock }); });
  expect(roleGroup().props['data-open']).toBe('false');
  act(() => roleGroup().findAllByType('button')[0].props.onClick());
  expect(visibleText(roleGroup().findByProps({ 'data-row-task': 't1' }))).toBe('Write primes.py');
  expect(visibleText(roleGroup().findByProps({ 'data-row-task': 't2' }))).toBe('Write fib.py');
});

it('keeps raw commands out of a running role header and its task headings until commands are requested', () => {
  const command = 'cat /private/raw-command-should-stay-hidden.txt';
  const snapshot = roleSnapshot([{ ...progress(1, command), kind: 'command_execution', item_id: 't1' }], {
    roles: [{ role: 'engineer', active: false, tasks: ['t1'], backend: 'codex', backend_label: 'Codex', model: '', effort: null, label: '', status: 'idle', age_s: null }],
    work_status: { state: 'running', roles: ['engineer'], role: 'engineer', taskId: '', title: '', reason: '', foreground: false, activityAt: null, label: { 'zh-CN': '', en: '' } },
  });
  act(() => { renderer = create(<EventStream events={[]} snapshot={snapshot} connected showReasoning={false} />, { createNodeMock: nodeMock }); });
  expect(roleGroup().props['data-active']).toBe('true');
  expect(visibleText(roleGroup())).toBe('Engineer');
  expect(JSON.stringify(renderer!.toJSON())).not.toContain(command);
  act(() => roleGroup().findAllByType('button')[0].props.onClick());
  expect(JSON.stringify(renderer!.toJSON())).not.toContain(command);
  act(() => roleGroup().findByProps({ 'data-command-toggle': true }).props.onClick());
  expect(visibleText(roleGroup())).toContain(command);
});
