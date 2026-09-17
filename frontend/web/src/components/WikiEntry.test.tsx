import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type WikiOverview, type WikiPageSummary } from '../api';
import { WikiEntry } from './WikiEntry';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'en-US', t: (key: string) => key }) }));
vi.mock('../api', () => ({ api: { wiki: vi.fn(), wikiPage: vi.fn() } }));
vi.mock('../lib/format', () => ({ formatRelativeTime: (ts: number) => `rel:${ts}` }));
vi.mock('./MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <div data-markdown>{children}</div> }));
vi.mock('./Modal', () => ({
  Modal: ({ open, onClose, children, label }: { open: boolean; onClose: () => void; children: unknown; label: string }) =>
    open ? <div role="dialog" aria-label={label}><button data-modal-close onClick={onClose}>close</button>{children as never}</div> : null,
  ModalHeader: ({ title, sub }: { title: string; sub?: string }) => <div data-modal-header>{title}{sub}</div>,
}));

const page = (name: string, updated_at: number): WikiPageSummary => ({
  path: `pages/${name.toLowerCase()}.md`, title: name, description: `About ${name}`, updated_at,
});
const fixture: WikiOverview = {
  exists: true, root: '.autors/proj/wiki', index_markdown: '# Index\n',
  pages: [page('Newest', 60), page('Fifth', 50), page('Fourth', 40), page('Third', 30), page('Second', 20), page('Oldest', 10)],
};
let client: QueryClient;
let renderer: ReactTestRenderer;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const button = (name: string) => renderer.root.findAllByType('button').find(node => content(node).startsWith(name))!;
const dialogs = () => renderer.root.findAllByProps({ role: 'dialog' });
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
async function mount(props: { sid?: string | null; compact?: boolean } = {}) {
  await act(async () => {
    renderer = create(<QueryClientProvider client={client}><WikiEntry sid={props.sid === undefined ? 'one' : props.sid} compact={props.compact} /></QueryClientProvider>);
  });
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.mocked(api.wiki).mockResolvedValue(structuredClone(fixture));
  vi.mocked(api.wikiPage).mockImplementation(async (_sid, path) => ({ path, title: `Title of ${path}`, markdown: 'Page body. '.repeat(50), truncated: false, updated_at: 60 }));
});
afterEach(() => { act(() => renderer?.unmount()); client.clear(); vi.useRealTimers(); });

it('shows one muted line when the project has no wiki', async () => {
  vi.mocked(api.wiki).mockResolvedValue({ exists: false });
  await mount();
  expect(api.wiki).toHaveBeenCalledWith('one', expect.any(AbortSignal));
  expect(content(renderer.root)).toContain('No wiki pages yet');
  expect(renderer.root.findAllByType('button')).toHaveLength(1);
  expect(dialogs()).toHaveLength(0);
});

it('renders nothing without a project and stays quiet when the request fails', async () => {
  await mount({ sid: null });
  expect(api.wiki).not.toHaveBeenCalled();
  expect(renderer.root.findAllByType('section')).toHaveLength(0);
  vi.mocked(api.wiki).mockRejectedValue(new Error('Offline'));
  await act(async () => renderer.update(<QueryClientProvider client={client}><WikiEntry sid="two" /></QueryClientProvider>)); await settle();
  expect(content(renderer.root)).toContain('Wiki unavailable');
  expect(content(renderer.root)).not.toContain('No wiki pages yet');
});

it('lists the five most recently updated pages with the page count', async () => {
  await mount();
  const text = content(renderer.root);
  expect(text).toContain('Wiki');
  expect(renderer.root.findByProps({ 'aria-label': '6 pages' })).toBeDefined();
  const rows = renderer.root.findAllByType('button').slice(1).map(content);
  expect(rows).toEqual(['Newestrel:60', 'Fifthrel:50', 'Fourthrel:40', 'Thirdrel:30', 'Secondrel:20']);
  expect(text).not.toContain('Oldest');
  expect(text).not.toContain('No wiki pages yet');
});

it('opens a page in the modal, reads its body, and closes again', async () => {
  await mount();
  act(() => button('Fourth').props.onClick()); await settle();
  expect(dialogs()).toHaveLength(1);
  expect(api.wikiPage).toHaveBeenCalledWith('one', 'pages/fourth.md', expect.any(AbortSignal));
  const dialog = content(dialogs()[0]);
  expect(dialog).toContain('Title of pages/fourth.md');
  expect(dialog).toContain('pages/fourth.md');
  expect(dialog).toContain('Updated ');
  expect(content(renderer.root.findByProps({ 'data-markdown': true })).length).toBeGreaterThan(500);
  act(() => renderer.root.findByProps({ 'data-modal-close': true }).props.onClick());
  expect(dialogs()).toHaveLength(0);
  expect(renderer.root.findAllByProps({ 'data-markdown': true })).toHaveLength(0);
});

it('lets the modal list every page and step back from a page to the list', async () => {
  await mount();
  act(() => button('Wiki').props.onClick()); await settle();
  const list = renderer.root.findByProps({ 'aria-label': 'Wiki pages' });
  const rows = list.findAllByType('button').map(content);
  expect(rows[0]).toBe('INDEX.md');
  expect(rows.slice(1)).toHaveLength(6);
  expect(rows.at(-1)).toContain('Oldest');
  expect(content(dialogs()[0])).toContain('.autors/proj/wiki · 6 pages');
  act(() => list.findAllByType('button').at(-1)!.props.onClick()); await settle();
  expect(api.wikiPage).toHaveBeenCalledWith('one', 'pages/oldest.md', expect.any(AbortSignal));
  act(() => button('← Back to pages').props.onClick());
  expect(renderer.root.findByProps({ 'aria-label': 'Wiki pages' }).findAllByType('button')).toHaveLength(7);
  act(() => button('INDEX.md').props.onClick());
  expect(content(renderer.root.findByProps({ 'data-markdown': true }))).toBe('# Index\n');
});

it('shows only an icon with a badge in the slim sidebar and opens the page list from it', async () => {
  await mount({ compact: true });
  expect(renderer.root.findAllByType('button')).toHaveLength(1);
  expect(content(renderer.root)).toBe('6');
  expect(content(renderer.root)).not.toContain('Newest');
  act(() => renderer.root.findByType('button').props.onClick()); await settle();
  expect(dialogs()).toHaveLength(1);
  expect(content(dialogs()[0])).toContain('Oldest');
});

it('picks up a page the project just wrote without remounting', async () => {
  await mount();
  const next: WikiOverview = { ...fixture, pages: [page('Just written', 70), ...fixture.pages] };
  vi.mocked(api.wiki).mockResolvedValue(next);
  await act(async () => { await client.invalidateQueries({ queryKey: ['wiki', 'one'] }); }); await settle();
  expect(content(renderer.root.findAllByType('button')[1])).toContain('Just written');
  expect(renderer.root.findByProps({ 'aria-label': '7 pages' })).toBeDefined();
});
