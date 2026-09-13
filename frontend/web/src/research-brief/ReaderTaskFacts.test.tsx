import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it } from 'vitest';
import { MarkdownContent } from '../components/MarkdownContent';
import { MapReaderContent } from '../map/MapReaderContent';
import type { MapEvent, MapTask } from '../map/model';
import type { CardCopy, CardSourceSnapshot } from '../map/presentation';
import { ReaderTaskFacts } from './ReaderEvidence';
import { selectReaderEvidence } from './evidence';

// Generic offline records; these are never sent to a model or collected as training data.
const task: MapTask = { id: 'task-a', title: 'Compare two implementations', status: 'done',
  objective: 'Compare the implementations on the same machine.', attempt: 1, revision: 'task-v1', content_revision: 'goal-v1' };
const main: MapEvent = { id: 'main-1', item_id: task.id, revision: 'main-v1', type: 'round.main.completed',
  role: 'engineer', ts: 100, attempt: 1, round_index: 1, next_action: '',
  text: 'NEXT_OWNER=reviewer\n\nCompleted:\n- Compared the implementations.\n- Set a new bounded follow-up goal.\n\nThe result is limited to this machine.' };
const review: MapEvent = { id: 'review-1', item_id: task.id, revision: 'review-v1', type: 'round.review.completed',
  role: 'reviewer', ts: 110, attempt: 1, round_index: 1, next_action: '', status: 'done', review_skipped: false,
  text: 'The stated comparison was checked. The broader performance question remains open.' };

function saved(patch: Partial<CardSourceSnapshot> = {}): CardCopy {
  return { version: 27, title: task.title, summary: 'A bounded comparison', detail: 'Explanation detail', generated_at: 120,
    task_revision: task.revision, task_content_revision: task.content_revision,
    reader_brief: { why: 'Compare the same work.', concept: null, scope: 'Only this machine was measured.', next: 'No later goal is recorded.' },
    source_snapshot: { version: 2, card_key: task.id, task_id: task.id, captured_at: 115,
      task: { status: 'done', attempt: 1 }, events: [{ ...main }, { ...review }], source_ids: [main.id, review.id], ...patch } };
}
let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; });
const markdown = (root: ReactTestInstance) => root.findAllByType(MarkdownContent).map(node => node.props.children);

it('keeps every selected report and prose-only follow-up readable independently of generated next text', () => {
  const earlier = { ...main, id: 'earlier-main', revision: 'earlier-v1', ts: 90, text: 'An earlier report records another follow-up condition.' };
  const copy = saved({ events: [{ ...earlier }, { ...main }, { ...review }], source_ids: [earlier.id, main.id, review.id] });
  const props = { cardKey: task.id, taskId: task.id, task, originalDetail: 'Original objective', card: copy };
  act(() => { renderer = create(<MapReaderContent {...props} />); });
  const facts = () => renderer!.root.findByProps({ 'data-reader-task-facts': task.id });
  expect(markdown(facts())).toEqual([earlier.text, main.text, review.text]);
  expect(facts().findAllByType('details')).toHaveLength(0);
  expect(facts().findAllByType('pre')).toHaveLength(0);
  expect(facts().findByProps({ 'data-reader-fact-event': main.id }).findByType('time').props.dateTime).toBe('1970-01-01T00:01:40.000Z');
  expect(markdown(facts()).join('\n')).toContain('NEXT_OWNER=reviewer');
  const interpretation = renderer!.root.findByProps({ 'data-reader-next-interpretation': task.id });
  expect(interpretation.findByType('h3').children).toEqual(['How the explanation interprets the follow-up']);
  expect(interpretation.findByType(MarkdownContent).props.children).toBe(copy.reader_brief!.next);
  act(() => renderer!.update(<MapReaderContent {...props} card={{ ...copy,
    reader_brief: { ...copy.reader_brief!, next: 'An unsupported generated arrangement.' } }} />));
  expect(markdown(facts())).toEqual([earlier.text, main.text, review.text]);
});

it('uses a verified same-version full record without treating its longer text as the generation excerpt', () => {
  const full = { ...main, text: `${main.text}\n\n${'Full measurement detail. '.repeat(100)}\nThe final recorded condition stays visible.` };
  const copy = saved({ events: [{ ...main, text: 'A retained excerpt.', text_truncated: true }], source_ids: [main.id] });
  const selection = selectReaderEvidence({ cardKey: task.id, taskId: task.id, task, card: copy, loadedEvents: [full] });
  expect(selection.currentTask).toBeUndefined();
  expect(selection.usedTask?.fullRecord).toBe(task);
  act(() => { renderer = create(<ReaderTaskFacts selection={selection} />); });
  const root = renderer!.root;
  expect(root.findByProps({ 'data-reader-fact-state': 'current' }).findByType('p').children).toContain('Done');
  expect(markdown(root)).toEqual([full.text]);
  expect(root.findAllByType('p').some(node => node.children.includes('This is the verified complete record from the same version; generation used only an excerpt.'))).toBe(true);
  expect(copy.source_snapshot!.events[0].text).toBe('A retained excerpt.');
});

