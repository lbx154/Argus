import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi, type Mock } from 'vitest';
import { api, type KnowledgeEvent, type WikiCatalog, type WikiLibraryItem, type WikiScope } from '../api';
import { WikiEntry } from './WikiEntry';
import { wikiQueryKey } from './WikiLibrary';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'en-US', t: (key: string) => key }) }));
vi.mock('../api', () => ({ api: { wikiLibrary: vi.fn(), wikiDocument: vi.fn(), knowledgeFeed: vi.fn() } }));
vi.mock('../lib/format', () => ({ formatRelativeTime: (ts: number) => `rel:${ts}` }));
vi.mock('./MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <div data-markdown>{children}</div> }));
vi.mock('./Modal', () => ({
  ModalHeader: ({ title, sub }: { title: string; sub?: string }) => <div data-modal-header>{title}{sub}</div>,
}));

const page = (name: string, updated_at: number, scope: WikiScope = 'project', vertical = ''): WikiLibraryItem => ({
  scope, vertical, root: scope === 'project' ? '.autors/proj/wiki' : `/shared/wiki/${scope}/${vertical}`,
  path: `pages/${name.toLowerCase().replace(/\s+/g, '-')}.md`, title: name, description: `About ${name}`, updated_at,
  kind: 'page', source: '', created: '', reuse_count: 0,
});
const items = [
  page('Newest', 60), page('Fifth', 50, 'vertical', 'research'), page('Fourth', 40), page('Third', 30, 'global'),
  page('Second', 20), page('Oldest', 10),
];
const fixture: WikiCatalog = {
  scopes: ['global', 'vertical', 'project'],
  libraries: [
    { scope: 'project', vertical: 'research', root: '.autors/proj/wiki', index_markdown: '# Index\n', pages: items.filter(item => item.scope === 'project') },
    { scope: 'vertical', vertical: 'research', root: '/shared/wiki/vertical/research', index_markdown: '', pages: items.filter(item => item.scope === 'vertical'), principles: null },
    { scope: 'global', vertical: '', root: '/shared/wiki/global/', index_markdown: '', pages: items.filter(item => item.scope === 'global'), principles: null },
  ],
  items: [...items],
  verticals: ['research'],
  active_vertical: 'research',
  errors: [],
};
const now = Math.floor(Date.now() / 1000);
const learned = (ts: number, item: WikiLibraryItem, extra: Partial<KnowledgeEvent> = {}): KnowledgeEvent => ({
  ts, kind: 'learned', scope: item.scope, vertical: item.vertical, path: item.path, title: item.title,
  source_project: 'one', mission_id: 'm1', role: 'host', page_kind: 'lesson', note: '', ...extra,
});
let client: QueryClient;
let renderer: ReactTestRenderer;
let onOpen: Mock<(page?: WikiLibraryItem) => void>;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const button = (name: string) => renderer.root.findAllByType('button').find(node => content(node).startsWith(name))!;
const rows = () => renderer.root.findAllByType('button').slice(1).map(content);
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
const tree = (props: { sid?: string | null; compact?: boolean; withOpen?: boolean } = {}) =>
  <QueryClientProvider client={client}>
    <WikiEntry sid={props.sid === undefined ? 'one' : props.sid} compact={props.compact} onOpen={props.withOpen === false ? undefined : onOpen} />
  </QueryClientProvider>;
async function mount(props: { sid?: string | null; compact?: boolean; withOpen?: boolean } = {}) {
  await act(async () => { renderer = create(tree(props)); });
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  onOpen = vi.fn<(page?: WikiLibraryItem) => void>();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.mocked(api.wikiLibrary).mockResolvedValue(structuredClone(fixture));
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [] });
});
afterEach(() => { act(() => renderer?.unmount()); client.clear(); vi.useRealTimers(); });

it('shows one muted line when no level has any page yet', async () => {
  vi.mocked(api.wikiLibrary).mockResolvedValue({ ...structuredClone(fixture), libraries: [], items: [] });
  await mount();
  expect(api.wikiLibrary).toHaveBeenCalledWith('one', expect.any(AbortSignal));
  expect(api.knowledgeFeed).toHaveBeenCalledWith(20, expect.any(AbortSignal));
  expect(content(renderer.root)).toContain('No knowledge pages yet');
  expect(renderer.root.findAllByType('button')).toHaveLength(1);
});

it('still lists shared knowledge without a project and stays quiet when the request fails', async () => {
  await mount({ sid: null });
  expect(api.wikiLibrary).toHaveBeenCalledWith(null, expect.any(AbortSignal));
  expect(renderer.root.findAllByType('section')).toHaveLength(1);
  expect(renderer.root.findByProps({ 'aria-label': '6 pages' })).toBeDefined();
  vi.mocked(api.wikiLibrary).mockRejectedValue(new Error('Offline'));
  await act(async () => renderer.update(tree({ sid: 'two' }))); await settle();
  expect(content(renderer.root)).toContain('Knowledge base unavailable');
  expect(content(renderer.root)).not.toContain('No knowledge pages yet');
});

