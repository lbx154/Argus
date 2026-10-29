import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type SkillLibraryItem, type WikiLibraryItem } from '../api';
import { LearningLibrary, learningEntries } from '../components/LearningLibrary';
const state = vi.hoisted(() => ({ locale: 'zh-CN' }));
vi.mock('../i18n', async importOriginal => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return { ...actual, useI18n: () => ({ locale: state.locale, t: (key: string) => actual.translate(key, {}, state.locale as 'zh-CN' | 'en') }) };
});
vi.mock('../api', () => ({ api: { skillLibrary: vi.fn(), skillDocument: vi.fn(), wikiLibrary: vi.fn(), wikiDocument: vi.fn(), knowledgeFeed: vi.fn(), learningStatus: vi.fn() } }));
vi.mock('../components/MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <pre data-original>{children}</pre> }));
const builtin: SkillLibraryItem = { scope: 'global', vertical: '', source: 'bundled', library: 'global:bundled',
  path: 'engineer/presentation-master.md', name: 'Editable Presentations with PPT Master', description: 'Original English description',
  role: 'engineer', updated_at: null, is_default: true };
const custom = { ...builtin, library: 'project:learned', source: 'project', scope: 'project', is_default: false,
  name: 'Operator original title', description: 'Do not rewrite this description', updated_at: 2 } as SkillLibraryItem;
const wiki: WikiLibraryItem = { scope: 'private', vertical: '', root: '/synthetic/private-notes', path: 'notes/example.md',
  title: 'Original personal note', description: 'Original evidence summary', kind: 'note', source: 'synthetic conversation',
  created: '2026-09-18', updated_at: 3 };
let client: QueryClient, view: ReactTestRenderer | undefined;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
const button = (name: string) => view!.root.findAllByType('button').find(node => content(node).startsWith(name))!;
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); state.locale = 'zh-CN';
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.mocked(api.skillLibrary).mockResolvedValue({ scopes: ['global', 'vertical', 'project'], items: [builtin, custom], verticals: [], active_vertical: '', errors: [] });
  vi.mocked(api.skillDocument).mockImplementation(async (_sid, _library, path) => ({ ...builtin, path, content: '# ORIGINAL\n\nExact original text.\n', markdown: '' }));
  vi.mocked(api.wikiLibrary).mockResolvedValue({ scopes: ['private', 'global', 'vertical', 'project'], items: [wiki], verticals: [], active_vertical: '', errors: [],
    libraries: [{ scope: 'private', vertical: '', root: '/synthetic/private-notes', pages: [], index_markdown: '# ORIGINAL INDEX', profile: 'Original profile stays intact' }] });
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [] });
  vi.mocked(api.learningStatus).mockResolvedValue({ jobs: [], pending: 0, revision: 0 });
  vi.mocked(api.wikiDocument).mockResolvedValue({ ...wiki, content: '# ORIGINAL KNOWLEDGE\n\nNo automatic translation.\n', markdown: '', truncated: false });
});

it('unifies navigation into four categories while keeping private records, indexes and original scope', async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  const nav = view!.root.findByType('nav');
  expect(nav.findAllByType('button').map(content)).toEqual(['学习动态', '全局项目', '垂直领域', '项目']);
  expect(nav.findAllByType('button').map(node => node.props['aria-pressed'])).toEqual([false, true, false, false]);
  expect(content(view!.root)).toContain('个人记录');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(wiki.title))!.props.onClick()); await settle();
  expect(api.wikiDocument).toHaveBeenCalledWith('s-A', 'private', '', wiki.path, expect.any(AbortSignal));
  expect(content(view!.root)).toContain('No automatic translation.');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes('个人概况（原私有范围）'))!.props.onClick()); await settle();
  expect(content(view!.root)).toContain('Original profile stays intact');
  act(() => view!.root.findAllByType('button').find(node => content(node).startsWith('索引'))!.props.onClick()); await settle();
  expect(content(view!.root)).toContain('# ORIGINAL INDEX');
});