it('keeps an earlier attempt separate from the current task and rejects another owner’s matching event id', () => {
  const currentTask = { ...task, status: 'running', attempt: 2, revision: 'task-v2' };
  const changed = { ...main, revision: 'main-v2', attempt: 2, ts: 200, text: 'A result recorded in attempt two.' };
  const foreign = { ...main, item_id: 'other-task', text: 'Other task material must not appear.' };
  const selection = selectReaderEvidence({ cardKey: task.id, taskId: task.id, card: saved(), task: currentTask,
    loadedEvents: [foreign, changed], currentEvents: [foreign, changed] });
  act(() => { renderer = create(<ReaderTaskFacts selection={selection} />); });
  const root = renderer!.root;
  expect(root.findByProps({ 'data-reader-fact-state': 'retained' }).findByType('p').children).toContain('Done');
  expect(root.findByProps({ 'data-reader-fact-state': 'current' }).findByType('p').children).toContain('In progress');
  expect(root.findByProps({ 'data-reader-fact-state': 'current' }).findByType('span').children).toEqual(['Attempt 2']);
  expect(markdown(root.findByProps({ 'data-reader-fact-group': 'used' }))).toEqual([main.text, review.text]);
  expect(markdown(root.findByProps({ 'data-reader-fact-group': 'current' }))).toEqual([changed.text]);
  expect(markdown(root)).not.toContain(foreign.text);
});

it('does not replace a historical step’s unrecorded task state with the later task success', () => {
  const copy = saved({ card_key: 'old-step', task: { title: task.title },
    events: [{ ...review, status: 'blocked', review_skipped: true, text: 'The old round stopped before review.' }], source_ids: [review.id] });
  const selection = selectReaderEvidence({ cardKey: 'old-step', taskId: task.id, task: { ...task, attempt: 2, revision: 'task-v2' }, card: copy });
  act(() => { renderer = create(<ReaderTaskFacts selection={selection} />); });
  const root = renderer!.root;
  expect(root.findByProps({ 'data-reader-fact-state': 'retained' }).findByType('p').children).toContain('Not recorded');
  expect(root.findByProps({ 'data-reader-fact-state': 'current' }).findByType('p').children).toContain('Done');
  expect(root.findAllByType('p').some(node => node.children.includes('This record marks the round as not reviewed.'))).toBe(true);
  expect(markdown(root)).toEqual(['The old round stopped before review.']);
});

it('keeps a mismatched snapshot owner unavailable while showing only the actual selected task', () => {
  const copy = saved({ events: [{ ...main, item_id: 'other-task' }], source_ids: [main.id] });
  const selection = selectReaderEvidence({ cardKey: task.id, taskId: task.id, card: copy,
    task: { ...task, id: 'other-task', status: 'failed' } });
  act(() => { renderer = create(<ReaderTaskFacts selection={selection} />); });
  const root = renderer!.root;
  expect(root.findAllByProps({ 'data-reader-fact-state': 'current' })).toHaveLength(0);
  expect(markdown(root)).toEqual([]);
  expect(root.findAllByType('p').some(node => node.children.includes('The record belongs to a different task and is not shown as source text.'))).toBe(true);
});

it('shows explicit recorded actions and the full current question without borrowing a neighboring assignment', () => {
  const question = 'Please choose the input to compare. '.repeat(30);
  const copy = saved({ task: { status: 'paused_operator', pending_question: question.slice(0, 500), pending_question_truncated: true },
    events: [{ ...review, review_source: 'engineer_self_review', next_action: 'Repeat the comparison with the selected input.' }], source_ids: [review.id],
    related_tasks: [{ id: 'neighbor', status: 'pending', objective: 'Run a different experiment.' }] });
  const selection = selectReaderEvidence({ cardKey: task.id, taskId: task.id, card: copy,
    task: { ...task, status: 'paused_operator', pending_question: question, revision: 'task-v2' } });
  act(() => { renderer = create(<ReaderTaskFacts selection={selection} />); });
  const root = renderer!.root;
  expect(markdown(root.findByProps({ 'data-reader-fact-state': 'current' }))).toEqual([question]);
  expect(markdown(root)).toContain('Repeat the comparison with the selected input.');
  expect(markdown(root)).not.toContain('Run a different experiment.');
  expect(root.findAllByType('p').some(node => node.children.includes('This record is the executor’s self-check.'))).toBe(true);
  expect(root.findAllByType('p').some(node => node.children.includes('Only an excerpt of the question is retained here.'))).toBe(true);
});

it('shows loaded task records before any generated explanation exists', () => {
  act(() => { renderer = create(<MapReaderContent cardKey={task.id} taskId={task.id} task={task} originalDetail="Original goal"
    selection={{ request: { key: task.id, task_id: task.id, kind: 'task', event_ids: [main.id] },
      evidence: [main], pending: false, generating: false }} />); });
  const facts = renderer!.root.findByProps({ 'data-reader-task-facts': task.id });
  expect(facts.findByProps({ 'data-reader-fact-state': 'current' }).findByType('p').children).toContain('Done');
  expect(markdown(facts)).toEqual([main.text]);
  expect(facts.findAllByType('details')).toHaveLength(0);
  expect(renderer!.root.findAllByProps({ 'data-reader-next-interpretation': task.id })).toHaveLength(0);
});

it('preserves explicit execution-versus-overall completion flags without manufacturing a next assignment', () => {
  const completion = { ...main, type: 'life.mission.completed', overall_complete: false, campaign_continues: true,
    text: 'The bounded comparison has ended.', next_action: '' };
  const selection = selectReaderEvidence({ cardKey: task.id, taskId: task.id, card: saved({
    events: [completion], source_ids: [completion.id] }) });
  act(() => { renderer = create(<ReaderTaskFacts selection={selection} />); });
  const root = renderer!.root;
  expect(root.findAllByType('p').some(node => node.children.includes('This execution ended; the overall goal is not complete and further work remains.'))).toBe(true);
  expect(markdown(root)).toEqual([completion.text]);
});
