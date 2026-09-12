import type { NodeProps } from '@xyflow/react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { MacroTaskNode, type MacroData, type MacroNode } from '../map/MacroTaskNode';
import { MapReaderContent } from '../map/MapReaderContent';
import { layoutSubmap, type SubmapStep } from '../map/submap';
import type { CardCopy } from '../map/presentation';
import { ReaderExplanation } from '../research-brief/ReaderExplanation';
import { MarkdownContent } from '../components/MarkdownContent';

vi.mock('@xyflow/react', async original => ({
  ...await original<typeof import('@xyflow/react')>(), Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const task = { id: 'historical-task', title: 'Recorded task', objective: 'Original task objective', status: 'done' };
const step: SubmapStep = { id: 'earlier-review', kind: 'review', title: 'Earlier review', detail: 'Original result from that attempt.',
  status: 'failed', ts: 100, source: 'event', eventIds: ['old-event'] };
const explanation = (title: string): CardCopy => ({ title, summary: title, detail: `${title} retained details`, generated_at: 120, version: 14,
  event_ids: ['old-event'], reader_brief: { why: `${title} purpose`, scope: `${title} scope`, next: `${title} next`,
    concept: { name: `${title} concept`, explanation: `${title} definition`, example: `${title} example`, connection: `${title} connection` } } });
const oldCopy = explanation('Earlier Chinese explanation');
const props = (patch: Partial<MacroData> = {}): NodeProps<MacroNode> => ({ id: 'historical-part', data: {
  id: 'historical-part', task, ordinal: 1, part: 1, partCount: 2, start: 1, end: 1, totalSteps: 2,
  layout: layoutSubmap(task, [], false, [step]), frame: { width: 900, height: 650, scale: 1 },
  canvasSize: { width: 1440, height: 960 }, zh: false, focused: true, detailed: true, live: true,
  source: 'live:project', readOnly: false, open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn(), readCopy: vi.fn(),
  copy: { version: 15, cards: { [task.id]: explanation('LATEST ROOT CONCLUSION'), [step.id]: oldCopy } },
  readerCopy: { request: { key: step.id, task_id: task.id, kind: step.kind, event_ids: step.eventIds }, pending: true, generating: false,
    evidence: [
      { id: 'old-event', item_id: task.id, type: 'round.review.completed', ts: 100, text: 'Earlier event text' },
      { id: 'new-event', item_id: task.id, type: 'round.review.completed', ts: 200, text: 'Later outcome' },
      { id: 'old-event', item_id: 'another-task', type: 'round.review.completed', ts: 100, text: 'Other task evidence' },
    ] },
  ...patch,
} } as NodeProps<MacroNode>);
let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; });

it('uses the same explanation component for a historical step without borrowing the root conclusion or later evidence', () => {
  const value = props();
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, step.id);
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  expect(reader.findByType(ReaderExplanation).props.brief).toEqual(oldCopy.reader_brief);
  const sources = reader.findAllByType(MarkdownContent).map(node => node.props.children).join('\n');
  expect(sources).toContain(oldCopy.reader_brief!.concept!.example);
  expect(sources).toContain(step.detail);
  expect(sources).toContain(oldCopy.detail);
  expect(sources).not.toContain('LATEST ROOT CONCLUSION');
  expect(sources).not.toContain('Later outcome');
  expect(sources).not.toContain('Other task evidence');
  expect(reader.findAllByProps({ 'data-event-id': 'old-event' })).toHaveLength(1);
  expect(reader.findByProps({ 'data-testid': 'research-brief-status' }).findByType('time').props.dateTime).toBe('1970-01-01T00:02:00.000Z');
  expect(reader.findAllByType('span').some(node => node.children.includes(' · update pending'))).toBe(true);
  expect(renderer!.root.findAll(node => node.type === 'button' && node.props['data-testid'] === 'map-task-read')).toHaveLength(0);
  act(() => reader.findByProps({ 'aria-label': 'Close step details' }).props.onClick());
  expect(value.data.readCopy).toHaveBeenLastCalledWith(value.id, null);
});

it('keeps legacy step details readable and clears the reader selection when leaving a task', () => {
  const value = props({ copy: { version: 15, cards: { [task.id]: explanation('LATEST ROOT CONCLUSION'),
    [step.id]: { ...oldCopy, version: 9, reader_brief: undefined } } } });
  act(() => { renderer = create(<MacroTaskNode {...value} />); });
  act(() => renderer!.root.findByProps({ 'data-step-id': step.id }).props.onClick());
  const reader = renderer!.root.findByProps({ 'data-testid': 'map-reader' });
  expect(reader.findAllByType(ReaderExplanation)).toHaveLength(0);
  expect(reader.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(oldCopy.detail);
  expect(reader.findAllByType('p').some(node => node.children.some(child => typeof child === 'string' && child.includes('explanation is pending')))).toBe(true);
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

it('does not use mismatched reader metadata as a card’s evidence', () => {
  act(() => { renderer = create(<MapReaderContent cardKey={step.id} taskId={task.id} card={oldCopy}
    originalDetail={step.detail} selection={{ ...props().data.readerCopy!, request: { key: 'another-step', task_id: task.id, kind: 'review', event_ids: ['old-event'] } }} />); });
  expect(renderer!.root.findAllByProps({ 'data-event-id': 'old-event' })).toHaveLength(0);
});
