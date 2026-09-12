import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { MarkdownContent } from '../components/MarkdownContent';
import type { CardCopy, CardSourceSnapshot } from '../map/presentation';
import { ReaderEvidence } from './ReaderEvidence';
import { selectReaderEvidence } from './evidence';

const language = vi.hoisted(() => ({ locale: 'en' as 'en' | 'zh-CN' }));
vi.mock('../i18n', async original => {
  const module = await original<typeof import('../i18n')>();
  return { ...module, useI18n: () => ({ locale: language.locale,
    t: (key: string, variables?: Record<string, string | number>) => module.translate(key, variables, language.locale) }) };
});

// Offline fixtures, never submitted as research or training data.
const taskId = 'bsd-finite-case';
const related = [
  { id: 'bsd-p-part', title: 'Compare the normalized p-part', objective: 'State the normalization before comparing p-valuations.', status: 'pending', deps: [taskId] },
  { id: 'bsd-leading-term', title: 'Explain the leading term', objective: 'Separate algebraic rank, zero order, and leading coefficient.', status: 'blocked', deps: ['bsd-p-part'] },
];
const snapshot = (patch: Partial<CardSourceSnapshot> = {}): CardSourceSnapshot => ({
  version: 1, card_key: taskId, task_id: taskId, captured_at: 110,
  task: { title: 'Finite BSD condition', objective: 'Check the stated finite case.' },
  events: [{ id: 'finite-review', item_id: taskId, type: 'round.review.completed', text: 'The finite condition is checked; the general statement remains open.' }],
  source_ids: ['finite-review'], ...patch,
});
const card = (source_snapshot: CardSourceSnapshot): CardCopy => ({
  title: 'Finite BSD explanation', summary: 'Retained summary', detail: 'Retained detail', generated_at: 120, source_snapshot,
});
let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; language.locale = 'en'; });

function render(source: CardSourceSnapshot) {
  const selection = selectReaderEvidence({ cardKey: taskId, taskId, card: card(source),
    task: { id: taskId, title: 'Current task title', objective: 'A later task objective.', status: 'running' } });
  act(() => { renderer = create(<ReaderEvidence selection={selection} />); });
  return renderer!.root;
}

it('still reads a version 1 snapshot without inventing neighboring task material', () => {
  const source = snapshot(), root = render(source);
  expect(root.findByProps({ 'data-testid': 'reader-evidence' }).props['data-evidence-mode']).toBe('snapshot');
  const used = root.findByProps({ 'data-evidence-group': 'used' });
  expect(used.findByProps({ 'data-evidence-task': 'used' }).findByType(MarkdownContent).props.children).toBe(source.task.objective);
  expect(used.findByProps({ 'data-event-id': 'finite-review' }).findByType(MarkdownContent).props.children).toBe(source.events[0].text);
  expect(used.findAllByProps({ 'data-evidence-related-tasks': true })).toHaveLength(0);
});

it.each(['en', 'zh-CN'] as const)('shows the two retained BSD task IDs and their context boundary in %s', locale => {
  language.locale = locale;
  const source = snapshot({ version: 2, related_tasks: related }), root = render(source);
  const used = root.findByProps({ 'data-evidence-group': 'used' });
  const context = used.findByProps({ 'data-evidence-related-tasks': true });
  expect(context.findAllByType('p').some(node => node.children.includes(locale === 'zh-CN'
    ? '相邻任务不代表本项交接。以下仅展示生成时保存的相邻任务材料。'
    : 'Related tasks do not establish a handoff from this task. Only related task material retained at generation time is shown below.'))).toBe(true);
  const disclosure = context.findAllByType('details')[0];
  expect(disclosure.props.open).toBeUndefined();
  expect(disclosure.findAllByType('summary')[0].children).toEqual([locale === 'zh-CN'
    ? '生成时保存的相邻任务（2）' : 'Related tasks retained at generation time (2)']);
  for (const record of related) {
    const row = context.findByProps({ 'data-evidence-related-task': record.id });
    expect(row.findByType('h3').children).toEqual([record.title]);
    expect(row.findAllByType('code').map(node => node.children.join(''))).toEqual([record.id, ...record.deps]);
    expect(row.findByType(MarkdownContent).props.children).toBe(record.objective);
    expect(row.findAllByType('p').some(node => node.children.includes(locale === 'zh-CN' ? '保存时状态：' : 'Retained status: '))).toBe(true);
    expect(JSON.parse(row.findByProps({ 'data-evidence-json': 'related-task' }).children.join(''))).toEqual(record);
    expect(row.findAllByProps({ 'data-evidence-full-record': true })).toHaveLength(0);
    expect(row.findAll(node => node.props['data-event-id'] !== undefined || node.props['data-evidence-task'] !== undefined)).toHaveLength(0);
  }
  expect(used.findByProps({ 'data-evidence-task': 'used' }).findByType(MarkdownContent).props.children).toBe(source.task.objective);
  expect(root.findByProps({ 'data-evidence-group': 'current' }).findByType(MarkdownContent).props.children).toBe('A later task objective.');
  expect(root.findByProps({ 'data-evidence-summary': true }).findAllByType('p')[0].children).toEqual([locale === 'zh-CN'
    ? '这份说明保留了 1 条来源材料节选。' : 'This explanation retains 1 source excerpts.']);
});

it('shows only supplied neighboring task fields and records truncation without filling missing facts', () => {
  const record = { id: 'bsd-unspecified-next', objective: 'Retained partial objective.', objective_truncated: true };
  const root = render(snapshot({ version: 2, related_tasks: [record], related_tasks_truncated: true }));
  const context = root.findByProps({ 'data-evidence-related-tasks': true });
  expect(context.findAllByType('p').some(node => node.children.includes('The generation material contains only some related tasks.'))).toBe(true);
  const row = context.findByProps({ 'data-evidence-related-task': record.id });
  expect(row.findAllByType('h3')).toHaveLength(0);
  expect(row.findAllByType('code').map(node => node.children.join(''))).toEqual([record.id]);
  expect(row.findAllByType('p').flatMap(node => node.children)).not.toContain('Retained status: ');
  expect(row.findAllByType('p').flatMap(node => node.children)).not.toContain('Dependencies: ');
  expect(row.findAllByType('p').some(node => node.children.includes('This retained excerpt includes shortened content.'))).toBe(true);
  expect(JSON.parse(row.findByProps({ 'data-evidence-json': 'related-task' }).children.join(''))).toEqual(record);
});
