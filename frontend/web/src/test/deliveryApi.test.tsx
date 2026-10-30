import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api, adoptTokenFromUrl } from '../api';
import { useArtifact } from '../hooks';

let tree: ReactTestRenderer | undefined;
afterEach(() => { act(() => tree?.unmount()); tree = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('carries the delivery in artifact, raw, HTML, archive and model resource requests', async () => {
  const location = new URL('http://argus.test/?token=delivery-test');
  vi.stubGlobal('window', { location, history: { replaceState() {} } });
  vi.stubGlobal('localStorage', { getItem: () => null, setItem() {} });
  adoptTokenFromUrl();
  const fetcher = vi.fn(async () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }));
  vi.stubGlobal('fetch', fetcher);
  await api.artifact('s', 'index.html', undefined, 'd12');
  await api.artifactPreview('s', 'index.html', undefined, 'd12');
  await api.artifactBlob('s', 'index.html', true, undefined, 'd12');
  await api.artifactBundle('s', 'index.html', undefined, 'd12');
  await api.artifactModelAsset('s', 'robot.urdf', 'mesh.obj', undefined, 'd12');
  await api.artifactsBundle('s', ['index.html'], 'Report', 'Old summary', undefined, 'd12');
  const calls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
  for (const [url] of calls.slice(0, 5)) expect(new URL(url, location).searchParams.get('delivery')).toBe('d12');
  expect(JSON.parse(String(calls[5][1].body))).toEqual({ paths: ['index.html'], name: 'Report', summary: 'Old summary', delivery: 'd12' });
  expect(api.artifactRawUrl('s', 'report.pdf', 'd12')).toContain('delivery=d12');
});

it('isolates cached previews of the same path by delivery identity', async () => {
  const read = vi.spyOn(api, 'artifact').mockImplementation(async (_sid, path, _signal, delivery) => ({
    path, name: path, kind: 'text', mime: 'text/plain', size: 1, mtime: 1, preview: delivery === 'd1' ? 'Old saved text' : 'New saved text',
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  function Probe({ delivery }: { delivery: string }) { const q = useArtifact('s', 'report.md', null, delivery); return <p>{q.data?.preview}</p>; }
  const view = (delivery: string) => <QueryClientProvider client={client}><Probe delivery={delivery} /></QueryClientProvider>;
  try {
    await act(async () => { tree = create(view('d1')); });
    await vi.waitFor(() => expect(tree!.root.findByType('p').children).toEqual(['Old saved text']));
    await act(async () => { tree!.update(view('d2')); });
    await vi.waitFor(() => expect(tree!.root.findByType('p').children).toEqual(['New saved text']));
    expect(read.mock.calls.map(call => call[3])).toEqual(['d1', 'd2']);
    expect(client.getQueryData(['artifact', 's', 'report.md', null, 'd1'])).toMatchObject({ preview: 'Old saved text' });
  } finally { act(() => tree?.unmount()); tree = undefined; client.clear(); }
});