it('reads skills in the unified browser and resets selection on a project switch', async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes('可编辑演示文稿'))!.props.onClick()); await settle();
  expect(api.skillDocument).toHaveBeenCalledWith('s-A', builtin.library, builtin.path, expect.any(AbortSignal));
  expect(content(view!.root.findByProps({ 'data-skill-reading-guide': true }))).toContain('任务用得上时会自动参考');
  expect(content(view!.root.findByProps({ 'data-skill-reading-guide': true }))).toContain('前置条件和软件许可以原文为准。');
  await act(async () => view!.update(<QueryClientProvider client={client}><LearningLibrary sid="s-B" initialScope="global"
    initialSelection={{ kind: 'skill', item: builtin }} /></QueryClientProvider>)); await settle();
  expect(view!.root.findAllByProps({ 'data-skill-reading-guide': true })).toHaveLength(0);
  expect(api.skillDocument).toHaveBeenCalledTimes(1);
});

it('adds only an explicitly selected, loaded document with its original scope to the draft', async () => {
  const onUse = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" onUse={onUse} /></QueryClientProvider>); }); await settle();
  expect(onUse).not.toHaveBeenCalled();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(wiki.title))!.props.onClick()); await settle();
  expect(button('用于当前对话').props.disabled).toBe(false);
  act(() => button('用于当前对话').props.onClick());
  expect(onUse).toHaveBeenCalledWith(wiki.title, '# ORIGINAL KNOWLEDGE\n\nNo automatic translation.\n', expect.stringContaining('个人记录'));
  expect(onUse.mock.calls[0][2]).toContain(wiki.path);
});

it('does not offer an unreadable document as usable context', async () => {
  vi.mocked(api.wikiDocument).mockRejectedValue(new Error('unavailable'));
  const onUse = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" onUse={onUse} /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(wiki.title))!.props.onClick()); await settle();
  expect(button('用于当前对话').props.disabled).toBe(true);
  expect(onUse).not.toHaveBeenCalled();
});

it('separates real learning records from file updates and reports source failures explicitly', async () => {
  vi.mocked(api.wikiLibrary).mockRejectedValue(new Error('synthetic failure'));
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="feed" /></QueryClientProvider>); }); await settle();
  expect(content(view!.root)).toContain('知识无法加载');
  expect(content(view!.root)).toContain(custom.name);
  expect(content(view!.root)).not.toContain('当前分类或筛选条件下没有资料');
  expect(content(view!.root)).not.toContain('可编辑演示文稿');
});

it.each([
  { locale: 'zh-CN', labels: ['学习动态', '全局项目', '垂直领域', '项目'], skillName: '可编辑演示文稿' },
  { locale: 'en', labels: ['Learning feed', 'Across projects', 'Vertical', 'Project'], skillName: 'Editable Presentations with PPT Master' },
])('restores the original category order and learning-feed default in $locale', async ({ locale, labels, skillName }) => {
  state.locale = locale;
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" /></QueryClientProvider>); }); await settle();
  const tabs = view!.root.findByType('nav').findAllByType('button');
  expect(tabs.map(content)).toEqual(labels);
  expect(tabs.map(node => node.props['aria-pressed'])).toEqual([true, false, false, false]);
  expect(api.knowledgeFeed).toHaveBeenCalled();
  expect(content(view!.root)).not.toContain(skillName);
  act(() => button(labels[1]).props.onClick()); await settle();
  expect(content(view!.root)).toContain(skillName);
  expect(view!.root.findByType('nav').findAllByType('button').map(node => node.props['aria-pressed'])).toEqual([false, true, false, false]);
});

it('assigns lessons, principles, profiles and indexes to their original scopes in the four-category browser', async () => {
  const wikiCatalog = await api.wikiLibrary(null);
  const skillCatalog = await api.skillLibrary(null);
  const catalog = { ...wikiCatalog, items: [{ ...wiki, scope: 'vertical' as const, vertical: 'research', kind: 'lesson' }],
    libraries: [...wikiCatalog.libraries, { scope: 'vertical' as const, vertical: 'research', root: '/synthetic/research',
      pages: [], index_markdown: '# index', principles: 'Keep the evidence' }] };
  const entries = learningEntries(catalog, skillCatalog, 'vertical', 'zh-CN');
  expect(entries.map(entry => entry.kind).sort()).toEqual(['index', 'knowledge', 'principles']);
  expect(entries.every(entry => entry.scope === 'vertical' && entry.vertical === 'research')).toBe(true);
  expect(learningEntries(catalog, skillCatalog, 'project', 'en').some(entry => entry.scope === 'private')).toBe(false);
});
afterEach(() => { if (view) act(() => view!.unmount()); view = undefined; client.clear(); vi.useRealTimers(); });