it('renders nothing and asks the host for nothing when it has nowhere to open', async () => {
  await mount({ withOpen: false });
  expect(api.wikiLibrary).not.toHaveBeenCalled();
  expect(api.knowledgeFeed).not.toHaveBeenCalled();
  expect(renderer.root.findAllByType('section')).toHaveLength(0);
});

it('lists the five newest pages across levels with the total count and each level in the footer', async () => {
  await mount();
  const text = content(renderer.root);
  expect(text).toContain('Knowledge base');
  expect(renderer.root.findByProps({ 'aria-label': '6 pages' })).toBeDefined();
  expect(rows()).toEqual(['NewestProject · rel:60', 'Fifthresearch · rel:50', 'FourthProject · rel:40', 'ThirdGlobal · rel:30', 'SecondProject · rel:20']);
  expect(text).not.toContain('Oldest');
  expect(text).not.toContain('Just learned');
  expect(text).not.toContain('No knowledge pages yet');
});

it('leads with what Argus just learned, keeps five rows, and opens that page', async () => {
  const ts = now - 90;
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [
    learned(ts, items[1]),
    learned(now - 600, items[3], { kind: 'promoted' }),
    { ...learned(now - 20, items[0]), kind: 'recalled', role: 'reviewer' },
  ] });
  await mount();
  expect(rows()).toEqual([`Just learned: FifthLearned · research · rel:${ts}`, 'NewestProject · rel:60', 'FourthProject · rel:40', 'ThirdGlobal · rel:30', 'SecondProject · rel:20']);
  expect(renderer.root.findByProps({ 'data-just-learned': true })).toBeDefined();
  act(() => button('Just learned').props.onClick());
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ scope: 'vertical', vertical: 'research', path: 'pages/fifth.md' }));
});

it('falls back to a promoted page, and to opening the browser when the page is not in the catalog', async () => {
  const ts = now - 7200;
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [
    learned(ts, items[3], { kind: 'promoted', title: 'Third' }),
    learned(now - 9000, items[5], { title: 'Elsewhere', path: 'pages/elsewhere.md', source_project: 'other' }),
  ] });
  await mount();
  expect(rows()[0]).toBe(`Just learned: ThirdPromoted · rel:${ts}`);
  expect(rows()).toHaveLength(5);
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [learned(now - 9000, items[5], { title: 'Elsewhere', path: 'pages/elsewhere.md', source_project: 'other' })] });
  act(() => renderer.unmount()); client.clear();
  await mount();
  expect(rows()[0]).toBe(`Just learned: ElsewhereLearned · rel:${now - 9000}`);
  act(() => button('Just learned').props.onClick());
  expect(onOpen).toHaveBeenCalledTimes(1);
  expect(onOpen.mock.calls[0]).toEqual([]);
});

it('drops the just-learned row once the page is older than a day', async () => {
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [learned(now - 90_000, items[1])] });
  await mount();
  expect(content(renderer.root)).not.toContain('Just learned');
  expect(rows()).toHaveLength(5);
});

it('opens the browser from the title and hands a page over when one is clicked', async () => {
  await mount();
  act(() => button('Knowledge base').props.onClick());
  expect(onOpen).toHaveBeenCalledTimes(1);
  expect(onOpen.mock.calls[0]).toEqual([]);
  act(() => button('Fifth').props.onClick());
  expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'vertical', vertical: 'research', path: 'pages/fifth.md' }));
  expect(renderer.root.findAllByProps({ role: 'dialog' })).toHaveLength(0);
  expect(api.wikiDocument).not.toHaveBeenCalled();
});

it('shows only an icon with a badge in the slim sidebar and opens the browser from it', async () => {
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [learned(now - 90, items[1])] });
  await mount({ compact: true });
  expect(api.knowledgeFeed).not.toHaveBeenCalled();
  expect(renderer.root.findAllByType('button')).toHaveLength(1);
  expect(content(renderer.root)).toBe('6');
  expect(content(renderer.root)).not.toContain('Newest');
  act(() => renderer.root.findByType('button').props.onClick());
  expect(onOpen).toHaveBeenCalledWith();
});

it('picks up a page the project just wrote without remounting', async () => {
  await mount();
  const next: WikiCatalog = { ...fixture, items: [page('Just written', 70), ...fixture.items] };
  vi.mocked(api.wikiLibrary).mockResolvedValue(next);
  await act(async () => { await client.invalidateQueries({ queryKey: wikiQueryKey('one') }); }); await settle();
  expect(content(renderer.root.findAllByType('button')[1])).toContain('Just written');
  expect(renderer.root.findByProps({ 'aria-label': '7 pages' })).toBeDefined();
});
