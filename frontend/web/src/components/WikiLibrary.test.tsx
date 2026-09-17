import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type KnowledgeEvent, type WikiCatalog, type WikiLibraryItem, type WikiScope } from '../api';
import { groupKnowledgeEvents, latestLearned, resolveKnowledgeItem } from './KnowledgeFeed';
import { recentWikiPages, WikiLibrary, type WikiTab } from './WikiLibrary';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'en-US', t: (key: string) => key }) }));
vi.mock('../api', () => ({ api: { wikiLibrary: vi.fn(), wikiDocument: vi.fn(), knowledgeFeed: vi.fn() } }));
vi.mock('../lib/format', () => ({ formatRelativeTime: (ts: number) => `rel:${ts}` }));
vi.mock('./MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <div data-markdown>{children}</div> }));
vi.mock('./Modal', () => ({
  ModalHeader: ({ title, sub }: { title: string; sub?: string }) => <div data-modal-header>{title}{sub}</div>,
}));

const roots: Record<string, string> = { project: '.autors/proj/wiki', 'vertical/research': '/shared/wiki/_shared_verticals/research', 'vertical/math': '/shared/wiki/_shared_verticals/math', global: '/shared/wiki/_global' };
const page = (name: string, updated_at: number, scope: WikiScope, vertical = '', extra: Partial<WikiLibraryItem> = {}): WikiLibraryItem => ({
  scope, vertical, root: roots[scope === 'vertical' ? `vertical/${vertical}` : scope],
  path: `pages/${name.toLowerCase().replace(/\s+/g, '-')}.md`, title: name, description: `About ${name}`, updated_at,
  kind: 'page', source: '', created: '', reuse_count: 0, ...extra,
});
const items = [
  page('Fused epilogue', 60, 'project', 'research', { kind: 'fact', source: 's-one/m1', created: '2026-09-17', reuse_count: 2 }),
  page('Baseline table', 50, 'vertical', 'research'),
  page('Lean tactics', 45, 'vertical', 'math', { kind: 'lesson', path: 'pages/lessons/20260917-lean-tactics.md', source: 's-two/m7', created: '2026-09-17' }),
  page('Cluster etiquette', 40, 'global', '', { kind: 'survey', source: 'chat/s-one' }),
  page('Dataset notes', 30, 'project', 'research', { kind: 'lesson' }),
];
const principles = '# Principles\n\n1. Check the baseline before tuning — evidence: [a](pages/lessons/a.md), [b](pages/lessons/b.md)\n\n## History\n- 2026-09-17 compiled from 3 lessons\n';
const library = (scope: WikiScope, vertical: string, index_markdown: string, withPrinciples: string | null = null) => ({
  scope, vertical, root: roots[scope === 'vertical' ? `vertical/${vertical}` : scope], index_markdown, principles: withPrinciples,
  pages: items.filter(item => item.scope === scope && item.vertical === vertical).map(({ path, title, description, updated_at }) => ({ path, title, description, updated_at })),
});
const fixture: WikiCatalog = {
  scopes: ['global', 'vertical', 'project'],
  libraries: [library('project', 'research', '# Project index\n'), library('vertical', 'research', '# Research knowledge\n', principles), library('vertical', 'math', ''), library('global', '', '# Global index\n')],
  items: [...items],
  verticals: ['math', 'research'],
  active_vertical: 'research',
  errors: [],
};
const now = Math.floor(Date.now() / 1000);
const event = (kind: KnowledgeEvent['kind'], ts: number, item: WikiLibraryItem, extra: Partial<KnowledgeEvent> = {}): KnowledgeEvent => ({
  ts, kind, scope: item.scope, vertical: item.vertical, path: item.path, title: item.title,
  source_project: item.scope === 'project' ? 'one' : 's-two', mission_id: 'm1', role: kind === 'learned' ? 'host' : 'reviewer', page_kind: item.kind, note: '', ...extra,
});
const events: KnowledgeEvent[] = [
  event('learned', now - 30, items[2], { note: 'Search the Lean library before proving by hand.' }),
  event('recalled', now - 100, items[1]),
  event('recalled', now - 120, items[3]),
  event('recalled', now - 150, items[0]),
  event('recalled', now - 170, items[1], { role: 'engineer' }),
  event('recalled', now - 400, items[4]),
  event('promoted', now - 3600, items[1], { role: 'host' }),
  event('learned', now - 5000, items[4], { source_project: 'other', title: 'Vanished page', path: 'pages/vanished.md' }),
];
const frontMatter = '---\ntitle: Baseline table\ndescription: About Baseline table\naudience: vertical\n---\n';
let client: QueryClient;
let renderer: ReactTestRenderer;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const buttons = (root: ReactTestInstance = renderer.root) => root.findAllByType('button');
const button = (name: string) => buttons().find(node => content(node).startsWith(name))!;
const tab = (name: string) => renderer.root.findByProps({ 'aria-label': 'Knowledge levels' }).findAllByType('button').find(node => content(node).startsWith(name))!;
const list = () => renderer.root.findByProps({ 'aria-label': 'Knowledge pages' });
const listTitles = () => list().findAllByType('button').filter(node => node.props['aria-pressed'] !== undefined && !content(node).startsWith('INDEX.md')).map(node => content(node.find(child => child.props['data-wiki-title'] === true)));
const feedRows = () => renderer.root.findAll(node => node.type === 'li' && Boolean(node.props['data-feed-row']));
const article = () => renderer.root.findByProps({ 'aria-label': 'Knowledge page' });
const markdown = () => content(renderer.root.findByProps({ 'data-markdown': true }));
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
async function mount(props: { sid?: string | null; initialSelection?: WikiLibraryItem | null; initialScope?: WikiTab } = {}) {
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
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: structuredClone(events) });
  vi.mocked(api.wikiDocument).mockImplementation(async (_sid, scope, vertical, path) => ({
    scope, vertical, path, title: `Title of ${path}`, description: `Described ${path}`,
    content: `Body of ${path}. `.repeat(40), markdown: frontMatter + `Body of ${path}. `.repeat(40), truncated: false, updated_at: 50,
  }));
});
afterEach(() => { act(() => renderer?.unmount()); client.clear(); vi.useRealTimers(); });

