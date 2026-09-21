import type { NodeProps } from '@xyflow/react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { MacroTaskNode, type MacroData, type MacroNode } from '../map/MacroTaskNode';
import { MapReaderContent } from '../map/MapReaderContent';
import { layoutSubmap, type SubmapStep } from '../map/submap';
import type { CardCopy } from '../map/presentation';
import { ReaderExplanation } from '../research-brief/ReaderExplanation';
import { MarkdownContent } from '../components/MarkdownContent';
import { Spinner } from '../components/primitives';
import * as i18n from '../i18n';

vi.mock('@xyflow/react', async original => ({
  ...await original<typeof import('@xyflow/react')>(), Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const task = { id: 'historical-task', title: 'Recorded task', objective: 'Original task objective', status: 'done', revision: 'task-v1' };
const step: SubmapStep = { id: 'earlier-review', kind: 'review', title: 'Earlier review', detail: 'Original result from that attempt.',
  status: 'failed', ts: 100, source: 'event', eventIds: ['old-event'] };
const explanation = (title: string): CardCopy => ({ title, summary: title, detail: `${title} retained details`, generated_at: 120, version: 14,
  task_revision: 'task-v1', event_ids: ['old-event'], event_revisions: ['event-v1'], reader_brief: { why: `${title} purpose`, scope: `${title} scope`, next: `${title} next`,
    concept: { name: `${title} concept`, explanation: `${title} definition`, example: `${title} example`, connection: `${title} connection` } } });
const oldCopy = explanation('Earlier Chinese explanation');
const props = (patch: Partial<MacroData> = {}): NodeProps<MacroNode> => ({ id: 'historical-part', data: {
  id: 'historical-part', task, ordinal: 1, part: 1, partCount: 2, start: 1, end: 1, totalSteps: 2,
  layout: layoutSubmap(task, [], false, [step]), frame: { width: 900, height: 650, scale: 1 },
  canvasSize: { width: 1440, height: 960 }, zh: false, focused: true, detailed: true, live: true,
  source: 'live:project', readOnly: false, open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn(), readCopy: vi.fn(),
  copy: { version: 15, cards: { [task.id]: explanation('LATEST ROOT CONCLUSION'), [step.id]: oldCopy } },
  readerCopy: { request: { key: step.id, task_id: task.id, kind: step.kind, event_ids: step.eventIds }, pending: true, generating: false },
  ...patch,
} } as NodeProps<MacroNode>);
let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; vi.restoreAllMocks(); });

it('uses the same explanation component for a historical step without borrowing the root conclusion or later evidence', () => {
  const value = props();
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, step.id);
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  const explanation = reader.findByType(ReaderExplanation);
  expect(explanation.props.brief).toEqual(oldCopy.reader_brief);
  expect(explanation.props.detail).toBe(oldCopy.detail);
  const teaching = oldCopy.reader_brief!;
  expect(explanation.findAllByType(MarkdownContent).map(node => node.props.children)).toEqual([
    teaching.why, teaching.concept!.explanation, teaching.concept!.example, teaching.concept!.connection,
    teaching.scope, teaching.next, oldCopy.detail,
  ]);
  const details = explanation.findAllByType('details');
  expect(details).toHaveLength(1);
  expect(details[0].findByType('summary').children).toContain('Detailed explanation and conditions');
  expect(details[0].props.open).toBeUndefined();
  expect(details[0].findByType(MarkdownContent).props.children).toBe(oldCopy.detail);
  expect(reader.findAllByType(MarkdownContent).filter(node => node.props.children === oldCopy.detail)).toHaveLength(1);
  const sources = reader.findAllByType(MarkdownContent).map(node => node.props.children).join('\n');
  expect(sources).toContain(oldCopy.reader_brief!.concept!.example);
  expect(sources).not.toContain(step.detail);
  expect(sources).toContain(oldCopy.detail);
  expect(sources).not.toContain('LATEST ROOT CONCLUSION');
  expect(sources).not.toContain('Later outcome');
  expect(sources).not.toContain('Other task evidence');
  expect(reader.findAllByProps({ 'data-event-id': 'old-event' })).toHaveLength(0);
  expect(reader.findByProps({ 'data-testid': 'research-brief-status' }).findByType('time').props.dateTime).toBe('1970-01-01T00:02:00.000Z');
  expect(reader.findAllByType('span').some(node => node.children.includes(' · update pending'))).toBe(true);
  expect(renderer!.root.findAll(node => node.type === 'button' && node.props['data-testid'] === 'map-task-read')).toHaveLength(0);
  act(() => reader.findByProps({ 'aria-label': 'Close step details' }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, null);
});

it('passes only the selected historical card’s learning path to the shared reader', () => {
  const path = (prefix: string) => ({ question: `${prefix} question`, steps: [{ title: `${prefix} meaning`,
    explanation: `${prefix} explanation`, example: `${prefix} example`,
    check: { question: `${prefix} check`, answer: `${prefix} answer` } }] });
  const retained = { ...oldCopy, learning_path: path('Earlier step') };
  const value = props({ copy: { version: 25, cards: {
    [task.id]: { ...explanation('LATEST ROOT CONCLUSION'), learning_path: path('LATEST ROOT') }, [step.id]: retained,
  } } });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  const shared = renderer!.root.findByType(ReaderExplanation);
  expect(shared.props.learningPath).toEqual(retained.learning_path);
  expect(shared.findByProps({ 'data-reader-learning-path': step.id })).toBeDefined();
  const text = shared.findAllByType(MarkdownContent).map(node => node.props.children).join('\n');
  expect(text).toContain('Earlier step question');
  expect(text).toContain(oldCopy.reader_brief!.scope);
  expect(text).not.toContain('LATEST ROOT');
  expect(renderer!.root.findByProps({ 'data-reader-card': step.id }).props['data-reader-task-id']).toBe(task.id);
});

it('keeps legacy step details readable and clears the reader selection when leaving a task', () => {
  const value = props({ copy: { version: 15, cards: { [task.id]: explanation('LATEST ROOT CONCLUSION'),
    [step.id]: { ...oldCopy, version: 9, reader_brief: undefined } } } });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  expect(reader.findAllByType(ReaderExplanation)).toHaveLength(0);
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

it('does not use mismatched reader metadata as a card’s pending generation', () => {
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} card={oldCopy}
    originalDetail={step.detail} selection={{ ...props().data.readerCopy!, request: { key: 'another-step', task_id: task.id, kind: 'review', event_ids: ['old-event'] } }} />); });
  expect(renderer!.root.findAllByType('pre')).toHaveLength(0);
  expect(JSON.stringify(renderer!.toJSON())).not.toContain('update pending');
});

