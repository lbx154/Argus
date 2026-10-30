import type { NodeProps } from '@xyflow/react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { MacroTaskNode, MapArtifactContext, type MacroData, type MacroNode } from '../map/MacroTaskNode';
import { MapReaderContent, MapTaskReader } from '../map/MapReaderContent';
import { layoutSubmap, type SubmapStep } from '../map/submap';
import { MarkdownContent } from '../components/MarkdownContent';
import { api } from '../api';
import recorded from './fixtures/home/projects/p-c855623c/project.json';
import { Spinner } from '../components/primitives';
import * as i18n from '../i18n';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@xyflow/react', async original => ({
  ...await original<typeof import('@xyflow/react')>(), Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const task = { id: 'historical-task', title: 'Recorded task', objective: 'Original task objective', status: 'done', revision: 'task-v1' };
const step: SubmapStep = { id: 'earlier-review', kind: 'review', title: 'Earlier review', detail: 'Original result from that attempt.',
  status: 'failed', ts: 100, source: 'event', eventIds: ['old-event'] };
const props = (patch: Partial<MacroData> = {}): NodeProps<MacroNode> => ({ id: 'historical-part', type: "task", zIndex: 0, draggable: false, selected: false, dragging: false, selectable: true, deletable: false, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0, data: {
  id: 'historical-part', task, ordinal: 1, part: 1, partCount: 2, start: 1, end: 1, totalSteps: 2,
  layout: layoutSubmap(task, [], false, [step]), frame: { width: 900, height: 650, scale: 1 },
  canvasSize: { width: 1440, height: 960 }, zh: false, focused: true, detailed: true, open: vi.fn(), readStep: vi.fn(), readCopy: vi.fn(),
  ...patch,
} } as NodeProps<MacroNode>);
let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; vi.restoreAllMocks(); });

it('identifies cards by task name while retaining ordinal for the arrival animation', () => {
  const value = props({ ordinal: 27, arriving: true });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  expect(renderer!.root.findByProps({ 'data-testid': 'map-macro' }).props.style.animationDelay).toMatch(/\d+ms/);
  const text = JSON.stringify(renderer!.toJSON());
  expect(text).toContain('Recorded task');
  expect(text).not.toContain('macro-index');
  expect(text).not.toContain('map-card-number');
  expect(renderer!.root.findByProps({ className: 'map-card-kind' }).children.join('')).not.toContain('27');
});

it('formats workspace paths at the opened step reader', () => {
  const workspace = '/tenant/workspace with spaces';
  const path = 'reports/中文资料/very-long-folder-name-for-mobile-layout/verification.md';
  const record = `已核对 \`${workspace}/${path}\`，外部资料在 /other/reference.md。`;
  const value = props({ layout: layoutSubmap(task, [], false, [{ ...step, detail: record }]) });
  act(() => { renderer = create(<MapArtifactContext.Provider value={{ workspace }}><MacroTaskNode {...value} /></MapArtifactContext.Provider>); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  const sources = renderer!.root.findByProps({ 'data-testid': 'map-reader' }).findAllByType(MarkdownContent).map(node => node.props.children).join('\n');
  expect(sources).toContain(`\`${path}\``);
  expect(sources).toContain('/other/reference.md');
  expect(sources).not.toContain(workspace);
});

it('formats the live task trace at the task reader’s actual display entry', () => {
  const workspace = '/tenant/workspace with spaces';
  const path = 'reports/中文资料/very-long-folder-name-for-mobile-layout/verification.md';
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  // The TS map-trace endpoint returns each call's closing letter as a message entry.
  client.setQueryData(['task-trace', 'reader-project', task.id], { task_id: task.id, groups: [
    { call_id: 'engineer-1', role: 'engineer', entries: [{ kind: 'message', text: `已检查 ${workspace}/${path}，外部资料在 /other/reference.md。` }] },
  ] });
  act(() => { renderer = create(<QueryClientProvider client={client}>
    <MapReaderContent cardKey={task.id} taskId={task.id} task={task} sessionId="reader-project" workspace={workspace} originalDetail="Task objective" />
  </QueryClientProvider>); });
  const text = JSON.stringify(renderer!.toJSON());
  expect(text).toContain(path);
  expect(text).toContain('/other/reference.md');
  expect(text).not.toContain(workspace);
  act(() => renderer!.unmount()); renderer = undefined;
  client.clear();
});

it.each([[true, false], [false, false], [true, true]])('shows full latest words once with a recorded letter (%s) and trace error (%s)', (recordedInTrace, traceError) => {
  const source = recorded.tasks[2];
  const progress = source.note.text;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false, gcTime: Infinity, staleTime: Infinity } } });
  client.setQueryData(['task-trace', recorded.id, source.id], { task_id: source.id, groups: recordedInTrace ? [
    { call_id: 'reviewer-1', role: 'reviewer', entries: [{ kind: 'message', text: progress }] },
  ] : [] });
  if (traceError) client.getQueryCache().find({ queryKey: ['task-trace', recorded.id, source.id] })!.setState({ status: 'error', error: new Error('Trace read failed') });
  act(() => { renderer = create(<QueryClientProvider client={client}>
    <MapReaderContent cardKey={source.id} taskId={source.id} sessionId={recorded.id} originalDetail={source.objective}
      task={{ id: source.id, title: source.title, status: 'done', latest_action: { role: 'reviewer', text: progress, ts: 1 } }} />
  </QueryClientProvider>); });
  const copies = renderer!.root.findAll(node => node.type === 'p' && node.children.join('') === progress);
  expect(copies).toHaveLength(1);
  act(() => renderer!.unmount()); renderer = undefined;
  client.clear();
});

