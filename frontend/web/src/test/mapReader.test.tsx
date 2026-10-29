import type { NodeProps } from '@xyflow/react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { MacroTaskNode, MapArtifactContext, type MacroData, type MacroNode } from '../map/MacroTaskNode';
import { MapReaderContent } from '../map/MapReaderContent';
import { layoutSubmap, type SubmapStep } from '../map/submap';
import type { CardCopy } from '../map/presentation';
import { MarkdownContent } from '../components/MarkdownContent';
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
const explanation = (title: string): CardCopy => ({ title, summary: title, detail: `${title} retained details`, generated_at: 120, version: 14,
  task_revision: 'task-v1', event_ids: ['old-event'], event_revisions: ['event-v1'] });
const oldCopy = explanation('Earlier Chinese explanation');
const props = (patch: Partial<MacroData> = {}): NodeProps<MacroNode> => ({ id: 'historical-part', data: {
  id: 'historical-part', task, ordinal: 1, part: 1, partCount: 2, start: 1, end: 1, totalSteps: 2,
  layout: layoutSubmap(task, [], false, [step]), frame: { width: 900, height: 650, scale: 1 },
  canvasSize: { width: 1440, height: 960 }, zh: false, focused: true, detailed: true, live: true,
  source: 'live:project', readOnly: false, open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn(), readCopy: vi.fn(),
  copy: { version: 15, cards: { [task.id]: explanation('LATEST ROOT CONCLUSION'), [step.id]: oldCopy } },
  ...patch,
} } as NodeProps<MacroNode>);
let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; vi.restoreAllMocks(); });

it('identifies cards by task name while retaining ordinal for the arrival animation', () => {
  const value = props({ ordinal: 27, arriving: true, copy: undefined });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  expect(renderer!.root.findByProps({ 'data-testid': 'map-macro' }).props.style.animationDelay).toMatch(/\d+ms/);
  const text = JSON.stringify(renderer!.toJSON());
  expect(text).toContain('Recorded task');
  expect(text).not.toContain('macro-index');
  expect(text).not.toContain('map-card-number');
  expect(renderer!.root.findByProps({ className: 'map-card-kind' }).children.join('')).not.toContain('27');
});

it.each([false, true])('formats workspace paths at the opened step reader, including saved prose (%s)', saved => {
  const workspace = '/tenant/workspace with spaces';
  const path = 'reports/中文资料/very-long-folder-name-for-mobile-layout/verification.md';
  const record = `已核对 \`${workspace}/${path}\`，外部资料在 /other/reference.md。`;
  const value = props({ layout: layoutSubmap(task, [], false, [{ ...step, detail: record }]),
    copy: saved ? { version: 15, cards: { [step.id]: { ...oldCopy, detail: record } } } : undefined });
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

it('uses the same explanation component for a historical step without borrowing the root conclusion or later evidence', () => {
  const value = props();
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, step.id);
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  const sources = reader.findAllByType(MarkdownContent).map(node => node.props.children).join('\n');
  expect(reader.findAllByType(MarkdownContent).filter(node => node.props.children === oldCopy.detail)).toHaveLength(1);
  expect(sources).not.toContain(step.detail);
  expect(sources).toContain(oldCopy.detail);
  expect(sources).not.toContain('LATEST ROOT CONCLUSION');
  expect(sources).not.toContain('Later outcome');
  expect(sources).not.toContain('Other task evidence');
  expect(reader.findAllByProps({ 'data-event-id': 'old-event' })).toHaveLength(0);
  expect(reader.findAllByProps({ 'data-testid': 'research-brief-status' })).toHaveLength(0);
  expect(renderer!.root.findAll(node => node.type === 'button' && node.props['data-testid'] === 'map-task-read')).toHaveLength(0);
  act(() => reader.findByProps({ 'aria-label': 'Close step details' }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, null);
});

it('keeps legacy step details readable and clears the reader selection when leaving a task', () => {
  const value = props({ copy: { version: 15, cards: { [task.id]: explanation('LATEST ROOT CONCLUSION'),
    [step.id]: { ...oldCopy, version: 9 } } } });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  expect(reader.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(oldCopy.detail);
  expect(reader.findAllByType('p').some(node => node.children.some(child => typeof child === 'string' && child.includes('explanation is pending')))).toBe(false);
  act(() => renderer!.update(<MacroTaskNode {...value} data={{ ...value.data, detailed: false, focused: false }} />));
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, null);
  expect(renderer!.root.findAllByProps({ 'data-testid': 'map-reader' })).toHaveLength(0);
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

it('keeps useful explanation links without repeating source records or current task detail', () => {
  const copy: CardCopy = { ...oldCopy, detail: 'Retained condition; see the [recorded note](research/note.md).' };
  const currentTask = { ...task, objective: 'Changed task goal', revision: 'task-v2', attempt: 2 };
  const onOpenArtifact = vi.fn();
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} card={copy} task={currentTask}
    artifacts={[{ path: 'research/note.md', name: 'note.md', why: 'Recorded note', exists: true, kind: 'markdown', mime: 'text/markdown', size: 42, mtime: null }]} onOpenArtifact={onOpenArtifact}
    originalDetail="Current loaded detail only" />); });
  const text = JSON.stringify(renderer!.toJSON());
  for (const shell of ['View evidence', 'Source record', 'Original record', 'Currently loaded task and step record', 'data-evidence', 'Current loaded detail only']) {
    expect(text).not.toContain(shell);
  }
  expect(renderer!.root.findByType(MarkdownContent).props.children).toBe(copy.detail);
  const preventDefault = vi.fn();
  act(() => renderer!.root.findByProps({ 'data-artifact-path': 'research/note.md' }).props.onClick({ preventDefault }));
  expect(preventDefault).toHaveBeenCalledOnce();
  expect(onOpenArtifact).toHaveBeenCalledWith('research/note.md');
});

it.each([false, true])('preserves the full blocking question with saved explanation=%s and no evidence shells', saved => {
  const question = 'Please choose the input to compare. '.repeat(40);
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id}
    task={{ ...task, pending_question: question }} card={saved ? oldCopy : undefined} originalDetail="Useful task objective" />); });
  expect(renderer!.root.findByProps({ 'data-reader-pending-question': task.id }).findByType(MarkdownContent).props.children).toBe(question.trim());
  const text = JSON.stringify(renderer!.toJSON());
  for (const shell of ['View evidence', 'Source record', 'Original record', 'data-evidence']) expect(text).not.toContain(shell);
  expect(text.includes('Useful task objective')).toBe(!saved);
  act(() => renderer!.update(<MapReaderContent cardKey={step.id} taskId={task.id}
    task={{ ...task, id: 'another-task', pending_question: question }} originalDetail="Useful task objective" />));
  expect(renderer!.root.findAllByProps({ 'data-reader-pending-question': 'another-task' })).toHaveLength(0);
});
