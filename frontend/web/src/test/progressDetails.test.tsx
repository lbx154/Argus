import { renderToStaticMarkup } from 'react-dom/server';
import { create, act, type ReactTestRenderer } from 'react-test-renderer';
import { expect, it, vi } from 'vitest';
import { UsageBreakdown } from '../components/UsageBreakdown';
import { TaskLetters } from '../components/TaskLetters';
import { I18nProvider } from '../i18n';
import { workDurationLabel } from '../lib/projectOverview';
import { snapshot } from '../../../../packages/core/src/serve/views';
import { Store } from '../../../../packages/core/src/store';
import { fileURLToPath } from 'node:url';

it('renders snapshot dimensions, known spend and missing-duration bounds without fetching', () => {
  const store = new Store(fileURLToPath(new URL('./fixtures/home', import.meta.url)));
  const usage = snapshot(store, store.load('p-c855623c'), { since: null, calls: [], requests: [] }).usage_summary!;
  const fetcher = vi.spyOn(globalThis, 'fetch');
  const html = renderToStaticMarkup(<UsageBreakdown usage={{ ...usage, work_duration_ms: 105000, calls_without_duration: 1 }} />);
  for (const text of ['by_role', 'by_model', 'by_date', 'Total', 'By date (UTC)', 'Worked ≥1m 45s', '1 calls lack a duration']) expect(html).toContain(text);
  expect(fetcher).not.toHaveBeenCalled();
  fetcher.mockRestore();
  expect(workDurationLabel(3661000, true)).toBe('已工作 1小时 1分 1秒');
});

it('renders the original return reason, latest-turn files and only the first result image with the existing viewer', () => {
  const open = vi.fn();
  const item = { id: 't1', title: 'Chart', status: 'done', objective: '', priority: 0,
    letters: [{ role: 'reviewer', to: 'engineer', text: '退回：缺少坐标单位。' }, { role: 'engineer', to: 'reviewer', text: '已修正。' }],
    changed_files: ['plot.ts'], result_paths: ['result.md', 'first.png', 'second.png'] };
  const html = renderToStaticMarkup(<TaskLetters item={item} sid="p-fixture" onOpenArtifact={open} />);
  expect(html).toContain('退回：缺少坐标单位。');
  // Who wrote to whom reads in the page's language.
  expect(html).toContain('Reviewer → Engineer');
  vi.stubGlobal('localStorage', { getItem: () => 'zh-CN' });
  expect(renderToStaticMarkup(<I18nProvider><TaskLetters item={item} sid="p-fixture" /></I18nProvider>)).toContain('审阅者 → 工程师');
  vi.unstubAllGlobals();
  expect(html).toContain('Files changed this turn');
  expect(html).toContain('plot.ts');
  expect(html).toContain('data-task-thumbnail="first.png"');
  expect(html).not.toContain('data-task-thumbnail="second.png"');
  let tree!: ReactTestRenderer;
  act(() => { tree = create(<TaskLetters item={item} sid={null} onOpenArtifact={open} />); });
  tree.root.findAllByType('button').find(button => button.children.join('') === 'plot.ts')!.props.onClick();
  expect(open).toHaveBeenCalledWith('plot.ts');
  act(() => tree.unmount());
});
