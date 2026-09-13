import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { ReadingQuestionEditor } from './ReadingQuestionEditor';
import type { FoundationDraft } from './foundation';

const language = vi.hoisted(() => ({ locale: 'zh-CN' }));
vi.mock('../i18n', () => ({ useI18n: () => language }));

let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; language.locale = 'zh-CN'; });

const source = {
  source_id: '45fc60c7-3e93-4c70-8221-3fc40d3e5341', title: 'The explanation the reader selected',
  generated_at: 120, path: 'reader-progress/project-one/source.md', task_id: 'task-one',
  card_key: 'task-one', copy_revision: 2,
};

it('lets a beginner choose a question while retaining the exact source and selected parent until explicit submission', () => {
  let draft: FoundationDraft = { question: '', progressSource: source, parentId: 'answer-one', parentTitle: 'Earlier answer' };
  const submit = vi.fn(), focus = vi.fn();
  const render = () => <ReadingQuestionEditor draft={draft} disabled={false} onSubmit={submit}
    onChange={next => { draft = next; renderer!.update(render()); }} />;
  act(() => { renderer = create(render(), { createNodeMock: node => node.type === 'textarea' ? { focus } : null }); });
  const choice = renderer!.root.findAllByType('button').find(button => button.children.includes('从头讲清楚'))!;
  act(() => choice.props.onClick());
  expect(draft.question).toContain('哪些条件下能比较');
  expect(draft.progressSource).toBe(source);
  expect(draft.parentId).toBe('answer-one');
  expect(draft.parentTitle).toBe('Earlier answer');
  expect(submit).not.toHaveBeenCalled();
  expect(focus).toHaveBeenCalledOnce();
  expect(renderer!.root.findAll(node => node.props['data-reading-question-suggestions'] !== undefined)).toHaveLength(0);
  act(() => renderer!.root.findAllByType('button').find(button => button.props['data-reading-submit'] !== undefined)!.props.onClick());
  expect(submit).toHaveBeenCalledOnce();
});

it('keeps an existing unsent question visible without offering replacement suggestions', () => {
  const change = vi.fn(), submit = vi.fn();
  const question = '我写到一半的问题，请保留。';
  act(() => { renderer = create(<ReadingQuestionEditor draft={{ question, progressSource: source }}
    onChange={change} onSubmit={submit} />); });
  expect(renderer!.root.findByType('textarea').props.value).toBe(question);
  expect(renderer!.root.findAll(node => node.props['data-reading-question-suggestions'] !== undefined)).toHaveLength(0);
  expect(change).not.toHaveBeenCalled();
  expect(submit).not.toHaveBeenCalled();
});

it('does not suggest a source-dependent question for a new standalone foundation with no reading context', () => {
  act(() => { renderer = create(<ReadingQuestionEditor draft={{ question: '', sourceTaskId: 'provenance-only' }}
    onChange={vi.fn()} onSubmit={vi.fn()} />); });
  expect(renderer!.root.findAll(node => node.props['data-reading-question-suggestions'] !== undefined)).toHaveLength(0);
});

it('keeps suggestion and submit actions disabled while the shared request lifecycle blocks creation', () => {
  act(() => { renderer = create(<ReadingQuestionEditor draft={{ question: '', progressSource: source }} disabled
    onChange={vi.fn()} onSubmit={vi.fn()} />); });
  expect(renderer!.root.findAllByType('button')).toHaveLength(4);
  expect(renderer!.root.findAllByType('button').every(button => button.props.disabled)).toBe(true);
});

it('uses English suggestions for an existing parent without changing that parent or sending a request', () => {
  language.locale = 'en-US';
  const change = vi.fn(), submit = vi.fn();
  act(() => { renderer = create(<ReadingQuestionEditor draft={{ question: '', parentId: 'old-foundation-answer' }}
    onChange={change} onSubmit={submit} />); });
  const choice = renderer!.root.findAllByType('button').find(button => button.children.includes('Work through an example'))!;
  act(() => choice.props.onClick());
  expect(change.mock.calls[0][0].parentId).toBe('old-foundation-answer');
  expect(change.mock.calls[0][0].question).toContain('Change one input');
  expect(submit).not.toHaveBeenCalled();
});