it.each([true, false])('shows a stopped failure and manual retry with retained explanation=%s', async retained => {
  vi.spyOn(i18n, 'useI18n').mockReturnValue({ locale: 'zh-CN', setLocale: vi.fn(), t: key => key });
  const retry = vi.fn().mockResolvedValue(undefined);
  const selection = { ...props().data.readerCopy!, error: new Error('Internal provider detail'), retry };
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} card={retained ? oldCopy : undefined}
    originalDetail={step.detail} selection={selection} />); });
  const status = renderer!.root.findByProps({ 'data-testid': 'research-brief-status' });
  expect(status.findByProps({ role: 'status' }).children).toEqual(['说明生成未完成；不会自动重复请求。']);
  expect(status.findAllByType(Spinner)).toHaveLength(0);
  const button = status.findByType('button');
  expect(button.children).toContain('重试');
  expect(button.props.disabled).toBe(false);
  await act(async () => { button.props.onClick(); });
  expect(retry).toHaveBeenCalledOnce();
  const text = JSON.stringify(renderer!.toJSON());
  expect(text).not.toContain('Internal provider detail');
  expect(text).not.toContain('阅读说明待整理');
  expect(text).not.toContain('待更新');
  expect(text.includes(step.detail)).toBe(!retained);
  expect(renderer!.root.findAllByProps({ 'data-event-id': 'old-event' })).toHaveLength(0);
  if (retained) {
    expect(renderer!.root.findByType(ReaderExplanation).props.brief).toEqual(oldCopy.reader_brief);
    expect(status.findByType('time').props.dateTime).toBe('1970-01-01T00:02:00.000Z');
    expect(text).toContain(oldCopy.detail);
  } else {
    expect(renderer!.root.findAllByType(ReaderExplanation)).toHaveLength(0);
    expect(status.findAllByType('time')).toHaveLength(0);
  }
  // A stale generating flag must not claim automatic work after a failed attempt.
  act(() => renderer!.update(<MapReaderContent cardKey={step.id} taskId={task.id} card={retained ? oldCopy : undefined}
    originalDetail={step.detail} selection={{ ...selection, generating: true }} />));
  expect(renderer!.root.findAllByType(Spinner)).toHaveLength(0);
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-status' }).findByType('button').props.disabled).toBe(true);
});

