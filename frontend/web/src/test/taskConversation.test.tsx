import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { EventStream } from './serverSnapshot';

afterEach(() => vi.unstubAllGlobals());

it('a task shows the whole title, its progress, and opens each delivered file with the existing file callback', () => {
  vi.stubGlobal('window', { requestAnimationFrame: vi.fn(() => 1), cancelAnimationFrame: vi.fn() });
  const title = '对话任务完整标题与交付文件'.repeat(14) + '标题结束';
  const events = [{ type: 'ui.task', row_id: 'task-t1', item_id: 't1', title, state: 'done',
    progress: '计算已完成，输出已核对。', result_paths: ['result.md', 'a.py'], ts: 1 }];
  const open = vi.fn();
  const view = <EventStream events={events} connected showReasoning={false} onOpenArtifact={open} />;
  const html = renderToStaticMarkup(view);
  expect(html).toContain(title);
  expect(html).toContain('计算已完成，输出已核对。');
  let root!: ReturnType<typeof create>;
  act(() => { root = create(view); });
  const file = root.root.findAllByType('button').find(button => button.children.join('') === 'result.md')!;
  act(() => file.props.onClick());
  expect(open).toHaveBeenCalledWith('result.md');
  act(() => root.unmount());
});
