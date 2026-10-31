import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../../../core/src/http';
import { api } from '../api';
import { LedgerPanel } from '../components/LedgerPanel';
vi.mock('../api', () => ({ api: { skillDocument: vi.fn() } }));
vi.mock('../i18n', async importOriginal => ({ ...await importOriginal<typeof import('../i18n')>(), useI18n: () => ({ locale: 'zh-CN', t: (key: string) => key }) }));
let client: QueryClient, view: ReactTestRenderer | undefined;
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});
const render = async (sid = 'p-one') => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LedgerPanel sid={sid} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
};
it('reads only the project TSV content via the document endpoint and renders a table', async () => {
  vi.mocked(api.skillDocument).mockResolvedValue({ content: 'run\tvalue\nr1\t10\nr2\t12\n' });
  await render();
  expect(api.skillDocument).toHaveBeenCalledWith('p-one', 'shared', 'runs/p-one.tsv', expect.any(AbortSignal));
  expect(view!.root.findAllByType('table')).toHaveLength(1);
  expect(view!.root.findAllByType('th').map(node => node.children.join(''))).toEqual(['run', 'value']);
  expect(view!.root.findAllByType('td').map(node => node.children.join(''))).toEqual(['r1', '10', 'r2', '12']);
});
it('hides the entire card on 404 without retrying', async () => {
  vi.mocked(api.skillDocument).mockRejectedValue(new ApiError('missing', 404, 'GET', '/api/skill-library/document'));
  await render();
  expect(view!.toJSON()).toBeNull(); expect(api.skillDocument).toHaveBeenCalledTimes(1);
});
it('shows other endpoint failures rather than treating them as missing ledgers', async () => {
  vi.mocked(api.skillDocument).mockRejectedValue(new ApiError('unavailable', 500, 'GET', '/api/skill-library/document'));
  await render(); expect(view!.root.findAllByProps({ role: 'alert' })).toHaveLength(1);
});
it('uses a new project path when the project changes', async () => {
  vi.mocked(api.skillDocument).mockResolvedValue({ content: 'run\tvalue\nr1\t10\n' });
  await render();
  await act(async () => view!.update(<QueryClientProvider client={client}><LedgerPanel sid="p-two" /></QueryClientProvider>));
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
  expect(api.skillDocument).toHaveBeenLastCalledWith('p-two', 'shared', 'runs/p-two.tsv', expect.any(AbortSignal));
});
afterEach(() => { if (view) act(() => view!.unmount()); view = undefined; client.clear(); vi.useRealTimers(); });
