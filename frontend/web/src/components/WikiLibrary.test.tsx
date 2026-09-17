import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type WikiCatalog, type WikiLibraryItem, type WikiScope } from '../api';
import { recentWikiPages, WikiLibrary } from './WikiLibrary';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'en-US', t: (key: string) => key }) }));
vi.mock('../api', () => ({ api: { wikiLibrary: vi.fn(), wikiDocument: vi.fn() } }));
vi.mock('../lib/format', () => ({ formatRelativeTime: (ts: number) => `rel:${ts}` }));
vi.mock('./MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <div data-markdown>{children}</div> }));
vi.mock('./Modal', () => ({
  ModalHeader: ({ title, sub }: { title: string; sub?: string }) => <div data-modal-header>{title}{sub}</div>,
}));

const roots: Record<string, string> = { project: '.autors/proj/wiki', 'vertical/research': '/shared/wiki/_shared_verticals/research', 'vertical/math': '/shared/wiki/_shared_verticals/math', global: '/shared/wiki/_global' };
const page = (name: string, updated_at: number, scope: WikiScope, vertical = ''): WikiLibraryItem => ({
  scope, vertical, root: roots[scope === 'vertical' ? `vertical/${vertical}` : scope],
  path: `pages/${name.toLowerCase().replace(/\s+/g, '-')}.md`, title: name, description: `About ${name}`, updated_at,
});
const items = [
  page('Fused epilogue', 60, 'project', 'research'),
  page('Baseline table', 50, 'vertical', 'research'),
  page('Lean tactics', 45, 'vertical', 'math'),
  page('Cluster etiquette', 40, 'global'),
  page('Dataset notes', 30, 'project', 'research'),
];
const library = (scope: WikiScope, vertical: string, index_markdown: string) => ({
  scope, vertical, root: roots[scope === 'vertical' ? `vertical/${vertical}` : scope], index_markdown,
  pages: items.filter(item => item.scope === scope && item.vertical === vertical).map(({ path, title, description, updated_at }) => ({ path, title, description, updated_at })),
});
const fixture: WikiCatalog = {
  scopes: ['global', 'vertical', 'project'],
  libraries: [library('project', 'research', '# Project index\n'), library('vertical', 'research', '# Research knowledge\n'), library('vertical', 'math', ''), library('global', '', '# Global index\n')],
  items: [...items],
  verticals: ['math', 'research'],
  active_vertical: 'research',
  errors: [],
};
const frontMatter = '---\ntitle: Baseline table\ndescription: About Baseline table\naudience: vertical\n---\n';
let client: QueryClient;
let renderer: ReactTestRenderer;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const buttons = (root: ReactTestInstance = renderer.root) => root.findAllByType('button');
const button = (name: string) => buttons().find(node => content(node).startsWith(name))!;
const tab = (name: string) => renderer.root.findByProps({ 'aria-label': 'Knowledge levels' }).findAllByType('button').find(node => content(node).startsWith(name))!;
const list = () => renderer.root.findByProps({ 'aria-label': 'Knowledge pages' });
const listTitles = () => list().findAllByType('button').filter(node => node.props['aria-pressed'] !== undefined && !content(node).startsWith('INDEX.md')).map(node => content(node.findAllByType('span')[0]));
const article = () => renderer.root.findByProps({ 'aria-label': 'Knowledge page' });
const markdown = () => content(renderer.root.findByProps({ 'data-markdown': true }));
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
async function mount(props: { sid?: string | null; initialSelection?: WikiLibraryItem | null; initialScope?: WikiScope | 'recent' } = {}) {
  await act(async () => {
    renderer = create(<QueryClientProvider client={client}>
      <WikiLibrary sid={props.sid === undefined ? 'one' : props.sid} projectName="Proj" initialSelection={props.initialSelection} initialScope={props.initialScope} />
    </QueryClientProvider>);
  });
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.mocked(api.wikiLibrary).mockResolvedValue(structuredClone(fixture));
  vi.mocked(api.wikiDocument).mockImplementation(async (_sid, scope, vertical, path) => ({
    scope, vertical, path, title: `Title of ${path}`, description: `Described ${path}`,
    content: `Body of ${path}. `.repeat(40), markdown: frontMatter + `Body of ${path}. `.repeat(40), truncated: false, updated_at: 50,
  }));
});
afterEach(() => { act(() => renderer?.unmount()); client.clear(); vi.useRealTimers(); });

it('orders every page newest first regardless of level', () => {
  expect(recentWikiPages([items[4], items[0], items[2]]).map(item => item.title)).toEqual(['Fused epilogue', 'Lean tactics', 'Dataset notes']);
});

it('opens on recent updates with every level counted in the tabs', async () => {
  await mount();
  expect(api.wikiLibrary).toHaveBeenCalledWith('one', expect.any(AbortSignal));
  expect(content(renderer.root.findByProps({ 'data-modal-header': true }))).toContain('Knowledge base');
  expect(listTitles()).toEqual(['Fused epilogue', 'Baseline table', 'Lean tactics', 'Cluster etiquette', 'Dataset notes']);
  expect(content(tab('Global'))).toBe('Global1');
  expect(content(tab('Vertical'))).toBe('Vertical2');
  expect(content(tab('Project'))).toBe('Project2');
  expect(content(list())).toContain('Vertical · research · rel:50');
  expect(content(list())).toContain('Project · Proj · rel:60');
  expect(content(article())).toContain('Select a page to read it in full.');
});