it('reads the selected historical step from its own record', () => {
  const value = props();
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, step.id);
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  const sources = reader.findAllByType(MarkdownContent).map(node => node.props.children).join('\n');
  expect(sources).toContain(step.detail);
  expect(sources).not.toContain('LATEST ROOT CONCLUSION');
  expect(sources).not.toContain('Later outcome');
  expect(sources).not.toContain('Other task evidence');
  expect(reader.findAllByProps({ 'data-event-id': 'old-event' })).toHaveLength(0);
  expect(reader.findAllByProps({ 'data-testid': 'research-brief-status' })).toHaveLength(0);
  expect(renderer!.root.findAll(node => node.type === 'button' && node.props['data-testid'] === 'map-task-read')).toHaveLength(0);
  act(() => reader.findByProps({ 'aria-label': 'Close step details' }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, null);
});

it('opens a task explanation with only its own root key, including read-only viewing', () => {
  const value = props({ part: 2, partCount: 2, readOnly: true });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.find(node => node.type === 'button' && node.props['data-testid'] === 'map-task-read').props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, task.id);
});

it.each([true, false])('shows the card’s own record without writing, failure or retry status when it has no explanation (zh=%s)', zh => {
  vi.spyOn(i18n, 'useI18n').mockReturnValue({ locale: zh ? 'zh-CN' : 'en', setLocale: vi.fn(), t: key => key });
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} originalDetail={step.detail} zh={zh} />); });
  expect(renderer!.root.findByType(MarkdownContent).props.children).toBe(step.detail);
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-status' })).toHaveLength(0);
  expect(renderer!.root.findAllByType(Spinner)).toHaveLength(0);
  expect(renderer!.root.findAllByType('button')).toHaveLength(0);
  expect(JSON.stringify(renderer!.toJSON())).not.toMatch(/正在撰写说明|Writing the explanation|重试|Retry/);
});

it('leaves a pending question in conversation instead of copying it into the task reader', () => {
  const question = 'Please choose the input to compare.';
  act(() => { renderer = create(<MapReaderContent cardKey={task.id} taskId={task.id}
    task={{ ...task, status: "blocked" }} originalDetail="Useful task objective" />); });
  expect(JSON.stringify(renderer!.toJSON())).not.toContain(question);
});