it('orders every page newest first regardless of level', () => {
  expect(recentWikiPages([items[4], items[0], items[2]]).map(item => item.title)).toEqual(['Fused epilogue', 'Lean tactics', 'Dataset notes']);
});

it('folds consecutive recalls by one role within a minute and leaves everything else as its own row', () => {
  const rows = groupKnowledgeEvents(events);
  expect(rows.map(row => row.kind === 'single' ? `${row.event.kind}:${row.event.title}` : `recalls:${row.role}:${row.events.length}`))
    .toEqual(['learned:Lean tactics', 'recalls:reviewer:3', 'recalls:engineer:1', 'recalls:reviewer:1', 'promoted:Baseline table', 'learned:Vanished page']);
  // Order on the wire does not matter; the newest line anchors each group.
  expect(groupKnowledgeEvents([...events].reverse())).toEqual(rows);
});

it('resolves a journal line to a catalog page only when level, vertical, path and project agree', () => {
  expect(resolveKnowledgeItem(events[1], items, 'one')).toBe(items[1]);
  expect(resolveKnowledgeItem(events[3], items, 'one')).toBe(items[0]);
  expect(resolveKnowledgeItem({ ...events[3], source_project: 'other' }, items, 'one')).toBeNull();
  expect(resolveKnowledgeItem({ ...events[1], vertical: 'math' }, items, 'one')).toBeNull();
  expect(latestLearned(events, now)?.title).toBe('Lean tactics');
  expect(latestLearned(events.filter(item => item.kind !== 'learned'), now)?.kind).toBe('promoted');
  expect(latestLearned(events, now + 2 * 86_400)).toBeNull();
});

it('opens on the learning feed, newest first, with recalls by one role folded into a single row', async () => {
  await mount();
  expect(api.knowledgeFeed).toHaveBeenCalledWith(50, expect.any(AbortSignal));
  expect(tab('Learning feed').props['aria-pressed']).toBe(true);
  expect(content(article())).toContain('Click a title in the feed to read its page here.');
  const rows = feedRows();
  expect(rows.map(row => row.props['data-feed-row'])).toEqual(['learned', 'recalls', 'recalls', 'recalls', 'promoted', 'learned']);
  expect(content(rows[0])).toContain(`rel:${now - 30}`);
  expect(content(rows[0])).toContain('Learned');
  expect(content(rows[0])).toContain('Lean tactics');
  expect(content(rows[0])).toContain('Vertical · math · from s-two · host');
  expect(content(rows[0])).toContain('Search the Lean library before proving by hand.');
  expect(content(rows[1])).toContain('Recalled 3 existing pages');
  expect(content(rows[1])).toContain('reviewer');
  expect(rows[1].findAllByType('button').map(content)).toEqual(['Baseline table', 'Cluster etiquette', 'Fused epilogue']);
  expect(content(rows[2])).toContain('Baseline table');
  expect(content(rows[2])).toContain('engineer');
  expect(content(rows[2])).not.toContain('existing pages');
  expect(content(rows[4])).toContain('Promoted');
  // A line whose page is not in the catalog stays plain text.
  expect(content(rows[5])).toContain('Vanished page');
  expect(rows[5].findAllByType('button')).toHaveLength(0);
});

