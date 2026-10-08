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
const state = (status: 'running' | 'completed' | 'unchanged' | 'failed' | 'skipped',
  outcome: LearningState['jobs'][number]['outcome'] = {}): LearningState => ({
  pending: status === 'running' ? 1 : 0, revision: status === 'running' ? 1 : 2,
  jobs: [{ id: 'turn', status, created: 1, updated: 2, attempts: 1, outcome: status === 'completed'
    ? { counts: { knowledge: 1, skills: 1, preferences: 0 }, items: [{ channel: 'knowledge', title: 'CRISPR 机制与来源', scope: 'global', path: 'pages/crispr.md' }] } : outcome }],
});
const tree = (sid = 'one', channel?: 'knowledge') => <QueryClientProvider client={client}><LearningStatus sid={sid} channel={channel} /></QueryClientProvider>;
async function settle() { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); }
async function mount(status: Parameters<typeof state>[0], outcome?: Parameters<typeof state>[1], channel?: 'knowledge') {
  vi.mocked(api.learningStatus).mockResolvedValue(state(status, outcome));
  await act(async () => { renderer = create(tree('one', channel)); }); await settle();
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
  // A library this turn did not change is not mentioned at all.
  expect(content(renderer.root)).not.toContain('用户偏好');
  expect(content(renderer.root)).toContain('CRISPR 机制与来源');
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['wiki-library'] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['skill-library'] });
});

it('shows nothing for a finished check that added nothing, in the workspace or a library', async () => {
  await mount('unchanged');
  expect(renderer.toJSON()).toBeNull();
  act(() => renderer.unmount());
  await mount('unchanged', {}, 'knowledge');
  expect(renderer.toJSON()).toBeNull();
});

it('offers retry for failed learning without re-sending the user task', async () => {
  await mount('failed');
  expect(content(renderer.root)).toContain('本轮学习未完成，回复不受影响');
  expect(content(renderer.root)).not.toContain('知识库更新未完成');
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

it('says nothing when the turn had nothing to learn', async () => {
  await mount('skipped', { reason: 'not_needed' });
  expect(renderer.toJSON()).toBeNull();
});

it('keeps a policy pause out of the workspace, where the reply already explains it', async () => {
  await mount('skipped', { reason: 'paused', detail: 'cost_unreconciled' });
  expect(renderer.toJSON()).toBeNull();
});

it('explains a policy pause in plain words inside the library, not as a failure', async () => {
  await mount('skipped', { reason: 'paused', detail: 'cost_unreconciled' }, 'knowledge');
  const text = content(renderer.root);
  expect(text).toContain('本轮未学习：上一次模型调用的费用尚未结算');
  expect(text).not.toContain('未完成');
  expect(renderer.root.findByProps({ 'data-learning-status': 'skipped' })).toBeTruthy();
  expect(renderer.root.findAllByProps({ role: 'alert' })).toHaveLength(0);
  vi.mocked(api.retryLearning).mockResolvedValue(state('running'));
  await act(async () => renderer.root.findByType('button').props.onClick()); await settle();
  expect(api.retryLearning).toHaveBeenCalledWith('one', 'turn');
});
