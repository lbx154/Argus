import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { DiffLines } from '../components/DeliveryChanges';
import { ArtifactEditor } from '../components/ExperiencePanels';

let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const textOf = (node: { children: unknown[] }): string =>
  node.children.map(child => (typeof child === 'string' ? child : textOf(child as { children: unknown[] }))).join('');

const diff = '--- a/report.md\n+++ b/report.md\n@@ -1,3 +1,3 @@\n # Sales\n \n-North 10\n+North 12\n';

it('reads a unified diff as changes at their lines, without the diff headers', () => {
  const html = renderToStaticMarkup(<DiffLines diff={diff} truncated={false} zh />);
  expect(html).toContain('<span class="hunk">第 1 行</span>');
  expect(html).toContain('<span class="del">-North 10\n</span><span class="add">+North 12\n</span>');
  expect(html).not.toContain('report.md');
  expect(html).not.toContain('@@');
});

it('opens on the source to edit, without calling it an edition, and compares the draft with the source', async () => {
  vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:8000' }, confirm: () => true });
  const editionDiff = vi.spyOn(api, 'editionDiff').mockResolvedValue({ diff, truncated: false });
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['editions', 'one', 'artifact', 'report.md'], { original: '# Sales\n\nNorth 10\n', versions: [] });
  await act(async () => {
    renderer = create(<QueryClientProvider client={client}>
      <ArtifactEditor sid="one" path="report.md" onClose={() => {}} onRequest={() => {}} />
    </QueryClientProvider>);
  });
  const root = renderer!.root;
  expect(root.findByProps({ 'aria-label': 'Edition content' }).props.value).toBe('# Sales\n\nNorth 10\n');
  expect(textOf(root as never)).not.toContain('Unreviewed');
  await act(async () => { root.findByProps({ 'aria-label': 'Edition content' }).props.onChange({ target: { value: '# Sales\n\nNorth 12\n' } }); });
  await act(async () => { root.findByProps({ 'data-view': 'compare' }).props.onClick(); });
  expect(editionDiff).toHaveBeenCalledWith('one', 'report.md', '# Sales\n\nNorth 12\n');
  expect(root.findByType(DiffLines).props.diff).toBe(diff);
  client.clear();
});