it('opens the page a feed line points at and keeps the feed on a ten-second refresh', async () => {
  await mount();
  // The browser environment drives the interval; here only the configured period can be observed.
  expect(client.getQueryCache().find({ queryKey: ['knowledge-feed', 50] })?.observers[0]?.options.refetchInterval).toBe(10_000);
  act(() => feedRows()[0].findByType('button').props.onClick()); await settle();
  expect(api.wikiDocument).toHaveBeenCalledWith('one', 'vertical', 'math', 'pages/lessons/20260917-lean-tactics.md', expect.any(AbortSignal));
  expect(content(article())).toContain('Title of pages/lessons/20260917-lean-tactics.md');
  expect(content(article())).toContain('Written by Argus in reflection');
  expect(feedRows()[0].findByType('button').props['aria-pressed']).toBe(true);
  act(() => feedRows()[1].findAllByType('button')[1].props.onClick()); await settle();
  expect(api.wikiDocument).toHaveBeenLastCalledWith('one', 'global', '', 'pages/cluster-etiquette.md', expect.any(AbortSignal));
  expect(api.knowledgeFeed).toHaveBeenCalledTimes(1);
});

it('searches the feed and explains an empty one in plain words', async () => {
  await mount();
  const input = renderer.root.findByProps({ 'aria-label': 'Search pages' });
  act(() => input.props.onChange({ target: { value: 'engineer' } }));
  expect(feedRows()).toHaveLength(1);
  expect(content(feedRows()[0])).toContain('engineer');
  act(() => input.props.onChange({ target: { value: 'nothing here' } }));
  expect(content(list())).toContain('No feed lines match the search.');
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [] });
  act(() => renderer.unmount()); client.clear();
  await mount();
  expect(content(list())).toContain('Nothing learned yet.');
  expect(content(list())).toContain('writes a lesson after a mission');
});