it('offers manual retry for a coalesced older explanation without reporting a failed provider', () => {
  const retry = vi.fn().mockResolvedValue(undefined);
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} card={oldCopy}
    originalDetail={step.detail} selection={{ ...props().data.readerCopy!, unavailable: true, retry }} />); });
  const status = renderer!.root.findByProps({ 'data-testid': 'research-brief-status' });
  expect(status.findByProps({ role: 'status' }).children).toEqual(['The explanation has not been updated yet. You can retry manually.']);
  expect(status.findByType('button').children).toContain('Retry');
  expect(status.findByType('time').props.dateTime).toBe('1970-01-01T00:02:00.000Z');
  expect(renderer!.root.findByType(ReaderExplanation).props.brief).toEqual(oldCopy.reader_brief);
  expect(JSON.stringify(renderer!.toJSON())).not.toContain('could not be prepared');
  expect(renderer!.root.findAllByType(Spinner)).toHaveLength(0);
});

it.each([
  { key: 'another-step', task_id: task.id },
  { key: step.id, task_id: 'another-task' },
])('keeps another card or task’s retry status out of the reader: %j', identity => {
  const retry = vi.fn().mockResolvedValue(undefined);
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} card={oldCopy}
    originalDetail={step.detail} selection={{ ...props().data.readerCopy!,
      request: { ...props().data.readerCopy!.request, ...identity },
      error: new Error('Another card failed'), unavailable: true, generating: true, retry }} />); });
  const status = renderer!.root.findByProps({ 'data-testid': 'research-brief-status' });
  expect(status.findAllByProps({ role: 'status' })).toHaveLength(0);
  expect(status.findAllByType('button')).toHaveLength(0);
  expect(status.findAllByType(Spinner)).toHaveLength(0);
  expect(status.findByType('time').props.dateTime).toBe('1970-01-01T00:02:00.000Z');
  expect(retry).not.toHaveBeenCalled();
});

it('keeps useful explanation links without repeating source records or current task detail', () => {
  const copy: CardCopy = { ...oldCopy, detail: 'Retained condition; see the [recorded note](research/note.md).' };
  const currentTask = { ...task, objective: 'Changed task goal', revision: 'task-v2', attempt: 2 };
  const onOpenArtifact = vi.fn();
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} card={copy} task={currentTask}
    artifacts={[{ path: 'research/note.md', name: 'note.md', why: 'Recorded note', exists: true, kind: 'markdown', mime: 'text/markdown', size: 42, mtime: null }]} onOpenArtifact={onOpenArtifact}
    originalDetail="Current loaded detail only" selection={{ request: { key: step.id, task_id: task.id, kind: 'review', event_ids: [] },
      pending: true, generating: false }} />); });
  const text = JSON.stringify(renderer!.toJSON());
  for (const shell of ['View evidence', 'Source record', 'Original record', 'Currently loaded task and step record', 'data-evidence', 'Current loaded detail only']) {
    expect(text).not.toContain(shell);
  }
  const retainedDetails = renderer!.root.findByType(ReaderExplanation).findAllByType('details').find(node => node.findByType('summary').children.includes('Detailed explanation and conditions'))!;
  expect(retainedDetails.findByType(MarkdownContent).props.children).toBe(copy.detail);
  const preventDefault = vi.fn();
  act(() => retainedDetails.findByProps({ 'data-artifact-path': 'research/note.md' }).props.onClick({ preventDefault }));
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