it('loads the full recorded brief only when its task reader opens', async () => {
  const source = recorded.tasks[2];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
  let resolve!: (value: Awaited<ReturnType<typeof api.backlogItem>>) => void;
  const request = vi.spyOn(api, 'backlogItem').mockReturnValue(new Promise(done => { resolve = done; }));
  client.setQueryData(['task-trace', recorded.id, source.id], { task_id: source.id, groups: [] });
  const summary = { id: source.id, title: source.title, status: 'done' };
  const view = (open: boolean) => <QueryClientProvider client={client}>
    {open && <MapTaskReader task={summary} sessionId={recorded.id} />}
  </QueryClientProvider>;
  act(() => { renderer = create(view(false)); });
  expect(request).not.toHaveBeenCalled();
  act(() => renderer!.update(view(true)));
  expect(request).toHaveBeenCalledWith(recorded.id, source.id, expect.any(AbortSignal));
  expect(renderer!.root.findByProps({ role: 'status' }).children.join('')).toContain('正在读取');
  await act(async () => {
    resolve({ ...summary, objective: source.objective, latest_action: { role: 'reviewer', text: source.note.text, ts: 1 }, priority: 1 });
    await new Promise(done => setTimeout(done, 10));
  });
  expect(renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(source.objective);
  expect(renderer!.root.findByType(MapReaderContent).props.task).not.toHaveProperty('objective');
  expect(renderer!.root.findByProps({ className: 'task-trace-conclusion mb-3 font-medium' }).children.join('')).toBe(source.note.text);
  expect(renderer!.root.findByType(MapReaderContent).props.task.latest_action.text).toBe(source.note.text);
  act(() => renderer!.unmount()); renderer = undefined;
  client.clear();
});

it('opens the root task reader when the goal step is selected', () => {
  const value = props({ layout: layoutSubmap(task, [], false) });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': `${task.id}:brief` }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, task.id);
});

it.each(['in_progress', 'done', 'failed'])('reads the selected task %s letter in full', status => {
  const progress = recorded.tasks[2].note.text;
  const current = { ...task, status, latest_action: { role: 'reviewer', text: progress, ts: 1 } };
  const value = props({ task: current, layout: layoutSubmap(current, [{ id: 'full-letter', item_id: current.id, type: 'handoff', role: 'reviewer', to: 'manager', text: progress, ts: 1 }], false) });
  const stepId = 'full-letter';
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': stepId }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, stepId);
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  expect(reader.findByType(MapReaderContent).props.originalDetail).toBe(progress);
  expect(reader.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(progress);
});

it('reads a request answer step from its own answer', () => {
  const answer = 'The recorded answer belongs to this step, separate from the original question.';
  const current = { ...task, turn_kind: 'qa' as const, latest_action: { role: 'manager', text: answer, ts: 1 } };
  const value = props({ task: current, layout: layoutSubmap(current, [{ id: 'answer-letter', item_id: current.id, type: 'turn.replied', text: answer, ts: 1 }], false) });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': 'answer-letter' }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, 'answer-letter');
  expect(renderer!.root.findByProps({ 'data-testid': 'map-reader' }).findByType(MarkdownContent).props.children).toBe(answer);
});

it.each(['in_progress', 'done', 'failed'])('opens on-demand full task progress from a synthetic %s step', status => {
  const current = { ...task, status, latest_action: { role: 'engineer', text: 'Short map excerpt…', ts: 1 } };
  const value = props({ task: current, layout: layoutSubmap(current, [], false) });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': `${task.id}:${status === 'in_progress' ? 'active' : 'outcome'}` }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, task.id);
  expect(renderer!.root.findAllByProps({ 'data-testid': 'map-reader' })).toHaveLength(0);
});


it('requests the selected historical letter only when its reader is opened, keeping the complete body', async () => {
  const source = recorded.tasks[2];
  const letter = source.note.text;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
  const request = vi.spyOn(api, 'backlogItem').mockResolvedValue({ id: source.id, title: source.title, status: 'done', priority: 1, objective: source.objective, notes: letter });
  client.setQueryData(['task-trace', recorded.id, source.id], { task_id: source.id, groups: [] });
  const current = { ...task, id: source.id };
  const value = props({ task: current, layout: layoutSubmap(current, [{ id: 'e42', item_id: current.id, type: 'handoff', text: 'Short heading…', ts: 1 }], false) });
  act(() => { renderer = create(<QueryClientProvider client={client}><MapArtifactContext.Provider value={{ sessionId: recorded.id }}><MacroTaskNode {...value} /></MapArtifactContext.Provider></QueryClientProvider>); });
  expect(request).not.toHaveBeenCalled();
  await act(async () => {
    renderer!.root.findByProps({ 'data-step-id': 'e42' }).props.onClick();
    await new Promise(done => setTimeout(done, 10));
  });
  expect(request).toHaveBeenCalledWith(recorded.id, source.id, expect.any(AbortSignal), 'e42');
  expect(renderer!.root.findByProps({ 'data-testid': 'map-reader' }).findAllByType(MarkdownContent).some(node => node.props.children === letter)).toBe(true);
  client.clear();
});

it('reads the complete request answer from the detail entry instead of the map summary', async () => {
  const answer = 'Answer heading.\n\n' + 'Full answer body. '.repeat(60);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
  const summary = { id: 'q0', turn_kind: 'qa' as const, title: 'The request', status: 'done', latest_action: { role: 'manager', text: 'Answer heading.', ts: 1 } };
  vi.spyOn(api, 'backlogItem').mockResolvedValue({ ...summary, priority: 1, objective: 'The full request.', latest_action: { ...summary.latest_action, text: answer } });
  await act(async () => { renderer = create(<QueryClientProvider client={client}><MapTaskReader task={summary} sessionId="request-reading" /></QueryClientProvider>); await new Promise(done => setTimeout(done, 10)); });
  expect(renderer!.root.findAllByType(MarkdownContent).some(node => node.props.children === answer)).toBe(true);
  client.clear();
});