it('lists every page newest first with a kind badge and a reuse pill where the host counted reuse', async () => {
  await mount({ initialScope: 'recent' });
  expect(api.wikiLibrary).toHaveBeenCalledWith('one', expect.any(AbortSignal));
  expect(content(renderer.root.findByProps({ 'data-modal-header': true }))).toContain('Knowledge base');
  expect(listTitles()).toEqual(['Fused epilogue', 'Baseline table', 'Lean tactics', 'Cluster etiquette', 'Dataset notes']);
  expect(list().findAll(node => Boolean(node.props['data-wiki-kind'])).map(content)).toEqual(['Fact', 'Page', 'Lesson', 'Survey', 'Lesson']);
  const pills = list().findAll(node => node.props['data-wiki-reuse'] !== undefined);
  expect(pills.map(content)).toEqual(['Reused 2 times']);
  expect(content(tab('Recent'))).toBe('Recent');
  expect(content(tab('Lessons'))).toBe('Lessons2');
  expect(content(tab('Principles'))).toBe('Principles1');
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

it('shows lessons from every level on their own tab, newest first, without index files', async () => {
  await mount();
  act(() => tab('Lessons').props.onClick());
  expect(listTitles()).toEqual(['Lean tactics', 'Dataset notes']);
  expect(renderer.root.findAllByProps({ 'aria-label': 'Index files' })).toHaveLength(0);
  expect(content(list())).toContain('Vertical · math');
  expect(content(list())).toContain('Project · Proj');
  vi.mocked(api.wikiLibrary).mockResolvedValue({ ...structuredClone(fixture), items: items.filter(item => item.kind !== 'lesson') });
  act(() => renderer.unmount()); client.clear();
  await mount({ initialScope: 'lessons' });
  expect(content(list())).toContain('No lessons yet.');
});

it('renders each vertical\'s principles on their own tab and says what is missing when none exist', async () => {
  await mount();
  act(() => tab('Principles').props.onClick());
  expect(listTitles()).toEqual(['research']);
  expect(content(list())).toContain('Vertical · research · principles.md');
  expect(renderer.root.findAllByProps({ 'aria-label': 'Search pages' })).toHaveLength(0);
  expect(markdown()).toBe(principles);
  expect(content(article())).toContain('/shared/wiki/_shared_verticals/research/principles.md');
  act(() => list().findAllByType('button')[0].props.onClick());
  expect(markdown()).toBe(principles);
  expect(list().findAllByType('button')[0].props['aria-pressed']).toBe(true);
  expect(api.wikiDocument).not.toHaveBeenCalled();
  vi.mocked(api.wikiLibrary).mockResolvedValue({ ...structuredClone(fixture), libraries: fixture.libraries.map(entry => ({ ...entry, principles: null })) });
  act(() => renderer.unmount()); client.clear();
  await mount({ initialScope: 'principles' });
  expect(content(list())).toContain('No principles yet: at least 3 lessons are needed.');
  expect(content(article())).toContain('No principles yet: at least 3 lessons are needed.');
  expect(content(tab('Principles'))).toBe('Principles0');
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
  await mount({ initialScope: 'recent' });
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
  await mount({ initialScope: 'recent' });
  act(() => list().findAllByType('button').find(node => content(node).includes('Baseline table'))!.props.onClick()); await settle();
  expect(api.wikiDocument).toHaveBeenCalledWith('one', 'vertical', 'research', 'pages/baseline-table.md', expect.any(AbortSignal));
  const text = content(article());
  expect(text).toContain('Title of pages/baseline-table.md');
  expect(text).toContain('Described pages/baseline-table.md');
  expect(text).toContain('Vertical · research');
  expect(text).toContain('pages/baseline-table.md');
  expect(text).toContain('Updated ');
  expect(text).toContain('Not recalled yet');
  expect(text).not.toContain('Source ');
  expect(markdown()).not.toContain('audience: vertical');
  expect(markdown()).not.toContain('---');
  expect(markdown().length).toBeGreaterThan(500);
  expect(text).not.toContain('Page shortened for display.');
});

it('heads a page with its kind, origin, date and reuse count', async () => {
  await mount({ initialScope: 'recent' });
  act(() => list().findAllByType('button').find(node => content(node).includes('Fused epilogue'))!.props.onClick()); await settle();
  let text = content(article());
  expect(text).toContain('Fact');
  expect(text).toContain('Source s-one/m1');
  expect(text).toContain('Written 2026-09-17');
  expect(text).toContain('Reused 2 times');
  expect(text).not.toContain('Written by Argus in reflection');
  expect(text).not.toContain('Distilled after an answer');
  act(() => list().findAllByType('button').find(node => content(node).includes('Cluster etiquette'))!.props.onClick()); await settle();
  text = content(article());
  expect(text).toContain('Survey');
  expect(text).toContain('Source chat/s-one');
  expect(text).toContain('Distilled after an answer');
  act(() => list().findAllByType('button').find(node => content(node).includes('Dataset notes'))!.props.onClick()); await settle();
  text = content(article());
  expect(text).toContain('Lesson');
  expect(text).toContain('Written by Argus in reflection');
  expect(text).toContain('Not recalled yet');
});

it('opens straight on the page the sidebar handed over, beside the recent list', async () => {
  await mount({ initialSelection: items[3] }); await settle();
  expect(api.wikiDocument).toHaveBeenCalledWith('one', 'global', '', 'pages/cluster-etiquette.md', expect.any(AbortSignal));
  expect(content(article())).toContain('Title of pages/cluster-etiquette.md');
  expect(tab('Recent').props['aria-pressed']).toBe(true);
  act(() => button('← Back to pages').props.onClick());
  expect(api.wikiDocument).toHaveBeenCalledTimes(1);
  expect(content(article())).toContain('Select a page to read it in full.');
});

it('shows each library\'s INDEX.md in the reading pane', async () => {
  await mount({ initialScope: 'recent' });
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
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [] });
  await mount({ sid: null });
  expect(api.wikiLibrary).toHaveBeenCalledWith(null, expect.any(AbortSignal));
  expect(content(list())).toContain('Nothing learned yet.');
  act(() => tab('Recent').props.onClick());
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
  vi.mocked(api.knowledgeFeed).mockRejectedValue(new Error('Offline'));
  await mount();
  expect(content(list())).toContain('Could not load the learning feed.');
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: structuredClone(events) });
  act(() => button('Retry').props.onClick()); await settle();
  expect(feedRows().length).toBeGreaterThan(0);
  act(() => tab('Recent').props.onClick());
  expect(content(list())).toContain('Could not load the knowledge base.');
  vi.mocked(api.wikiLibrary).mockResolvedValue(structuredClone(fixture));
  act(() => button('Retry').props.onClick()); await settle();
  expect(listTitles()).toHaveLength(5);
  vi.mocked(api.wikiDocument).mockRejectedValue(new Error('Page missing'));
  act(() => list().findAllByType('button').find(node => content(node).includes('Dataset notes'))!.props.onClick()); await settle();
  expect(content(article())).toContain('Page missing');
});
