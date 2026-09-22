import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type LearningState } from '../api';
import { LearningStatus } from './LearningStatus';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'zh-CN' }) }));
vi.mock('../api', () => ({ api: { learningStatus: vi.fn(), retryLearning: vi.fn() } }));
let client: QueryClient;
let renderer: ReactTestRenderer;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const state = (status: 'running' | 'completed' | 'unchanged' | 'failed'): LearningState => ({
  pending: status === 'running' ? 1 : 0, revision: status === 'running' ? 1 : 2,
  jobs: [{ id: 'turn', status, created: 1, updated: 2, attempts: 1, outcome: status === 'completed'
    ? { counts: { knowledge: 1, skills: 1, preferences: 0 }, items: [{ channel: 'knowledge', title: 'CRISPR 机制与来源', scope: 'global', path: 'pages/crispr.md' }] } : {} }],
});
const tree = (sid = 'one') => <QueryClientProvider client={client}><LearningStatus sid={sid} /></QueryClientProvider>;
async function settle() { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); }
async function mount(status: Parameters<typeof state>[0]) {
  vi.mocked(api.learningStatus).mockResolvedValue(state(status));
  await act(async () => { renderer = create(tree()); }); await settle();
}
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});
afterEach(() => { act(() => renderer?.unmount()); client.clear(); vi.useRealTimers(); });

it('shows real background work after delivery, then refreshes libraries and shows what was saved', async () => {
  await mount('running');
  expect(content(renderer.root)).toContain('知识库正在更新');
  expect(content(renderer.root)).toContain('技能库正在更新');
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  await act(async () => { client.setQueryData(['learning-status', 'one'], state('completed')); });
  await settle();
  expect(content(renderer.root)).toContain('知识库已更新 · 1 条');
  expect(content(renderer.root)).toContain('技能库已更新 · 1 条');
  expect(content(renderer.root)).toContain('用户偏好已检查，无新增');
  expect(content(renderer.root)).toContain('CRISPR 机制与来源');
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['wiki-library'] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['skill-library'] });
});

it('explains a no-op and never presents it as an update', async () => {
  await mount('unchanged');
  expect(content(renderer.root)).toContain('知识库已检查，无新增');
  expect(content(renderer.root)).not.toContain('已更新');
});

it('offers retry for failed learning without re-sending the user task', async () => {
  await mount('failed');
  expect(content(renderer.root)).toContain('更新未完成');
  vi.mocked(api.retryLearning).mockResolvedValue(state('running'));
  await act(async () => renderer.root.findByType('button').props.onClick()); await settle();
  expect(api.retryLearning).toHaveBeenCalledWith('one', 'turn');
  expect(content(renderer.root)).toContain('知识库正在更新');
});

it('does not show the previous project learning after switching projects', async () => {
  await mount('completed');
  vi.mocked(api.learningStatus).mockResolvedValue({ jobs: [], pending: 0, revision: 0 });
  await act(async () => renderer.update(tree('two'))); await settle();
  expect(content(renderer.root)).not.toContain('CRISPR');
});