it('filters the list by level when a tab is picked', async () => {
  await mount();
  act(() => tab('Project').props.onClick());
  expect(listTitles()).toEqual(['Fused epilogue', 'Dataset notes']);
  act(() => tab('Global').props.onClick());
  expect(listTitles()).toEqual(['Cluster etiquette']);
  act(() => tab('Vertical').props.onClick());
  expect(listTitles()).toEqual(['Baseline table', 'Lean tactics']);
});

it('narrows the vertical tab to one vertical and marks the project\'s own', async () => {
  await mount();
  act(() => tab('Vertical').props.onClick());
  const select = renderer.root.findByProps({ 'aria-label': 'Vertical' });
  expect(select.findAllByType('option').map(content)).toEqual(['All verticals', 'math', 'research · this project']);
  act(() => select.props.onChange({ target: { value: 'math' } }));
  expect(listTitles()).toEqual(['Lean tactics']);
  act(() => select.props.onChange({ target: { value: 'research' } }));
  expect(listTitles()).toEqual(['Baseline table']);
});

it('searches titles, descriptions and paths and offers to clear an empty result', async () => {
  await mount();
  const input = renderer.root.findByProps({ 'aria-label': 'Search pages' });
  act(() => input.props.onChange({ target: { value: 'dataset' } }));
  expect(listTitles()).toEqual(['Dataset notes']);
  act(() => input.props.onChange({ target: { value: 'nothing here' } }));
  expect(listTitles()).toEqual([]);
  expect(content(list())).toContain('No pages match the current filters.');
  act(() => button('Clear filters').props.onClick());
  expect(listTitles()).toHaveLength(5);
});

it('fetches a page by level, vertical and path and shows the body without its front matter', async () => {
  await mount();
  act(() => list().findAllByType('button').find(node => content(node).startsWith('Baseline table'))!.props.onClick()); await settle();
  expect(api.wikiDocument).toHaveBeenCalledWith('one', 'vertical', 'research', 'pages/baseline-table.md', expect.any(AbortSignal));
  const text = content(article());
  expect(text).toContain('Title of pages/baseline-table.md');
  expect(text).toContain('Described pages/baseline-table.md');
  expect(text).toContain('Vertical · research');
  expect(text).toContain('pages/baseline-table.md');
  expect(text).toContain('Updated ');
  expect(markdown()).not.toContain('audience: vertical');
  expect(markdown()).not.toContain('---');
  expect(markdown().length).toBeGreaterThan(500);
  expect(text).not.toContain('Page shortened for display.');
});

it('opens straight on the page the sidebar handed over', async () => {
  await mount({ initialSelection: items[3] }); await settle();
  expect(api.wikiDocument).toHaveBeenCalledWith('one', 'global', '', 'pages/cluster-etiquette.md', expect.any(AbortSignal));
  expect(content(article())).toContain('Title of pages/cluster-etiquette.md');
  act(() => button('← Back to pages').props.onClick());
  expect(api.wikiDocument).toHaveBeenCalledTimes(1);
  expect(content(article())).toContain('Select a page to read it in full.');
});

it('shows each library\'s INDEX.md in the reading pane', async () => {
  await mount();
  const indexes = renderer.root.findByProps({ 'aria-label': 'Index files' }).findAllByType('button').map(content);
  expect(indexes).toEqual(['INDEX.md · Project · Proj', 'INDEX.md · Vertical · research', 'INDEX.md · Global']);
  act(() => button('INDEX.md · Vertical').props.onClick());
  expect(markdown()).toBe('# Research knowledge\n');
  expect(content(article())).toContain('/shared/wiki/_shared_verticals/research/INDEX.md');
  expect(api.wikiDocument).not.toHaveBeenCalled();
  act(() => tab('Global').props.onClick());
  expect(renderer.root.findByProps({ 'aria-label': 'Index files' }).findAllByType('button').map(content)).toEqual(['INDEX.md · Global']);
  expect(content(article())).toContain('Select a page to read it in full.');
});

it('explains an empty project tab without a project and empty levels in plain words', async () => {
  vi.mocked(api.wikiLibrary).mockResolvedValue({ ...structuredClone(fixture), libraries: [], items: [], verticals: [], active_vertical: '' });
  await mount({ sid: null });
  expect(api.wikiLibrary).toHaveBeenCalledWith(null, expect.any(AbortSignal));
  expect(content(list())).toContain('No knowledge pages yet.');
  act(() => tab('Project').props.onClick());
  expect(content(list())).toContain('Select a project to see its knowledge base.');
  act(() => tab('Vertical').props.onClick());
  expect(content(list())).toContain('No pages for this vertical yet.');
  act(() => tab('Global').props.onClick());
  expect(content(list())).toContain('No global pages yet.');
  expect(renderer.root.findAllByProps({ 'aria-label': 'Index files' })).toHaveLength(0);
});

it('reports a failed load with a retry and a page that would not open', async () => {
  vi.mocked(api.wikiLibrary).mockRejectedValue(new Error('Offline'));
  await mount();
  expect(content(list())).toContain('Could not load the knowledge base.');
  vi.mocked(api.wikiLibrary).mockResolvedValue(structuredClone(fixture));
  act(() => button('Retry').props.onClick()); await settle();
  expect(listTitles()).toHaveLength(5);
  vi.mocked(api.wikiDocument).mockRejectedValue(new Error('Page missing'));
  act(() => list().findAllByType('button').find(node => content(node).startsWith('Dataset notes'))!.props.onClick()); await settle();
  expect(content(article())).toContain('Page missing');
});
