import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import { EventStream } from './serverSnapshot';

afterEach(() => vi.unstubAllGlobals());

it('an ended task keeps its whole title and only the first selected file while its closing progress stays in the reader', () => {
  vi.stubGlobal('window', { requestAnimationFrame: vi.fn(() => 1), cancelAnimationFrame: vi.fn() });
  const title = '对话任务完整标题与交付文件'.repeat(14) + '标题结束';
  const events = [{ type: 'ui.task', row_id: 'task-t1', item_id: 't1', title, state: 'done',
    progress: '计算已完成，输出已核对。', result_paths: ['result.md', 'a.py'], ts: 1 }];
  const open = vi.fn();
  const view = <EventStream events={events} connected showReasoning={false} onOpenArtifact={open} />;
  const html = renderToStaticMarkup(view);
  expect(html).toContain(title);
  expect(html).not.toContain('计算已完成，输出已核对。');
  expect(html).toContain('Ended');
  expect(html).not.toContain('a.py');
  expect(events[0].progress).toBe('计算已完成，输出已核对。');
  let root!: ReturnType<typeof create>;
  act(() => { root = create(view); });
  const file = root.root.findAllByType('button').find(button => button.children.join('') === 'result.md')!;
  act(() => file.props.onClick());
  expect(open).toHaveBeenCalledWith('result.md');
  act(() => root.unmount());
});

it.each(['zh-CN', 'en'])('names the current role on the running task, including a handoff, in %s', locale => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  vi.stubGlobal('document', { documentElement: { lang: '' } });
  vi.stubGlobal('window', { requestAnimationFrame: vi.fn(() => 1), cancelAnimationFrame: vi.fn() });
  const task = { type: 'ui.task', row_id: 'task-t1', item_id: 't1', title: '日报工具与测试', state: 'running', result_paths: [], ts: 1 };
  const labels = locale === 'zh-CN' ? ['工程师在做', '审阅者在查', '规划者在排'] : ['Engineer working', 'Reviewer checking', 'Planner planning'];
  const shown = (role: string) => <I18nProvider><EventStream events={[{ ...task, role }]} connected showReasoning={false} /></I18nProvider>;
  let root!: ReturnType<typeof create>;
  act(() => { root = create(shown('engineer')); });
  for (const [index, role] of ['engineer', 'reviewer', 'planner'].entries()) {
    act(() => root.update(shown(role)));
    const line = root.root.findByProps({ 'data-task-work': 't1' });
    expect(line.findByType('button').children.join('')).toBe(`${labels[index]}${locale === 'zh-CN' ? '：' : ': '}日报工具与测试`);
  }
  act(() => root.unmount());
});
