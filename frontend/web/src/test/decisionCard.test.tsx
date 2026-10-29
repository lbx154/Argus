import { useState } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { operatorDecisionCards, questionRole } from '../../../core/src/decisions';
import { PendingBanner } from '../components/PendingBanner';
import * as i18n from '../i18n';

const [card] = operatorDecisionCards([{ id: 't1', operator_decision: {
  id: 'engineer-t1', item_id: 't1', status: 'pending', title: '编写并运行 hello.py',
  question: '请问输出用中文还是英文？', options: [],
} }], [{ id: 't1', title: '编写并运行 hello.py' }]);
let renderer: ReactTestRenderer;
afterEach(() => { act(() => renderer?.unmount()); vi.restoreAllMocks(); });

it('joins the actual role, task name and question in one sentence without a dialog', () => {
  vi.spyOn(i18n, 'useI18n').mockReturnValue({ locale: 'zh-CN', setLocale: vi.fn(), t: (key, vars) => i18n.translate(key, vars, 'zh-CN') });
  const html = renderToStaticMarkup(<PendingBanner reply={card} busy={false} error="" note="" onNoteChange={() => {}} onSubmit={async () => true} />);
  expect(html).toContain('工程师在「编写并运行 hello.py」上问：请问输出用中文还是英文？');
  expect(html.match(/请问输出用中文还是英文？/g)).toHaveLength(1);
  expect(html).not.toContain('role="dialog"');
  expect(html).not.toContain('<textarea');
});

it.each(['engineer', 'reviewer', 'planner'])('uses the server answer ID for the %s, including task IDs containing hyphens', role => {
  expect(questionRole({ ...card, id: `${role}-task-with-hyphens`, item_id: 'task-with-hyphens' })).toBe(role);
  expect(questionRole({ ...card, id: role, item_id: '' })).toBe(role);
});

it('answers in place, guards IME composition, and keeps a failed answer available to retry', async () => {
  const submit = vi.fn(async () => false);
  function Answer() {
    const [note, setNote] = useState('');
    return <PendingBanner reply={card} busy={false} error="Please retry" note={note} onNoteChange={setNote} onSubmit={submit} />;
  }
  act(() => { renderer = create(<Answer />); });
  act(() => renderer.root.findByType('button').props.onClick());
  const input = () => renderer.root.findByType('textarea');
  act(() => input().props.onChange({ target: { value: '中文' } }));
  const preventDefault = vi.fn();
  act(() => input().props.onKeyDown({ key: 'Enter', ctrlKey: true, nativeEvent: { isComposing: true }, preventDefault }));
  expect(submit).not.toHaveBeenCalled();
  await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault }));
  expect(submit).toHaveBeenCalledWith('中文');
  expect(input().props.value).toBe('中文');
  expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toBe('Please retry');
});
