import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it } from 'vitest';
import { MarkdownContent } from '../components/MarkdownContent';
import type { ReaderLearningPath } from '../map/presentation';
import { ReaderExplanation } from './ReaderExplanation';
import { readerBrief } from './testFixtures';

// Renderer fixtures, not model output or evidence about a research run.
const path: ReaderLearningPath = {
  question: 'Can waiting time fall without completing fewer jobs?',
  steps: [
    { title: 'Measure a wait', explanation: 'A job arrives, then waits.\n\nSubtract arrival time from its start time.',
      example: 'In this teaching example a job arrives at **2** seconds and starts at 5: it waits 3 seconds.',
      check: { question: 'What if it starts at 6 seconds?', answer: 'The wait is 6 − 2 = **4 seconds**.' } },
    { title: 'Compare both quantities', explanation: 'Count completed jobs in the same observation window.',
      example: 'A shorter wait alone does not say how many jobs finished.',
      check: { question: 'Can the wait alone establish both requirements?', answer: 'No. The completed-job count is also needed.' } },
  ],
};

let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; });

function inDisclosure(node: ReactTestInstance) {
  for (let parent = node.parent; parent; parent = parent.parent) if (parent.type === 'details') return true;
  return false;
}

it('shows the question and complete ordered steps before scope, with only answers folded', () => {
  act(() => { renderer = create(<ReaderExplanation brief={readerBrief} learningPath={path} identity="a" detail="Retained formal conditions" />); });
  const root = renderer!.root;
  expect(root.findAllByProps({ 'data-reader-teaching': 'a' })).toHaveLength(0);
  const learning = root.findByProps({ 'data-reader-learning-path': 'a' });
  expect(learning.findByType('ol').findAllByType('li')).toHaveLength(2);
  expect(learning.findAllByType('h4').map(node => node.children.at(-1))).toEqual(path.steps.map(step => step.title));
  for (const [index, step] of path.steps.entries()) {
    const rendered = learning.findByProps({ 'data-learning-step': index + 1 });
    const explanation = rendered.findByProps({ 'data-learning-explanation': true });
    const example = rendered.findByProps({ 'data-learning-example': true });
    const question = rendered.findByProps({ 'data-learning-check-question': true });
    const answer = rendered.findByProps({ 'data-learning-check-answer': true });
    for (const node of [explanation, example, question]) expect(inDisclosure(node)).toBe(false);
    expect(inDisclosure(answer)).toBe(true);
    expect(explanation.findByType(MarkdownContent).props.children).toBe(step.explanation);
    expect(example.findByType(MarkdownContent).props.children).toBe(step.example);
    expect(question.findByType(MarkdownContent).props.children).toBe(step.check.question);
    expect(answer.findByType(MarkdownContent).props.children).toBe(step.check.answer);
    const disclosure = rendered.findByType('details');
    expect(disclosure.props.open).toBeUndefined();
    expect(disclosure.findByType('summary').children).toEqual(['Show the answer']);
  }
  const markdown = root.findAllByType(MarkdownContent).map(node => node.props.children);
  expect(markdown).not.toContain(readerBrief.why);
  expect(markdown).toEqual([path.question,
    ...path.steps.flatMap(step => [step.explanation, step.example, step.check.question, step.check.answer]),
    readerBrief.scope, readerBrief.next, 'Retained formal conditions']);
  expect(root.findAllByType('input')).toHaveLength(0);
  expect(root.findAllByType('form')).toHaveLength(0);

  const markup = renderToStaticMarkup(<ReaderExplanation brief={readerBrief} learningPath={path} identity="a" />);
  expect(markup).toContain('A job arrives, then waits.</p>');
  expect(markup).toMatch(/<strong\b[^>]*>2<\/strong>/);
  expect(markup).not.toContain('line-clamp');
  expect(markup).not.toContain('overflow-hidden');
});

it('replaces native answer disclosures on a task switch, even when the teaching text is identical', () => {
  act(() => { renderer = create(<ReaderExplanation brief={readerBrief} learningPath={path} identity="a" />); });
  const original = renderer!.root.findAllByType('details')[0];
  act(() => renderer!.update(<ReaderExplanation brief={readerBrief} learningPath={path} identity="a" />));
  expect(renderer!.root.findAllByType('details')[0]).toBe(original);
  act(() => renderer!.update(<ReaderExplanation brief={readerBrief} learningPath={path} identity="b" />));
  expect(renderer!.root.findAllByType('details')[0]).not.toBe(original);
  expect(renderer!.root.findAllByProps({ 'data-reader-learning-path': 'a' })).toHaveLength(0);
  expect(renderer!.root.findByProps({ 'data-reader-learning-path': 'b' }).findAllByType('details').every(node => node.props.open === undefined)).toBe(true);
});

it('replaces a changed answer instead of retaining the previous disclosure', () => {
  act(() => { renderer = create(<ReaderExplanation brief={readerBrief} learningPath={path} identity="a" />); });
  const original = renderer!.root.findAllByType('details')[0];
  const updated = structuredClone(path);
  updated.steps[0].check.answer = 'Updated explanation of this answer.';
  act(() => renderer!.update(<ReaderExplanation brief={readerBrief} learningPath={updated} identity="a" />));
  expect(renderer!.root.findAllByType('details')[0]).not.toBe(original);
  const values = renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children);
  expect(values).toContain(updated.steps[0].check.answer);
  expect(values).not.toContain(path.steps[0].check.answer);
});

it.each([undefined, null])('retains the existing concept and source detail when the path is %s', learningPath => {
  act(() => { renderer = create(<ReaderExplanation brief={readerBrief} learningPath={learningPath} identity="legacy" detail="Legacy detail" />); });
  expect(renderer!.root.findAllByProps({ 'data-reader-learning-path': 'legacy' })).toHaveLength(0);
  expect(renderer!.root.findByProps({ 'data-reader-teaching': 'legacy' })).toBeDefined();
  expect(renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children)).toEqual([
    readerBrief.why, readerBrief.concept!.explanation, readerBrief.concept!.example, readerBrief.concept!.connection,
    readerBrief.scope, readerBrief.next, 'Legacy detail',
  ]);
});
